# -*- coding: utf-8 -*-
"""LLM 写作引擎：大纲 → 正文 → 润色 三阶段（全部走 DeepSeek API）。"""
import json
import logging
import re
from dataclasses import dataclass

from . import llm as llm_mod

logger = logging.getLogger("wechat_writer.writer")

STYLE_MAP = {"general": "大众读者（通俗易懂，减少术语）", "geek": "技术读者（可以深入细节）"}


@dataclass
class Article:
    topic: str
    title: str
    content: str        # Markdown 正文（含小节）
    outline: str = ""
    candidates: list = None  # 候选标题


def _material_block(items, related=None):
    """组装素材文本（仅可引用这些事实）。

    已抓取全文的素材附加"全文节选"（深度写作：LLM 可引用正文细节而非只有标题摘要）。
    """
    rel = set(related or [])
    lines = []
    for i, it in enumerate(items):
        if rel and i not in rel:
            continue
        head = f"[素材{i}] 标题：{it.title}（来源：{it.source}）\n链接：{it.url}\n摘要：{it.summary[:300]}"
        if it.fetched and it.full_text:
            head += f"\n全文节选：{it.full_text[:1500]}"
        lines.append(head)
    return "\n\n".join(lines[:8])


def build_outline(topic, items, style, llm_chat=None):
    """阶段 1：结构化大纲（标题×3 + 导语要点 + 小节标题×N + 结语要点）。"""
    llm_chat = llm_chat or llm_mod.chat
    material = _material_block(items, topic.related if not topic.fallback else None)
    if not material:
        material = _material_block(items)
    prompt = (
        f"你是资深 AI 领域公众号主笔。请为以下选题写一份写作大纲。\n\n"
        f"选题：{topic.name}\n切入点：{topic.angle or '（默认角度）'}\n\n"
        f"可用素材（仅可引用这些事实，不得虚构）：\n{material}\n\n"
        f"要求：\n1. 输出 3 个候选标题（一个悬念型、一个数字型、一个价值型）\n"
        f"2. 导语要点（2-3 句抓住读者）\n"
        f"3. {style['sections']} 个小节标题（每个配 1 句内容要点）\n"
        "4. 结语要点（观点或展望）\n"
        "严格输出 JSON："
        '{"titles": ["t1","t2","t3"], "lead": "...", "sections": [{"h": "...", "k": "..."}], "conclusion": "..."}'
    )
    text = llm_chat([{"role": "user", "content": prompt}], max_tokens=2500, temperature=0.7)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"大纲输出不含 JSON：{text[:200]}")
    return json.loads(m.group(0))


def write_body(topic, items, style, outline, llm_chat=None):
    """阶段 2：按大纲生成正文（一次生成全篇）。"""
    llm_chat = llm_chat or llm_mod.chat
    material = _material_block(items, topic.related if not topic.fallback else None)
    if not material:
        material = _material_block(items)
    sections_txt = "\n".join(f"## {s.get('h')}\n（要点：{s.get('k')}）" for s in outline.get("sections", []))
    prompt = (
        f"你是资深 AI 领域公众号主笔。请围绕主题「{topic.name}」写一篇公众号文章。\n\n"
        f"素材（仅可引用这些事实，不得编造数据/人名/公司名）：\n{material}\n\n"
        f"大纲：\n标题候选：{' / '.join(outline.get('titles', []))}\n"
        f"导语：{outline.get('lead', '')}\n{sections_txt}\n结语：{outline.get('conclusion', '')}\n\n"
        f"写作要求：\n"
        f"1. 采用第 1 个候选标题（标题单独一行 ## 开头）\n"
        f"2. 正文 {style['sections']} 个小节，每节 300-500 字，用 ## 小节标题\n"
        "3. 短段落（每段 2-4 句），口语化，善用设问\n"
        "4. 开篇导语 2 句内抓住读者；结语给出观点或展望\n"
        "5. 文末列出「参考资料」来源（素材的链接与来源名）\n"
        f"6. 全文 {style['min_chars']}-{style['max_chars']} 字，面向{STYLE_MAP.get(style['audience'], style['audience'])}，风格：{style['tone']}\n"
        "输出 Markdown。"
    )
    return llm_chat([{"role": "user", "content": prompt}], max_tokens=8000, temperature=0.7)


def polish(article_text, style, llm_chat=None):
    """阶段 3：润色自检（错别字/语病/超长句 + 字数压缩或扩写）。"""
    llm_chat = llm_chat or llm_mod.chat
    prompt = (
        "你是公众号资深编辑。请润色以下文章草稿：\n"
        "1. 修正错别字、语病、超长句；\n"
        "2. 保持事实与结构不变，不新增虚构内容；\n"
        "3. 保持标题与「参考资料」小节不变；\n"
        f"4. 全文控制在 {style['min_chars']}-{style['max_chars']} 字（删冗余或补展开）；\n"
        "5. 输出完整润色后的 Markdown（不要任何说明文字）。\n\n"
        "---- 文章 ----\n"
        f"{article_text}"
    )
    return llm_chat([{"role": "user", "content": prompt}], max_tokens=8000, temperature=0.5)


def write_article(topic, items, style, llm_chat=None):
    """三阶段主流程：大纲 → 正文 → 润色。失败抛出（调用方降级）。"""
    llm_chat = llm_chat or llm_mod.chat
    outline = build_outline(topic, items, style, llm_chat)
    body = write_body(topic, items, style, outline, llm_chat)
    polished = polish(body, style, llm_chat)
    titles = outline.get("titles") or []
    first_line = next((ln for ln in polished.splitlines() if ln.strip().startswith("#")), "")
    title = first_line.lstrip("#").strip() or (titles[0] if titles else topic.name)
    if first_line:
        polished = "\n".join(ln for ln in polished.splitlines() if ln != first_line).strip()
    return Article(topic=topic.name, title=title, content=polished, outline=json.dumps(outline, ensure_ascii=False), candidates=titles)


def rewrite_fix(article, reasons, style, llm_chat=None):
    """质检未通过时按原因重写（只重写正文，保留标题）。"""
    llm_chat = llm_chat or llm_mod.chat
    prompt = (
        "请根据以下质检意见修改文章草稿：\n"
        f"问题：{'；'.join(reasons)}\n"
        "要求：修正上述问题，保留标题与事实，输出完整修订后的 Markdown。\n\n"
        "---- 原文 ----\n"
        f"{article.title}\n\n{article.content}"
    )
    fixed = llm_chat([{"role": "user", "content": prompt}], max_tokens=8000, temperature=0.5)
    return Article(topic=article.topic, title=article.title, content=fixed,
                   outline=article.outline, candidates=article.candidates)
