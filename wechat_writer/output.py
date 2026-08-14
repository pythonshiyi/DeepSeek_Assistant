# -*- coding: utf-8 -*-
"""输出：草稿箱（publish_draft 兼容格式）+ 本地存档 + 可选 HTML。

草稿格式：drafts/{platform}_{safe_title}_{ts}.md，内容 "# {title}\n\n{content}"
（与鲸语 publish_draft 同格式，用户从现有草稿箱即可看到）。
"""
import html as html_mod
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger("wechat_writer.output")

_PLATFORM = "公众号"


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]', "_", str(name or ""))[:40] or "draft"


def save_article(article, config, drafts_dir=None, archive_dir=None, data_dir=None, dry_run=False):
    """保存文章。dry_run=True 不产生任何文件，返回路径占位。

    返回 {"draft_path", "archive_path", "html_path"}。
    """
    out = {"draft_path": "", "archive_path": "", "html_path": ""}
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    platform = str(config["output"].get("platform") or _PLATFORM)
    title = str(article.title or article.topic or "未命名").strip()
    content = f"# {title}\n\n{article.content}"

    if dry_run:
        return out

    if not drafts_dir:
        drafts_dir = os.path.join(os.environ.get("WORKSPACE_DIR", ""), "drafts")
        if not os.path.isdir(drafts_dir):
            drafts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "drafts")
    try:
        os.makedirs(drafts_dir, exist_ok=True)
        safe = f"{_safe(platform)}_{_safe(title)}_{ts}.md"
        draft_path = os.path.join(drafts_dir, safe)
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(content)
        out["draft_path"] = draft_path
    except Exception:
        logger.exception("草稿箱写入失败")

    if not archive_dir:
        base = data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        archive_dir = os.path.join(base, "articles")
    try:
        now = datetime.now()
        sub = os.path.join(archive_dir, str(now.year), f"{now.month:02d}")
        os.makedirs(sub, exist_ok=True)
        archive_path = os.path.join(sub, f"{now.day:02d}.md")
        head = (
            f"# {title}\n\n"
            f"- 日期：{now:%Y-%m-%d %H:%M}\n"
            f"- 主题：{getattr(article, 'topic', '')}\n"
            f"- 平台：{platform}\n\n---\n\n"
        )
        with open(archive_path, "w", encoding="utf-8") as f:
            f.write(head + article.content)
        out["archive_path"] = archive_path
    except Exception:
        logger.exception("本地存档写入失败")

    if config["output"].get("save_html"):
        try:
            html_path = out["archive_path"] or out["draft_path"]
            if html_path:
                html_path = os.path.splitext(html_path)[0] + ".html"
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(_to_html(title, article.content))
                out["html_path"] = html_path
        except Exception:
            logger.exception("HTML 输出失败")

    return out


def _to_html(title, md_text):
    """极简 md → HTML（段落/标题/列表/代码块/粗体/引用）。"""
    import markdown

    body = markdown.markdown(
        md_text,
        extensions=["fenced_code", "tables", "sane_lists"],
    )
    return (
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>{html_mod.escape(title)}</title><style>"
        "body{font-family:'Microsoft YaHei UI',sans-serif;max-width:720px;margin:0 auto;"
        "padding:24px;line-height:1.8;color:#1d1f24;background:#fafafa;}"
        "h1,h2,h3{color:#17324d;}blockquote{border-left:4px solid #ccc;margin:0;padding:4px 12px;color:#666;}"
        "code{background:#f0f0f0;padding:1px 5px;border-radius:4px;}"
        "pre{background:#f6f8fa;padding:12px;border-radius:6px;overflow:auto;}"
        "pre code{background:none;padding:0;}table{border-collapse:collapse;}td,th{border:1px solid #ddd;padding:6px 10px;}"
        "</style></head><body>" + body + "</body></html>"
    )
