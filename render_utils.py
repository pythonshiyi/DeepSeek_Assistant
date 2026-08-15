# -*- coding: utf-8 -*-
"""流式渲染与代码块切分工具。

从 main.py 中拆出的纯函数，供流式 Markdown 渲染/代码复制使用。
"""
import re

import mdparse

CODE_FENCE_RE = re.compile(r"^```\w*\s*$", re.MULTILINE)


def stream_defer_needed(buf):
    """流式 Markdown 渲染：缓冲含未闭合标记时暂缓整块（等标记闭合再渲染）。

    覆盖：代码围栏 ```、粗体 **、行内代码 `、链接 [ ] ( )。
    半角括号差值恰好为 1 才暂缓（多对不配对时视为普通文本，避免过度延迟）；
    生成结束由 _finish(force=True) 强制兜底，内容永不丢失。
    """
    if buf.count("```") % 2 == 1:
        return True
    if buf.count("**") % 2 == 1:
        return True
    if buf.count("`") % 2 == 1:
        return True
    if abs(buf.count("[") - buf.count("]")) == 1:
        return True
    if abs(buf.count("(") - buf.count(")")) == 1:
        return True
    return False


def tail_start_offset(raw):
    """最后一个未完块在 raw 中的起始偏移（块边界 = 空行 / 块起始行 / 表格分隔行）。"""
    lines = raw.split("\n")
    offsets = []
    off = 0
    for ln in lines:
        offsets.append(off)
        off += len(ln) + 1
    for i in range(len(lines) - 1, -1, -1):
        ln = lines[i]
        if not ln.strip():
            return offsets[i] + len(ln) + 1
        if mdparse._starts_block(ln) or mdparse._is_table_sep(ln):
            return offsets[i] + len(ln) + 1
    return 0


def stream_tail_split(raw):
    """把流式未渲染内容按段落边界切分，返回 (稳定部分, 尾部部分)。

    尾部 = 仍在书写、尚未到达段落边界的最后一个块（未闭合的段落等），
    它随流式逐帧整体重绘；稳定部分按完整 Markdown 渲染。段落只在
    "空行 / 块起始行 / 表格分隔行"处断开，而不是按输出 chunk 断开——
    修复"一句话被 40ms 输出块拆成多行"的分段问题。
    """
    if not raw:
        return "", ""
    if raw.endswith("\n\n"):
        return raw, ""
    blocks = mdparse.parse_blocks(raw)
    if not blocks:
        return "", raw
    last = blocks[-1]
    if last[0] != "plain" and raw.endswith("\n"):
        # 标题/列表/引用/表格/围栏等块已整行结束 → 全部稳定
        return raw, ""
    start = tail_start_offset(raw)
    return raw[:start], raw[start:]


def split_code_blocks(text):
    segments = []
    pos = 0
    is_code = False
    for match in CODE_FENCE_RE.finditer(text):
        if match.start() > pos:
            segments.append(("code" if is_code else "assistant", text[pos : match.start()]))
        is_code = not is_code
        pos = match.end()
        if pos < len(text) and text[pos] in "\r\n":
            pos += 1
            if pos < len(text) and text[pos] == "\n":
                pos += 1
    if pos < len(text):
        segments.append(("code" if is_code else "assistant", text[pos:]))
    return segments
