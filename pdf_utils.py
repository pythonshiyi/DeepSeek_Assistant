# -*- coding: utf-8 -*-
"""PDF 工具：页码范围解析、中文字体注册。

从 deepseek_client.py 中拆出，供 PDF 提取/生成功能复用。
"""
import logging
import os
import re

logger = logging.getLogger("whaletalk.pdf_utils")

_PDF_FONT_NAME = None  # 已注册的中文字体名（模块级缓存，只注册一次）


def parse_page_range(spec, total):
    """页码范围解析：'1-5' / '3' / '1,3-4' / 'all' → 页码列表（1 起，去重保序）。"""
    spec = str(spec or "all").strip().lower()
    if spec in ("", "all"):
        return list(range(1, total + 1))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                return None
            if not (1 <= lo <= hi <= total):
                return None
            out.extend(range(lo, hi + 1))
        else:
            try:
                n = int(part)
            except ValueError:
                return None
            if not (1 <= n <= total):
                return None
            out.append(n)
    seen = set()
    return [n for n in out if not (n in seen or seen.add(n))]


def md_inline_html(text):
    """Markdown 行内语法 → reportlab Paragraph 支持的 HTML 子集。

    注意：Paragraph 默认把换行符当空白（多行段落会被挤成一行），
    行内转换后必须把 \n 转 <br/> 保留换行。
    """
    import html as _html

    t = _html.escape(str(text or ""))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"`([^`]+?)`", r"<font face='Courier'>\1</font>", t, flags=re.S)
    t = t.replace("\n", "<br/>")
    return t


def md_table_rows(block):
    """Markdown 表格块 → list[list]（跳过分隔行）。"""
    rows = []
    for ln in str(block).split("\n"):
        s = ln.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        cells = [c.strip() for c in s.split("|")]
        if cells and all(re.match(r"^:?-+:?$", c) for c in cells):
            continue
        rows.append([c[:200] for c in cells])
    return rows


def find_cjk_font():
    for cand in (
        "C:/Windows/Fonts/msyh.ttc",   # 微软雅黑
        "C:/Windows/Fonts/msyh.ttf",
        "C:/Windows/Fonts/simhei.ttf",  # 黑体
        "C:/Windows/Fonts/simsun.ttc",  # 宋体
    ):
        if os.path.isfile(cand):
            return cand
    return None


def register_cjk_font():
    """注册中文字体（TTC 需 subfontIndex；失败回退 Helvetica 防崩溃）。"""
    global _PDF_FONT_NAME
    if _PDF_FONT_NAME:
        return _PDF_FONT_NAME
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    path = find_cjk_font()
    if not path:
        _PDF_FONT_NAME = "Helvetica"
        return _PDF_FONT_NAME
    try:
        if path.lower().endswith(".ttc"):
            pdfmetrics.registerFont(TTFont("WhaleTalkCJK", path, subfontIndex=0))
        else:
            pdfmetrics.registerFont(TTFont("WhaleTalkCJK", path))
        _PDF_FONT_NAME = "WhaleTalkCJK"
    except Exception:
        logger.warning("中文字体注册失败（%s），回退 Helvetica", path)
        _PDF_FONT_NAME = "Helvetica"
    return _PDF_FONT_NAME
