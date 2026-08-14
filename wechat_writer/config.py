# -*- coding: utf-8 -*-
"""WeChat Writer 配置：默认值 + 用户配置文件合并（缺失/损坏时用默认，不抛异常）。"""
import json
import logging
import os

logger = logging.getLogger("wechat_writer")

DEFAULT_CONFIG = {
    "schedule": "0 9 * * *",          # cron：每天 09:00（供 schedule_task 使用）
    "topic_domain": "AI",              # 主题域
    "style": {
        "audience": "general",         # general=大众科普 / geek=技术深度
        "min_chars": 1200, "max_chars": 2500,
        "sections": 4,                 # 正文小节数
        "tone": "通俗易懂，有理有据，适度有趣",
    },
    "sources": {
        # 信源按组组织：用户可在 wechat_writer/config.json 用 rss_groups 挑选组，
        # 或用 rss 直接覆盖完整列表（以下均为实测可达源）
        "rss_groups": {
            "ai_media": [   # AI 垂直媒体（首选）
                "https://www.jiqizhixin.com/rss",
                "https://www.qbitai.com/feed",
                "https://www.infoq.cn/feed",
                "https://www.leiphone.com/feed",
            ],
            "tech": [       # 科技/开发者综合
                "https://www.ithome.com/rss/",
                "https://www.oschina.net/news/rss",
                "https://www.solidot.org/index.rss",
            ],
            "life_tech": [  # 效率工具与数字生活
                "https://sspai.com/feed",
            ],
            "dev_global": [ # 国际开发者社区（英文，补充国际视角）
                "https://hnrss.org/frontpage",
            ],
        },
        "rss": None,  # None = 按 rss_groups 展开；用户可给列表直接覆盖
        "search_keywords": ["AI 最新进展 大模型", "人工智能 重大发布", "AI Agent 工具 开源"],
        "topic_keywords": [  # 主题相关性过滤：标题/摘要含任一关键词才保留（素材纯净度）。
            # 注意：只放"明确指向 AI 主题"的词；"发布/评测/大厂"等宽泛词会让
            # 手机/游戏等无关内容漏进素材，稀释选题深度（真实教训）
            "AI", "人工智能", "大模型", "LLM", "GPT", "ChatGPT", "Gemini", "Claude",
            "Agent", "智能体", "机器学习", "深度学习", "神经网络", "OpenAI", "Anthropic",
            "Google", "微软", "Meta", "英伟达", "NVIDIA", "芯片", "半导体", "机器人",
            "自动驾驶", "算法", "开源", "代码", "编程", "数据", "量子", "GPU", "算力",
            "多模态", "文心", "通义", "Kimi", "DeepSeek", "豆包", "智谱", "Moonshot",
            "HuggingFace", "PyTorch", "TensorFlow", "Python", "GitHub", "Copilot", "Cursor",
            "AI 模型", "AI 应用", "AI 工具", "AI 芯片", "AI 创业", "AI 编程",
        ],
        "max_candidates": 30,          # 采集候选上限
        "fetch_full_text": True,       # 抓全文（深度写作：引用全文节选；关闭可提速）
        "since_hours": 24,             # 只看最近 N 小时的条目
    },
    "quality": {
        "similarity_threshold": 0.70,  # 与历史主题 bigram Jaccard 相似度上限
        "sensitive_words": ["翻墙", "破解", "代购", "赌博", "博彩", "刷单"],
        "require_sources": True,       # 正文必须标注来源
        "max_retry": 1,                # 质检不过重写次数
    },
    "output": {
        "platform": "公众号",
        "save_html": True,
        "cover_image": False,          # MVP 不生成封面
        "ai_disclosure": True,         # 文末标注「本文由 AI 辅助写作」
    },
}

_KNOWN_KEYS = {"schedule", "topic_domain", "style", "sources", "quality", "output"}


def load_config(path=None):
    """加载配置：无文件/损坏时返回默认（深拷贝，防共享可变对象）。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                for k in _KNOWN_KEYS:
                    if isinstance(disk.get(k), dict):
                        cfg[k].update({kk: vv for kk, vv in disk[k].items() if vv is not None})
                    elif k in disk and disk[k] is not None:
                        cfg[k] = disk[k]
        except Exception:
            logger.exception("读取 wechat_writer 配置失败，使用默认值")
    # 归一化：关键字段钳制
    try:
        cfg["quality"]["similarity_threshold"] = max(0.1, min(0.99, float(cfg["quality"]["similarity_threshold"])))
    except (TypeError, ValueError):
        cfg["quality"]["similarity_threshold"] = DEFAULT_CONFIG["quality"]["similarity_threshold"]
    try:
        cfg["quality"]["max_retry"] = max(0, min(3, int(cfg["quality"]["max_retry"])))
    except (TypeError, ValueError):
        cfg["quality"]["max_retry"] = 1
    try:
        cfg["sources"]["max_candidates"] = max(5, min(200, int(cfg["sources"]["max_candidates"])))
    except (TypeError, ValueError):
        cfg["sources"]["max_candidates"] = 30
    try:
        cfg["sources"]["since_hours"] = max(1, min(24 * 7, int(cfg["sources"]["since_hours"])))
    except (TypeError, ValueError):
        cfg["sources"]["since_hours"] = 24
    try:
        cfg["sources"]["fetch_full_text"] = bool(cfg["sources"].get("fetch_full_text", True))
    except Exception:
        cfg["sources"]["fetch_full_text"] = True
    try:
        cfg["style"]["min_chars"] = max(500, int(cfg["style"]["min_chars"]))
        cfg["style"]["max_chars"] = max(cfg["style"]["min_chars"], min(8000, int(cfg["style"]["max_chars"])))
    except (TypeError, ValueError):
        cfg["style"]["min_chars"], cfg["style"]["max_chars"] = 1200, 2500
    try:
        cfg["style"]["sections"] = max(2, min(10, int(cfg["style"]["sections"])))
    except (TypeError, ValueError):
        cfg["style"]["sections"] = 4
    if cfg["style"]["audience"] not in ("general", "geek"):
        cfg["style"]["audience"] = "general"
    return cfg
