# -*- coding: utf-8 -*-
"""公众号自动写作工具（WeChat Writer）：每天自动生成 AI 领域公众号文章草稿。

独立包，不依赖鲸语 GUI；核心依赖标准库 + httpx + feedparser（可选 tiktoken）。
产出草稿供用户审阅发布，绝不自动发布。
"""
from .main import run_once, run_daily

__all__ = ["run_once", "run_daily"]
__version__ = "1.0.0"
