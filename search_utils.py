# -*- coding: utf-8 -*-
"""搜索解析工具：HTML 去标签、结果去重/安全过滤。

从 deepseek_client.py 中拆出的纯函数，供多引擎搜索聚合复用。
"""
import html as _html
import re

from security import _safe_url

BING_RESULT_RE = re.compile(
    r'<li class="b_algo".*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>'
    r"(.*?)(?=<li class=\"b_algo\"|</ol>)",
    re.S,
)
DDG_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r".*?<a[^>]*class=\"result__snippet\"[^>]*>(.*?)</a>",
    re.S,
)
SO360_RESULT_RE = re.compile(
    r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S
)


def strip_tags(html):
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    # 内联标签直接删除（不留空格），块级标签替换为空格分隔
    html = re.sub(r"</?(?:b|strong|em|i|u|span|font|code)[^>]*>", "", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return _html.unescape(re.sub(r"\s+", " ", html)).strip()


def decode_ddg_url(link):
    """DuckDuckGo 跳转链接（/l/?uddg=...）解码为真实 URL。"""
    link = link.replace("&amp;", "&")
    if "uddg=" in link:
        m = re.search(r"[?&]uddg=([^&]+)", link)
        if m:
            try:
                from urllib.parse import unquote

                return unquote(m.group(1))
            except Exception:
                return link
    if link.startswith("//"):
        return "https:" + link
    return link


def search_dedup(results):
    """按规范化 URL 去重（去尾部斜杠/fragment），保留首次出现。"""
    seen, out = set(), []
    for r in results:
        key = str(r.get("url") or "").rstrip("/").split("#")[0]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def search_safe(results):
    """结果链接安全过滤：只保留 http(s) 公网链接（回环不放行：
    搜索结果来自外部，是 SSRF 注入源，恶意站点可注入 localhost 链接）。"""
    return [r for r in results if not _safe_url(r["url"], allow_loopback=False)]
