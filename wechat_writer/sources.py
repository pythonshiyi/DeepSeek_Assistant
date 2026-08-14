# -*- coding: utf-8 -*-
"""素材采集：RSS 并发抓取 + 搜索引擎兜底 + 全文抓取，统一为 Item 结构。

任何单源失败跳过不中断整轮（RSS 源不可达是常态）。
"""
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger("wechat_writer.sources")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
SEARCH_TIMEOUT = 8
RSS_TIMEOUT = 10
FETCH_FULL_TIMEOUT = 10
FETCH_FULL_MAX_CHARS = 30000  # 全文上限（深度写作用，比摘要信息量大 75 倍）

# 主题相关性过滤的最小保留量：过滤后不足此数说明当日相关资讯少，
# 宁多勿缺——放行全部（避免把有价值的边缘内容误杀）
MIN_RELEVANT_KEEP = 5

# 英文纯字母关键词用词边界匹配（避免 "ai" 命中 "said" 等），中文用包含匹配
_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")

# 正文抓取的标签噪音（去 script/style/导航等）
_BLOCK_SKIP_RE = re.compile(
    r"<(script|style|nav|footer|header|aside|form)[^>]*>.*?</\1>", re.S | re.I
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_BING_RESULT_RE = re.compile(
    r'<li class="b_algo".*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>'
    r"(.*?)(?=<li class=\"b_algo\"|</ol>)",
    re.S,
)


@dataclass
class Item:
    title: str
    url: str
    summary: str = ""
    source: str = "未知来源"
    published: str = ""
    fetched: bool = False
    full_text: str = ""

    def display(self, max_summary=200):
        s = self.summary.replace("\n", " ")[:max_summary]
        return f"- [{self.title}]（{self.source}）{self.url}\n  {s}"


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def expand_rss(cfg):
    """展开信源列表：用户 rss 列表优先；否则按 enabled_groups 展开（None=全部组）。"""
    rss = cfg["sources"].get("rss")
    if isinstance(rss, list) and rss:
        return [str(u).strip() for u in rss if str(u).strip()]
    groups = cfg["sources"].get("rss_groups") or {}
    enabled = cfg["sources"].get("enabled_groups")
    if enabled is not None:
        groups = {g: u for g, u in groups.items() if g in set(enabled)}
    out = []
    for gname, urls in groups.items():
        if isinstance(urls, list):
            out.extend(str(u).strip() for u in urls if str(u).strip())
    return out


def _kw_hit(text, kw):
    """关键词命中：纯英文/数字词用词边界（防 'ai' 误中 'said'），其余包含匹配。"""
    text = str(text or "").lower()
    if re.fullmatch(r"[a-zA-Z0-9]+", kw):
        return re.search(rf"\b{re.escape(kw)}\b", text) is not None
    return kw in text


def filter_by_keywords(items, keywords):
    """主题相关性过滤：标题/摘要命中任一关键词保留；过滤后不足 MIN_RELEVANT_KEEP 放行全部。"""
    kws = [k for k in (keywords or []) if str(k).strip()]
    if not kws or not items:
        return items
    text_items = []
    for it in items:
        hay = f"{it.title} {it.summary}".lower()
        if any(_kw_hit(hay, k.lower()) for k in kws):
            text_items.append(it)
    if len(text_items) < MIN_RELEVANT_KEEP:
        return items  # 相关资讯稀少：宁多勿缺
    return text_items


def collect_rss(urls, since_hours=24, limit_per=10, timeout=RSS_TIMEOUT, use_blocked=False):
    """并发抓取多个 RSS 源，只保留 since_hours 内的条目（无时间字段的保留）。

    单源失败跳过；单源抓取有独立超时（内部线程 + join，feedparser 的
    urllib 请求本身无超时，慢源/DNS 挂起会卡死整轮采集——真实事故）。
    use_blocked=True 时：直连失败/超时的源自动经 fetch_blocked 代理通道
    重试（抓取 RSS 文本再解析），让被墙论坛（linux.do/hostloc 等）进入素材池。
    返回按发布时间倒序的 Item 列表。
    """
    urls = [u for u in (urls or []) if str(u).strip()]
    if not urls:
        return []
    try:
        import feedparser
    except ImportError:
        logger.warning("feedparser 未安装，RSS 采集不可用")
        return []
    import calendar
    from concurrent.futures import ThreadPoolExecutor

    cutoff = time.time() - since_hours * 3600 if since_hours and since_hours > 0 else None
    items = []

    def _parse_feed(url_or_text, is_text=False):
        """feedparser 解析 URL 或文本；失败返回 (None, err)。"""
        try:
            if is_text:
                return feedparser.parse(str(url_or_text)), None
            return feedparser.parse(str(url_or_text), request_headers={"User-Agent": UA}), None
        except Exception as e:
            return None, e

    def _one(url):
        out = []
        box = {}
        direct_err = [None]

        def _parse():
            parsed, err = _parse_feed(url)
            if err:
                direct_err[0] = err
                box["err"] = err
                return
            if parsed is None:
                return
            box["parsed"] = parsed

        t = threading.Thread(target=_parse, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            logger.warning("RSS 源超时跳过：%s", url)
            box["timeout"] = True
        if "parsed" not in box and use_blocked:
            # 代理升级：直连超时/失败 → fetch_blocked 抓 RSS 文本再解析
            try:
                from fetch_blocked import fetch_blocked as _fb
                raw = _fb(url)
                if raw and not str(raw).startswith("错误"):
                    parsed, err = _parse_feed(raw, is_text=True)
                    if parsed is not None:
                        logger.info("RSS 源经代理获取成功：%s", url)
                        box["parsed"] = parsed
                    else:
                        logger.warning("RSS 源代理解析失败 %s: %s", url, err)
            except Exception as e:
                logger.warning("RSS 源代理升级失败 %s: %s", url, e)
        if "err" in box and "parsed" not in box:
            logger.warning("RSS 源失败 %s: %s", url, box["err"])
            return out
        if "parsed" not in box:
            logger.warning("RSS 源超时跳过：%s", url)
            return out
        parsed = box["parsed"]
        for e in (parsed.entries or []):
            title = str(getattr(e, "title", "") or "").strip()
            link = str(getattr(e, "link", "") or "").strip()
            if not title or not link:
                continue
            published_ts = None
            published = ""
            pp = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            if pp:
                try:
                    published_ts = calendar.timegm(pp)
                    published = datetime.fromtimestamp(published_ts).astimezone().strftime("%Y-%m-%d %H:%M")
                except Exception:
                    published_ts = None
            if cutoff is not None and published_ts is not None and published_ts < cutoff:
                continue
            summary = re.sub(r"<[^>]+>", " ", str(getattr(e, "summary", "") or ""))
            summary = re.sub(r"\s+", " ", summary).strip()[:400]
            feed_title = str(getattr(getattr(parsed, "feed", None), "title", "") or "") or str(url)
            out.append(Item(title=title[:200], url=link, summary=summary,
                            source=feed_title[:40], published=published))
            if len(out) >= limit_per:
                break
        return out

    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as ex:
        for chunk in ex.map(_one, urls):
            items.extend(chunk)
    # 按发布时间倒序（无时间排后面）
    items.sort(key=lambda it: it.published, reverse=True)
    return items


def collect_search(keywords, limit_per=5, timeout=SEARCH_TIMEOUT):
    """Bing 国内版搜索兜底（RSS 无新内容时）：返回 Item 列表，失败返回 []。"""
    kw_list = [k for k in (keywords or []) if str(k).strip()]
    if not kw_list:
        return []
    import httpx
    from urllib.parse import quote

    items = []
    try:
        with httpx.Client(follow_redirects=True, headers={"User-Agent": UA}, timeout=timeout) as client:
            for kw in kw_list[:5]:
                try:
                    resp = client.get(
                        f"https://www.bing.com/search?q={quote(kw)}&count=10&setlang=zh-CN",
                        headers={"Accept-Language": "zh-CN,zh;q=0.9"},
                    )
                    resp.raise_for_status()
                except Exception as e:
                    logger.warning("搜索失败 %s: %s", kw, e)
                    continue
                for m in _BING_RESULT_RE.finditer(resp.text):
                    link, title, rest = m.groups()
                    title = re.sub(r"<[^>]+>", "", title).strip()
                    if not title or link.startswith(("javascript:", "//")):
                        continue
                    snip_m = re.search(r"<p[^>]*>(.*?)</p>", rest, re.S)
                    snippet = re.sub(r"<[^>]+>", " ", snip_m.group(1)) if snip_m else ""
                    snippet = re.sub(r"\s+", " ", snippet).strip()[:400]
                    items.append(Item(title=title[:200], url=link, summary=snippet, source="Bing搜索"))
                    if len(items) >= limit_per:
                        break
                if len(items) >= limit_per * 2:
                    break
    except Exception as e:
        logger.warning("搜索采集失败: %s", e)
    return items


def _fetch_text(url, timeout=FETCH_FULL_TIMEOUT, max_chars=FETCH_FULL_MAX_CHARS):
    """抓取网页正文（去标签取前 N 字符）；失败返回 ""。"""
    import httpx

    try:
        resp = httpx.get(url, follow_redirects=True, headers={"User-Agent": UA}, timeout=timeout)
        resp.raise_for_status()
        raw = resp.content
        charset = (resp.headers.get("content-type") or "").split("charset=")[-1].strip() or ""
        text = None
        for enc in (charset or "utf-8", "utf-8", "gb18030", "latin-1"):
            if not enc:
                continue
            try:
                text = raw.decode(enc, errors="strict")
                break
            except (LookupError, UnicodeDecodeError):
                continue
        if text is None:
            text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("全文抓取失败 %s: %s", url, e)
        return ""
    text = _BLOCK_SKIP_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_chars]


def fetch_full_text(item, use_blocked=False):
    """抓取 Item 正文并回填；失败保持原摘要（不阻塞主流程）。"""
    if item.fetched or not item.url:
        return item
    if use_blocked:
        try:
            from fetch_blocked import fetch_blocked  # 可选：境外站点代理抓取（P2）
            text = fetch_blocked(item.url) or ""
        except Exception:
            text = ""
    else:
        text = _fetch_text(item.url)
    if text:
        item.full_text = text
        item.fetched = True
        if not item.summary:
            item.summary = text[:300]
    return item


def collect_all(cfg, use_blocked=False):
    """主入口：RSS + 搜索 合并 → 主题相关性过滤 → 去重 → 截断 → 抓全文（深度模式）。

    use_blocked：RSS 直连失败的源自动经 fetch_blocked 代理通道重试，全文抓取
    同样走代理（被墙论坛素材）。默认从 cfg["sources"]["use_blocked"] 读取，
    显式传入则覆盖。
    """
    if not use_blocked:
        use_blocked = bool((cfg.get("sources") or {}).get("use_blocked", False))
    items = []
    rss_urls = expand_rss(cfg)
    items += collect_rss(
        rss_urls,
        since_hours=cfg["sources"].get("since_hours", 24),
        use_blocked=use_blocked,
    )
    if len(items) < 3:  # RSS 太少时用搜索兜底
        items += collect_search(cfg["sources"].get("search_keywords") or [])
    # 主题相关性过滤：综合源（IT之家/少数派等）会混入大量非 AI 内容，
    # 过滤后素材纯净，选题与写作深度明显提升
    items = filter_by_keywords(items, cfg["sources"].get("topic_keywords") or [])
    # 按 URL 去重（保留第一个）
    seen = set()
    deduped = []
    for it in items:
        key = it.url.strip().split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    max_cand = cfg["sources"].get("max_candidates", 30)
    deduped = deduped[:max_cand]
    if cfg["sources"].get("fetch_full_text") and deduped:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(lambda it: fetch_full_text(it, use_blocked=use_blocked), deduped))
    return deduped
