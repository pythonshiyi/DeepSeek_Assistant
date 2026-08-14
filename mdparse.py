import re
import unicodedata
from collections import OrderedDict

FENCE_RE = re.compile(r"^```([^`\s]*)\s*$", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)
HR_RE = re.compile(r"^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$")
QUOTE_RE = re.compile(r"^(>+)[ \t]*(.*)$")
LIST_RE = re.compile(r"^[ \t]*([-*+]|\d+[.)])[ \t]+(.*)$")
TABLE_SEP_CELL_RE = re.compile(r"^:?-+:?$")

_CODE_RE = re.compile(r"`([^`\n]+?)`")
_IMAGE_RE = re.compile(r"!\[([^\]]*?)\]\(([^()\s]+?)\)")
_LINK_RE = re.compile(r"\[([^\]]+?)\]\(([^()\s]+?)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_ITALIC_RE = re.compile(r"(?<!\w)\*(?![\s*])([^*\n]+?)(?<!\s)\*(?!\w)")

_INLINE_PATTERNS = (_IMAGE_RE, _CODE_RE, _LINK_RE, _BOLD_RE, _STRIKE_RE, _ITALIC_RE)

# 行内标记特征字符：无任何标记时整行跳过正则循环（纯文本行占绝大多数）
_INLINE_HINT_RE = re.compile(r"[*`\[~]")
# 超长行（日志/JSON/长 URL）降级为原样输出：避免大量孤立 ** 起点对行尾的
# 重复扫描（最坏 O(n²)），且这类行基本无内联样式收益
_INLINE_MAX_LINE = 4096


def _starts_block(line):
    return bool(
        FENCE_RE.match(line)
        or HEADING_RE.match(line)
        or HR_RE.match(line)
        or QUOTE_RE.match(line)
        or LIST_RE.match(line)
    )


def _is_table_row(line):
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_table_sep(line):
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return False
    cells = [c.strip() for c in s[1:-1].split("|")]
    return bool(cells) and all(TABLE_SEP_CELL_RE.match(c) for c in cells)


def _inline(line):
    """单行内联解析，返回 (显示文本, spans, links)。

    spans: [(start, end, tag)]，相对该行的偏移
    links: [(start, end, url)]
    """
    if not _INLINE_HINT_RE.search(line):
        return line, [], []
    if len(line) > _INLINE_MAX_LINE:
        # 超长行快速路径：原样返回（不建 spans/links），杜绝 O(n²) 逐字符扫描
        return line, [], []
    out = []
    spans = []
    links = []
    out_len = 0
    pos = 0
    n = len(line)
    while pos < n:
        hit = None
        for pat in _INLINE_PATTERNS:
            m = pat.match(line, pos)
            if m:
                hit = (pat, m)
                break
        if hit is None:
            out.append(line[pos])
            out_len += 1
            pos += 1
            continue
        pat, m = hit
        if pat is _IMAGE_RE:
            # 图片：显示 [图片] 占位（点击打开原图链接）
            seg = f"[图片] {m.group(1)}".strip() if m.group(1).strip() else "[图片]"
            url = m.group(2)
            out.append(seg)
            spans.append((out_len, out_len + len(seg), "link"))
            links.append((out_len, out_len + len(seg), url))
            out_len += len(seg)
        elif pat is _CODE_RE:
            seg = m.group(1)
            out.append(seg)
            spans.append((out_len, out_len + len(seg), "code"))
            out_len += len(seg)
        elif pat is _LINK_RE:
            seg, sub_spans, sub_links = _inline(m.group(1))
            url = m.group(2)
            out.append(seg)
            for a, b, st in sub_spans:
                spans.append((out_len + a, out_len + b, st))
            for a, b, u in sub_links:
                links.append((out_len + a, out_len + b, u))
            if seg:
                spans.append((out_len, out_len + len(seg), "link"))
                links.append((out_len, out_len + len(seg), url))
            out_len += len(seg)
        elif pat is _BOLD_RE or pat is _ITALIC_RE or pat is _STRIKE_RE:
            tag = "bold" if pat is _BOLD_RE else "italic" if pat is _ITALIC_RE else "strike"
            seg, sub_spans, sub_links = _inline(m.group(1))
            out.append(seg)
            for a, b, st in sub_spans:
                spans.append((out_len + a, out_len + b, st))
            for a, b, url in sub_links:
                links.append((out_len + a, out_len + b, url))
            if seg:
                spans.append((out_len, out_len + len(seg), tag))
            out_len += len(seg)
        pos = m.end()
    return "".join(out), spans, links


def parse_blocks(text):
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = FENCE_RE.match(line)
        if fence:
            lang = fence.group(1) or ""
            buf = []
            i += 1
            while i < n and not FENCE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            if i < n:
                i += 1
            blocks.append(("code", "\n".join(buf), lang))
            continue
        heading = HEADING_RE.match(line)
        if heading:
            blocks.append((f"h{len(heading.group(1))}", heading.group(2)))
            i += 1
            continue
        if HR_RE.match(line):
            blocks.append(("hr", ""))
            i += 1
            continue
        if QUOTE_RE.match(line):
            buf = []
            while i < n:
                m = QUOTE_RE.match(lines[i])
                if not m:
                    break
                buf.append(m.group(2))
                i += 1
            blocks.append(("quote", "\n".join(buf)))
            continue
        if LIST_RE.match(line):
            buf = []
            while i < n:
                m = LIST_RE.match(lines[i])
                if not m:
                    break
                buf.append(lines[i])
                i += 1
            blocks.append(("list", "\n".join(buf)))
            continue
        if _is_table_row(line) and i + 1 < n and _is_table_sep(lines[i + 1]):
            header = line
            i += 2
            rows = []
            while i < n and _is_table_row(lines[i]):
                rows.append(lines[i])
                i += 1
            blocks.append(("table", "\n".join([header] + rows)))
            continue
        buf = [line]
        i += 1
        while i < n:
            ln = lines[i]
            if not ln.strip():
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j >= n or _starts_block(lines[j]):
                    break
                buf.append("")
                i = j
                continue
            if _starts_block(ln):
                break
            if _is_table_row(ln) and i + 1 < n and _is_table_sep(lines[i + 1]):
                break
            buf.append(ln)
            i += 1
        blocks.append(("plain", "\n".join(buf)))
    return blocks


def _disp_width(s):
    width = 0
    for ch in s:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _ljust_cjk(s, width):
    return s + " " * max(0, width - _disp_width(s))


def _render_table(block):
    rows = []
    for ln in block.split("\n"):
        s = ln.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        cells = [c.strip() for c in s.split("|")]
        if all(TABLE_SEP_CELL_RE.match(c) for c in cells):
            continue
        rows.append(cells)
    if not rows:
        return "", []
    width = max(len(r) for r in rows)
    for r in rows:
        while len(r) < width:
            r.append("")
    widths = [max(_disp_width(r[c]) for r in rows) for c in range(width)]

    def row_line(r):
        return "| " + " | ".join(_ljust_cjk(cell, widths[ci]) for ci, cell in enumerate(r)) + " |"

    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    out = [row_line(rows[0]), sep]
    out.extend(row_line(r) for r in rows[1:])
    head = len(out[0])
    return "\n".join(out), [(0, head, "table_head")]


def render_markdown(text, plain=False):
    """将 Markdown 文本渲染为可显示的纯文本 + 样式区间。

    返回 (display_text, spans, links, code_blocks)：
    - spans: [(start, end, tag)]，相对 display_text 的偏移
    - links: [(start, end, url)]
    - code_blocks: [(start, end, content)]
    plain=True 时原样返回，不做任何解析。

    内容级 LRU 缓存：长会话全量重渲染/流式重渲染时同一 payload 反复解析
    （实测 600 条消息纯解析 ~0.33s/次），缓存后重渲染成本趋近于零。
    缓存键为 payload 字符串（内容不变即命中），按条目数与总字节双重上限。
    """
    if plain:
        return text, [], [], []
    hit = _render_cache_get(text)
    if hit is not None:
        return hit
    blocks = parse_blocks(text)
    out_parts = []
    spans = []
    links = []
    code_blocks = []
    out_len = 0

    def emit(seg, tag=None):
        nonlocal out_len
        if not seg:
            return
        out_parts.append(seg)
        if tag:
            spans.append((out_len, out_len + len(seg), tag))
        out_len += len(seg)

    def emit_inline(ln, base_tag=None):
        nonlocal out_len
        s = out_len
        text_, sp, lk = _inline(ln)
        emit(text_)
        if text_ and base_tag:
            spans.append((s, out_len, base_tag))
        for a, b, tag in sp:
            spans.append((s + a, s + b, tag))
        for a, b, url in lk:
            links.append((s + a, s + b, url))

    for blk in blocks:
        kind = blk[0]
        block = blk[1]
        if kind == "code":
            lang = blk[2] if len(blk) > 2 else ""
            if lang:
                emit(lang + "\n", "code_lang")
            seg = block + "\n"
            s = out_len
            emit(seg, "code")
            code_blocks.append((s, out_len, block))
        elif kind == "hr":
            emit("━" * 36 + "\n", "hr")
        elif kind == "table":
            ttext, tspans = _render_table(block)
            s = out_len
            emit(ttext + "\n", "table")
            for a, b, st in tspans:
                spans.append((s + a, s + b, st))
        elif kind == "list":
            for ln in block.split("\n"):
                # 无序与有序列表统一渲染：1. 第一项 → • 第一项（此前只认 [-*+]）
                mm = re.match(r"^(\s*)(?:[-*+]|\d+[.)])[ \t]+(.*)$", ln)
                if mm:
                    ln = f"{mm.group(1)}• {mm.group(2)}"
                emit_inline(ln, base_tag="list")
                emit("\n")
        else:
            for ln in block.split("\n"):
                base = kind if kind.startswith("h") else ("quote" if kind == "quote" else None)
                emit_inline(ln, base_tag=base)
                emit("\n")
    result = ("".join(out_parts), spans, links, code_blocks)
    _render_cache_put(text, result)
    return result


# ---------- render_markdown 内容级 LRU 缓存 ----------
_RENDER_CACHE = OrderedDict()
_RENDER_CACHE_MAX_ITEMS = 4096
_RENDER_CACHE_MAX_BYTES = 64 * 1024 * 1024
_RENDER_CACHE_BYTES = 0


def _render_cache_put(text, result):
    """写入缓存并执行 LRU/字节上限淘汰。超长内容（>2MB 单条）不入缓存。"""
    global _RENDER_CACHE_BYTES
    # 体积账目：正文 + 文本 span（CJK 按 2 字节计低估 UTF-8，再按字符数补计）
    text_out = result[0]
    size = len(text) * 2 + len(text_out) * 2
    for seq in result[1:3]:
        size += len(seq) * 24  # span/link 元组近似开销
    if size <= 0 or size > 2 * 1024 * 1024:
        return
    old = _RENDER_CACHE.get(text)
    if old is not None:
        _RENDER_CACHE_BYTES -= _size_of_entry(old)
        del _RENDER_CACHE[text]
    _RENDER_CACHE[text] = (size, result)
    _RENDER_CACHE_BYTES += size
    while len(_RENDER_CACHE) > _RENDER_CACHE_MAX_ITEMS or _RENDER_CACHE_BYTES > _RENDER_CACHE_MAX_BYTES:
        try:
            k, v = _RENDER_CACHE.popitem(last=False)
            _RENDER_CACHE_BYTES -= v[0]
        except (KeyError, IndexError):
            break


def _render_cache_get(text):
    hit = _RENDER_CACHE.get(text)
    if hit is None:
        return None
    _RENDER_CACHE.move_to_end(text)
    return hit[1]


def _size_of_entry(entry):
    return entry[0] if isinstance(entry, tuple) and entry else 0


def to_plain(text):
    """粗略去除 Markdown 标记，得到纯文本。"""
    t = text.replace("\r", "")
    t = FENCE_RE.sub("", t)
    t = HEADING_RE.sub(r"\2", t)
    t = re.sub(r"^[ \t]*>+[ \t]?", "", t, flags=re.M)
    t = re.sub(r"^[ \t]*([-*+]|\d+[.)])[ \t]+", "", t, flags=re.M)
    t = re.sub(r"^[ \t]*\|.*$", "", t, flags=re.M)
    t = re.sub(r"^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$", "", t, flags=re.M)
    t = _BOLD_RE.sub(r"\1", t)
    t = _STRIKE_RE.sub(r"\1", t)
    t = _LINK_RE.sub(r"\1", t)
    t = _CODE_RE.sub(r"\1", t)
    t = _ITALIC_RE.sub(r"\1", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
