# -*- coding: utf-8 -*-
"""主流程编排：采集 → 选题 → 写作 → 质检（重试）→ 输出。

CLI：python -m wechat_writer --dry-run / --run / --topic "指定主题"
"""
import argparse
import json
import logging
import os
import sys

from . import config as cfg_mod
from . import history as hist_mod
from . import output as out_mod
from . import quality as q_mod
from . import sources as src_mod
from . import topic as topic_mod
from . import writer as w_mod

logger = logging.getLogger("wechat_writer")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
LOG_PATH = os.path.join(DATA_DIR, "logs", "run.log")


def _init_logging():
    try:
        os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[
                logging.FileHandler(LOG_PATH, encoding="utf-8"),
                logging.StreamHandler(sys.stderr),
            ],
        )
    except Exception:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def run_once(dry_run=False, topic_override="", config_path=None, use_blocked=False,
             drafts_dir=None, archive_dir=None):
    """每日主流程。返回 {"ok", "topic", "title", "chars", "quality", "paths", "errors"}。

    任何关键步骤失败都不写草稿、不记录历史（失败降级原则）。
    drafts_dir/archive_dir：指定草稿箱与存档目录（鲸语工具调用时指向工作区，
    用户从产物面板/草稿箱即可看到，无需到代码目录翻找）。
    """
    cfg = cfg_mod.load_config(config_path)
    errors = []
    try:
        logger.info("=== WeChat Writer 开始（dry_run=%s）===", dry_run)
        # 1 采集
        items = src_mod.collect_all(cfg, use_blocked=use_blocked)
        if not items:
            return {"ok": False, "topic": "", "title": "", "chars": 0,
                    "quality": {"passed": False, "reasons": ["当日无素材（RSS 与搜索均无结果）"]},
                    "paths": {}, "errors": errors + ["当日无素材"]}
        logger.info("采集到 %d 条素材", len(items))

        # 2 选题（含历史去重）
        hist_topics = hist_mod.topics(HISTORY_PATH)
        hist_titles = hist_mod.titles(HISTORY_PATH)
        if topic_override:
            topic = topic_mod.Topic(str(topic_override).strip(), "（用户指定主题）")
        else:
            topic = topic_mod.pick_topic(items, hist_topics)
        logger.info("选题：%s（降级=%s）", topic.name, topic.fallback)

        # 3 写作（大纲 → 正文 → 润色）
        article = w_mod.write_article(topic, items, cfg["style"])
        logger.info("初稿完成：%s（%d 字）", article.title, q_mod._count_chars(article.content))

        # 4 质检 + 重试
        report = q_mod.check(article, cfg, hist_topics, hist_titles)
        for _ in range(cfg["quality"].get("max_retry", 1)):
            if report.passed:
                break
            logger.warning("质检未过：%s；重写修正", report.reasons)
            article = w_mod.rewrite_fix(article, report.reasons, cfg["style"])
            report = q_mod.check(article, cfg, hist_topics, hist_titles)
        if not report.passed:
            return {"ok": False, "topic": topic.name, "title": article.title,
                    "chars": report.chars,
                    "quality": {"passed": False, "reasons": report.reasons, "score": report.score},
                    "paths": {}, "errors": errors + report.reasons}

        # 5 输出 + 记录历史
        if dry_run:
            paths = {}
        else:
            paths = out_mod.save_article(article, cfg, data_dir=DATA_DIR,
                                         drafts_dir=drafts_dir, archive_dir=archive_dir)
            hist_mod.add(HISTORY_PATH, {
                "date": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
                "topic": topic.name,
                "title": article.title,
                "keywords": ",".join(article.candidates or [])[:200],
                "path": paths.get("archive_path", ""),
            })
            logger.info("已保存：%s", paths.get("draft_path"))
        return {"ok": True, "topic": topic.name, "title": article.title,
                "chars": report.chars,
                "quality": {"passed": True, "score": report.score},
                "paths": paths, "errors": errors}
    except Exception as e:
        logger.exception("主流程异常")
        return {"ok": False, "topic": "", "title": "", "chars": 0,
                "quality": {"passed": False, "reasons": [str(e)]},
                "paths": {}, "errors": errors + [str(e)]}


def run_daily(notify_cb=None):
    """方式 B 独立调度（配合 schedule 或线程定时器）。返回 run_once 结果并通知。"""
    result = run_once()
    if notify_cb:
        try:
            notify_cb(result)
        except Exception:
            logger.exception("通知回调失败")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="鲸语 · 公众号自动写作工具")
    parser.add_argument("--dry-run", action="store_true", help="预览：只打印选题/标题/质检，不写任何文件")
    parser.add_argument("--run", action="store_true", help="正式运行：写入草稿箱与存档")
    parser.add_argument("--topic", default="", help="指定主题，跳过自动选题")
    args = parser.parse_args(argv)

    _init_logging()
    dry = not args.run  # 默认 dry-run（安全）
    result = run_once(dry_run=dry, topic_override=args.topic)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
