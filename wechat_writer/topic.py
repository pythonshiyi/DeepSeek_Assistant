# -*- coding: utf-8 -*-
"""选题引擎：LLM 提炼候选主题 → 双通道去重（bigram Jaccard 粗筛 + LLM 精判）→ 评分排序。

关键设计（方案文档 §3.3）：纯中文 bigram Jaccard 对同义改写敏感度不足
（"OpenAI 发布新模型" vs "OpenAI 新模型发布后的影响" 词集相似度≈0），
必须叠加 LLM 精判通道，否则连续几天主题重复。
"""
import json
import logging
import re

from . import llm as llm_mod

logger = logging.getLogger("wechat_writer.topic")

_BIGRAM_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def _bigrams(text):
    """中文按字符 bigram、英文按词切分（与记忆检索同款）。"""
    out = []
    for seg in _BIGRAM_RE.findall(str(text or "").lower()):
        if re.search(r"[\u4e00-\u9fff]", seg):
            out.extend(seg[i:i + 2] for i in range(max(0, len(seg) - 1)))
            if len(seg) <= 4:
                out.append(seg)
        else:
            out.append(seg)
    return set(out)


def jaccard(a, b):
    """bigram 词集 Jaccard 相似度（0~1）。"""
    sa, sb = _bigrams(a), _bigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class Topic:
    def __init__(self, name, angle="", related=(), fallback=False):
        self.name = name          # 主题名
        self.angle = angle        # 切入点
        self.related = list(related)  # 关联素材索引
        self.fallback = fallback  # 是否盘点型降级选题

    def __repr__(self):
        return f"Topic({self.name!r})"


def _llm_pick_candidates(items, history_topics, llm_chat):
    """让 LLM 从素材清单提炼 3-5 个候选主题（含切入点与关联素材编号）。"""
    lines = [f"{i}. {it.display(200)}" for i, it in enumerate(items)]
    material = "\n".join(lines[:40])
    prompt = (
        f"你是 AI 领域公众号主编。以下是今日采集的资讯清单（编号对应素材）：\n{material}\n\n"
        "请提炼 3-5 个适合写公众号文章的选题。要求：\n"
        "1. 每个选题给：name（一句话主题）、angle（独特切入点）、related（关联素材编号数组，1-3 个）、why（为什么值得写，一句话）\n"
        "2. 选题要有新闻性/时效性，避免泛泛而谈\n"
        f"3. 历史已写主题（避免重复）：{history_topics[-14:] or '（无）'}\n"
        "严格输出 JSON 数组，如："
        '[{"name": "...", "angle": "...", "related": [1, 3], "why": "..."}]'
    )
    text = llm_chat([{"role": "user", "content": prompt}], max_tokens=2500, temperature=0.6)
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError(f"选题输出不含 JSON 数组：{text[:200]}")
    data = json.loads(m.group(0))
    out = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        if not name:
            continue
        rel = [int(x) for x in (d.get("related") or []) if str(x).isdigit()]
        out.append(Topic(name, str(d.get("angle") or ""), rel))
    return out[:5]


def _llm_judge_duplicate(candidate, history_topics, llm_chat):
    """LLM 精判：候选主题与最近历史是否"换汤不换药"（返回 True=重复）。"""
    prompt = (
        "判断以下候选公众号选题是否与最近已写主题重复（换汤不换药、角度雷同也判重复）。\n"
        f"候选主题：{candidate}\n"
        f"最近已写主题：{history_topics or '（无）'}\n"
        "只输出 JSON：{\"duplicate\": true/false, \"reason\": \"一句话\"}"
    )
    try:
        data = llm_mod.chat_json(
            [{"role": "user", "content": prompt}], max_tokens=500, temperature=0.1
        )
        return bool(data.get("duplicate"))
    except Exception:
        logger.warning("LLM 查重精判失败，按不重复处理")
        return False


def _score_topic(topic, items, history_topics):
    """评分：新鲜度（素材发布时间）+ 素材覆盖度。"""
    score = 0.0
    related = [items[i] for i in topic.related if 0 <= i < len(items)]
    if related:
        score += min(1.0, len(related) / 3.0) * 3.0
        for it in related[:3]:
            if it.published:
                score += 0.2
    # 与历史的粗筛相似度作为负分提示（粗筛阈值内）
    for h in history_topics[-7:]:
        s = jaccard(topic.name, h)
        if s > 0.5:
            score -= 1.5 * s
    return score


def pick_topic(items, history_topics, llm_chat=None):
    """选题主流程：LLM 候选 → 双通道去重 → 评分选优 → 全被剔除时降级盘点型。

    llm_chat：可注入的 LLM chat 函数（默认 llm.chat）；测试时可 mock。
    返回 Topic。
    """
    llm_chat = llm_chat or llm_mod.chat
    if not items:
        return Topic("今日 AI 资讯盘点", "综合梳理当日要点", [], fallback=True)
    try:
        candidates = _llm_pick_candidates(items, history_topics, llm_chat)
    except Exception:
        logger.exception("选题提炼失败，降级为盘点型")
        candidates = []
    if not candidates:
        return Topic("今日 AI 资讯盘点", "综合梳理当日要点", [], fallback=True)

    kept = []
    for t in candidates:
        # 通道 A：Jaccard 粗筛（零成本）
        if any(jaccard(t.name, h) > 0.70 for h in history_topics[-14:]):
            logger.info("选题被粗筛剔除（重复）：%s", t.name)
            continue
        kept.append(t)
    if not kept:
        logger.info("候选全部被粗筛剔除，降级盘点型")
        return Topic("今日 AI 资讯盘点", "综合梳理当日要点", [], fallback=True)

    # 通道 B：LLM 精判（对剩余候选逐一比对最近 14 天）
    final = []
    for t in kept:
        if history_topics:
            if _llm_judge_duplicate(f"{t.name}（切入点：{t.angle}）", history_topics[-14:], llm_chat):
                logger.info("选题被 LLM 精判剔除（换汤不换药）：%s", t.name)
                continue
        final.append(t)
    if not final:
        logger.info("候选全部被 LLM 精判剔除，降级盘点型")
        return Topic("今日 AI 资讯盘点", "综合梳理当日要点", [], fallback=True)

    final.sort(key=lambda t: _score_topic(t, items, history_topics), reverse=True)
    return final[0]
