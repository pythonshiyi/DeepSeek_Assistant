# -*- coding: utf-8 -*-
"""质量门禁：字数 / 来源标注 / 敏感词 / 完整性 / 双通道查重（Jaccard 粗筛 + LLM 精判）。"""
import logging
import re
from dataclasses import dataclass, field

from . import llm as llm_mod
from .topic import jaccard

logger = logging.getLogger("wechat_writer.quality")


@dataclass
class QualityReport:
    passed: bool = False
    reasons: list = field(default_factory=list)
    score: int = 0
    chars: int = 0


def _strip_md(text):
    """去 Markdown 标记估算正文字数。"""
    t = re.sub(r"```.*?```", "", text, flags=re.S)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"[*_`>|~]", "", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    return t.strip()


def _count_chars(text):
    return len(_strip_md(text))


def _llm_judge_duplicate(title, history_titles, llm_chat=None):
    """LLM 精判标题与最近历史标题是否主题重复。失败按不重复处理（粗筛已兜底）。"""
    llm_chat = llm_chat or llm_mod.chat
    try:
        data = llm_mod.chat_json(
            [
                {
                    "role": "user",
                    "content": (
                        "判断以下文章标题是否与最近已发文章主题重复（换汤不换药也算）。\n"
                        f"候选标题：{title}\n"
                        f"最近已发标题：{history_titles or '（无）'}\n"
                        '只输出 JSON：{"duplicate": true/false}'
                    ),
                }
            ],
            max_tokens=300, temperature=0.1,
        )
        return bool(data.get("duplicate"))
    except Exception:
        logger.warning("标题查重精判失败，按不重复处理")
        return False


def check(article, config, history_topics=None, history_titles=None, llm_chat=None):
    """质量检查。article 需有 .title/.content/.topic。返回 QualityReport。"""
    style = config["style"]
    q = config["quality"]
    text = str(getattr(article, "content", "") or "")
    title = str(getattr(article, "title", "") or "")
    chars = _count_chars(text)
    reasons = []
    score = 100

    # 1 字数
    if chars < style["min_chars"]:
        reasons.append(f"字数不足：{chars} < {style['min_chars']}")
        score -= 30
    elif chars > style["max_chars"] * 1.3:
        reasons.append(f"字数超限：{chars} > {style['max_chars'] * 1.3}")
        score -= 20
    # 2 来源标注
    if q.get("require_sources", True):
        if not re.search(r"参考资料|信息来源|来源：", text):
            reasons.append("缺少来源标注（文末需「参考资料」小节）")
            score -= 25
    # 3 敏感词
    hit = [w for w in (q.get("sensitive_words") or []) if w and w in text]
    if hit:
        reasons.append(f"命中敏感词：{'、'.join(hit[:5])}")
        score -= 60
    # 4 完整性：标题 + ≥2 小节
    if not title:
        reasons.append("缺少标题")
        score -= 20
    sections = len(re.findall(r"^##\s+", text, re.M))
    if sections < 2:
        reasons.append(f"小节不足：{sections} < 2")
        score -= 20
    # 5 双通道查重
    if history_topics or history_titles:
        if history_topics:
            for h in history_topics[-14:]:
                if jaccard(str(getattr(article, "topic", "") or title), h) > q.get("similarity_threshold", 0.70):
                    reasons.append(f"与历史主题重复（相似度超阈值）：{h[:30]}")
                    score -= 40
                    break
        if history_titles and not reasons:
            if _llm_judge_duplicate(title, history_titles[-14:], llm_chat):
                reasons.append("与近期文章主题重复（LLM 精判）")
                score -= 40

    return QualityReport(
        passed=not reasons,
        reasons=reasons,
        score=max(0, score),
        chars=chars,
    )
