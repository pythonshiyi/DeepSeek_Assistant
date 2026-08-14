# -*- coding: utf-8 -*-
"""WeChat Writer（公众号自动写作）单元测试：mock LLM 与网络，覆盖主路径与错误路径。"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wechat_writer import config as cfg_mod
from wechat_writer import history as hist_mod
from wechat_writer import llm as llm_mod
from wechat_writer import main as ww_main
from wechat_writer import output as out_mod
from wechat_writer import quality as q_mod
from wechat_writer import sources as src_mod
from wechat_writer import topic as topic_mod
from wechat_writer import writer as w_mod


def _item(title="OpenAI 发布新模型", url="https://example.com/1", source="机器之心", published="2026-08-11 09:00"):
    return src_mod.Item(title=title, url=url, summary="模型性能大幅提升，成本下降", source=source, published=published)


def _article(title="测试标题", content=None, topic="测试主题"):
    content = content or (
        "# 测试标题\n\n导语内容。\n\n## 第一节\n" + "这是第一节内容，足够长。" * 60 + "\n\n"
        "## 第二节\n" + "这是第二节内容，足够长。" * 60 + "\n\n"
        "## 参考资料\n- 来源：https://example.com/1\n"
    )
    return SimpleNamespace(topic=topic, title=title, content=content, outline="{}", candidates=["a"])


class TestConfig(unittest.TestCase):
    def test_defaults_no_file(self):
        cfg = cfg_mod.load_config("/nonexistent/config.json")
        self.assertEqual(cfg["schedule"], "0 9 * * *")
        self.assertEqual(cfg["style"]["min_chars"], 1200)
        self.assertEqual(cfg["quality"]["max_retry"], 1)

    def test_broken_file_falls_back(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "c.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{broken")
        cfg = cfg_mod.load_config(p)
        self.assertEqual(cfg["topic_domain"], "AI")
        shutil.rmtree(tmp, ignore_errors=True)

    def test_clamped_values(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "c.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"quality": {"similarity_threshold": 5, "max_retry": 99},
                       "style": {"min_chars": 100, "max_chars": 9000},
                       "sources": {"max_candidates": 9999}}, f)
        cfg = cfg_mod.load_config(p)
        self.assertLessEqual(cfg["quality"]["similarity_threshold"], 0.99)
        self.assertEqual(cfg["quality"]["max_retry"], 3)
        self.assertEqual(cfg["style"]["min_chars"], 500)
        self.assertEqual(cfg["sources"]["max_candidates"], 200)
        shutil.rmtree(tmp, ignore_errors=True)


class TestSources(unittest.TestCase):
    def test_collect_rss_empty(self):
        self.assertEqual(src_mod.collect_rss([], since_hours=24), [])
        self.assertEqual(src_mod.collect_rss(None), [])

    def test_collect_rss_single_source_failure_skipped(self):
        def boom(*a, **k):
            raise ConnectionError("网络失败")

        fp = mock.MagicMock()
        fp.parse.side_effect = boom
        with mock.patch.dict(sys.modules, {"feedparser": fp}):
            items = src_mod.collect_rss(["https://example.com/feed"], since_hours=24)
        self.assertEqual(items, [])

    def test_collect_rss_hanging_source_times_out(self):
        """真实缺陷回归：慢源/DNS 挂起必须被超时截断（feedparser 内部无超时）。"""
        import threading as _t

        real_parse_holder = {}

        def hanging_parse(*a, **k):
            # 模拟永不返回的解析（内部线程被 join 超时后丢弃）
            real_parse_holder["started"] = True
            _t.Event().wait(30)
            return None

        fp = mock.MagicMock()
        fp.parse.side_effect = hanging_parse
        with mock.patch.dict(sys.modules, {"feedparser": fp}):
            t0 = time.time()
            items = src_mod.collect_rss(["https://example.com/hang"], since_hours=24, timeout=1)
        self.assertEqual(items, [])
        self.assertLess(time.time() - t0, 8)  # 1s 超时 + 线程池余量，不能无限挂

    def test_collect_rss_parses_entries(self):
        entry = SimpleNamespace(
            title="AI 新闻", link="https://example.com/a",
            summary="<p>摘要</p>", published_parsed=None, updated_parsed=None,
        )
        feed = SimpleNamespace(entries=[entry], feed=SimpleNamespace(title="示例源"))
        fp = mock.MagicMock()
        fp.parse.return_value = feed
        with mock.patch.dict(sys.modules, {"feedparser": fp}):
            items = src_mod.collect_rss(["https://example.com/feed"], since_hours=24)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "AI 新闻")
        self.assertEqual(items[0].source, "示例源")

    def test_fetch_full_text_failure_keeps_item(self):
        it = _item()
        fake = mock.MagicMock()
        fake.get.side_effect = ConnectionError("x")
        with mock.patch.dict(sys.modules, {"httpx": fake}):
            out = src_mod.fetch_full_text(it)
        self.assertFalse(out.fetched)
        self.assertEqual(out.summary, it.summary)

    def test_keyword_filter_keeps_relevant(self):
        # 造 6 条素材（≥MIN_RELEVANT_KEEP=5 相关才启用过滤）：5 条 AI 相关 + 2 条无关
        items = [
            _item("OpenAI 发布新模型", "https://e.com/1"),
            _item("大模型推理成本下降", "https://e.com/2"),
            _item("Claude 新功能评测", "https://e.com/3"),
            _item("英伟达发布新芯片", "https://e.com/4"),
            _item("AI Agent 开源工具盘点", "https://e.com/5"),
            _item("某手机新品发布", "https://e.com/6"),
            _item("游戏评测：任天堂新作", "https://e.com/7"),
        ]
        kws = cfg_mod.DEFAULT_CONFIG["sources"]["topic_keywords"]
        out = src_mod.filter_by_keywords(items, kws)
        self.assertEqual(len(out), 5)  # 只保留 AI 相关
        self.assertTrue(all("OpenAI" in i.title or "大模型" in i.title or "Claude" in i.title
                            or "英伟达" in i.title or "Agent" in i.title for i in out))

    def test_keyword_filter_small_corpus_keeps_all(self):
        # 相关素材不足 5 条时宁多勿缺（放行全部）
        items = [_item("OpenAI 发布新模型"), _item("手机评测")]
        kws = cfg_mod.DEFAULT_CONFIG["sources"]["topic_keywords"]
        self.assertEqual(len(src_mod.filter_by_keywords(items, kws)), 2)

    def test_ascii_keyword_word_boundary(self):
        # 'ai' 不应命中 'said'/'email'（词边界）；'AI' 应命中 'AI 大模型'
        self.assertFalse(src_mod._kw_hit("he said hello", "ai"))
        self.assertFalse(src_mod._kw_hit("my email is x", "ai"))
        self.assertTrue(src_mod._kw_hit("AI 大模型发布", "ai"))
        self.assertTrue(src_mod._kw_hit("gpt-5 发布", "gpt"))

    def test_expand_rss_groups(self):
        cfg = cfg_mod.load_config()
        urls = src_mod.expand_rss(cfg)
        self.assertGreaterEqual(len(urls), 8)  # 分组展开后 ≥8 个信源
        self.assertIn("https://www.jiqizhixin.com/rss", urls)
        self.assertIn("https://hnrss.org/frontpage", urls)

    def test_expand_rss_user_override(self):
        cfg = cfg_mod.load_config()
        cfg["sources"]["rss"] = ["https://example.com/a"]
        self.assertEqual(src_mod.expand_rss(cfg), ["https://example.com/a"])


class TestTopic(unittest.TestCase):
    def test_empty_items_fallback(self):
        t = topic_mod.pick_topic([], [], llm_chat=lambda *a, **k: "[]")
        self.assertTrue(t.fallback)
        self.assertIn("盘点", t.name)

    def test_llm_failure_fallback(self):
        items = [_item()]
        with mock.patch.object(llm_mod, "chat", side_effect=RuntimeError("API 挂了")):
            t = topic_mod.pick_topic(items, [])
        self.assertTrue(t.fallback)

    def test_jaccard_filters_duplicate(self):
        # 与历史完全同主题 → 粗筛剔除
        items = [_item("OpenAI 发布新模型", "https://e.com/1")]
        hist = ["OpenAI 发布新模型"]
        fake_llm = mock.MagicMock(side_effect=[
            '[{"name": "OpenAI 发布新模型", "angle": "性能", "related": [0]}]',  # 候选
            '{"duplicate": false}',  # 精判（不应到达：粗筛已剔除）
        ])
        with mock.patch.object(llm_mod, "chat", fake_llm):
            t = topic_mod.pick_topic(items, hist)
        self.assertTrue(t.fallback)  # 全部被剔除 → 降级

    def test_llm_judge_removes_synonym_rewrite(self):
        """关键回归：同义改写粗筛可能放过，必须 LLM 精判剔除。"""
        items = [_item("OpenAI 发布新模型", "https://e.com/1")]
        hist = ["OpenAI 新模型发布后的影响"]  # 与候选 bigram 相似度低（粗筛放过）
        calls = []

        def fake_llm(messages, **kw):
            text = messages[-1]["content"]
            calls.append(text[:40])
            if "提炼" in text or "选题" in text and "判断" not in text:
                return '[{"name": "OpenAI 发布新模型", "angle": "影响", "related": [0]}]'
            if "判断以下候选" in text:
                return '{"duplicate": true, "reason": "换汤不换药"}'
            return "[]"

        with mock.patch.object(llm_mod, "chat", side_effect=fake_llm):
            t = topic_mod.pick_topic(items, hist)
        self.assertTrue(t.fallback)  # LLM 精判剔除后降级
        self.assertTrue(any("判断以下候选" in c for c in calls), "必须走到 LLM 精判通道")


class TestQuality(unittest.TestCase):
    def test_pass(self):
        cfg = cfg_mod.load_config()
        report = q_mod.check(_article(), cfg)
        self.assertTrue(report.passed, report.reasons)
        self.assertEqual(report.score, 100)

    def test_too_short(self):
        cfg = cfg_mod.load_config()
        a = _article(content="## 甲\n太短\n## 乙\n太短\n## 参考资料\n- x")
        report = q_mod.check(a, cfg)
        self.assertFalse(report.passed)
        self.assertTrue(any("字数不足" in r for r in report.reasons))

    def test_no_sources(self):
        cfg = cfg_mod.load_config()
        a = _article(content="导语。\n\n## 甲\n" + "内容" * 300 + "\n## 乙\n" + "内容" * 300)
        report = q_mod.check(a, cfg)
        self.assertFalse(report.passed)
        self.assertTrue(any("来源" in r for r in report.reasons))

    def test_sensitive_word(self):
        cfg = cfg_mod.load_config()
        cfg["quality"]["sensitive_words"] = ["赌博"]
        a = _article(content="导语含赌博字眼。\n\n## 甲\n" + "内容" * 300 + "\n## 乙\n" + "内容" * 300
                     + "\n## 参考资料\n- x")
        report = q_mod.check(a, cfg)
        self.assertFalse(report.passed)
        self.assertTrue(any("敏感词" in r for r in report.reasons))

    def test_history_dup_coarse(self):
        cfg = cfg_mod.load_config()
        report = q_mod.check(_article(), cfg, history_topics=["测试主题"])
        self.assertFalse(report.passed)
        self.assertTrue(any("重复" in r for r in report.reasons))


class TestWriter(unittest.TestCase):
    def test_build_outline_parses_json(self):
        fake = mock.MagicMock(return_value=(
            '{"titles": ["t1", "t2", "t3"], "lead": "导语", '
            '"sections": [{"h": "第一节", "k": "要点"}], "conclusion": "结论"}'
        ))
        outline = w_mod.build_outline(topic_mod.Topic("主题"), [_item()], cfg_mod.load_config()["style"], llm_chat=fake)
        self.assertEqual(len(outline["titles"]), 3)
        self.assertEqual(outline["sections"][0]["h"], "第一节")

    def test_write_article_three_stages(self):
        style = cfg_mod.load_config()["style"]
        calls = []

        def fake_llm(messages, **kw):
            text = messages[-1]["content"]
            calls.append(text[:20])
            if "大纲" in text:
                return '{"titles": ["悬念标题", "数字标题", "价值标题"], "lead": "导语", "sections": [{"h": "一", "k": "a"}, {"h": "二", "k": "b"}], "conclusion": "c"}'
            if "润色" in text:
                return "## 悬念标题\n\n导语。\n\n## 一\n内容一\n\n## 二\n内容二\n\n## 参考资料\n- src"
            return "## 悬念标题\n\n初稿正文。\n\n## 一\n内容\n\n## 二\n内容\n\n## 参考资料\n- src"

        article = w_mod.write_article(topic_mod.Topic("主题"), [_item()], style, llm_chat=fake_llm)
        self.assertEqual(len(calls), 3)  # 大纲 → 正文 → 润色
        self.assertEqual(article.title, "悬念标题")

    def test_rewrite_fix_keeps_title(self):
        a = _article()
        fake = mock.MagicMock(return_value="## 原标题\n\n修正后内容。\n\n## 参考资料\n- src")
        fixed = w_mod.rewrite_fix(a, ["字数不足"], cfg_mod.load_config()["style"], llm_chat=fake)
        self.assertEqual(fixed.title, "测试标题")
        self.assertIn("修正后内容", fixed.content)


class TestOutput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_ww_")
        self.cfg = cfg_mod.load_config()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_no_files(self):
        paths = out_mod.save_article(_article(), self.cfg, drafts_dir=os.path.join(self.tmp, "d"),
                                     archive_dir=os.path.join(self.tmp, "a"), dry_run=True)
        self.assertEqual(paths["draft_path"], "")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "d")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "a")))

    def test_real_save_format(self):
        self.cfg["output"]["save_html"] = False
        paths = out_mod.save_article(_article(), self.cfg, drafts_dir=os.path.join(self.tmp, "d"),
                                     archive_dir=os.path.join(self.tmp, "a"))
        self.assertTrue(os.path.exists(paths["draft_path"]))
        with open(paths["draft_path"], encoding="utf-8") as f:
            content = f.read()
        self.assertTrue(content.startswith("# 测试标题\n\n"))  # publish_draft 兼容格式
        self.assertIn("参考资料", content)
        self.assertTrue(os.path.exists(paths["archive_path"]))
        with open(paths["archive_path"], encoding="utf-8") as f:
            self.assertIn("- 主题：测试主题", f.read())


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_h_")
        self.path = os.path.join(self.tmp, "history.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_recent_topics(self):
        for i in range(3):
            hist_mod.add(self.path, {"date": f"2026-08-{i+1:02d}", "topic": f"主题{i}", "title": f"标题{i}", "keywords": "", "path": ""})
        self.assertEqual(len(hist_mod.recent(self.path, 14)), 3)
        self.assertIn("主题1", hist_mod.topics(self.path))
        recent2 = hist_mod.recent(self.path, 2)
        self.assertEqual(recent2[0]["topic"], "主题2")  # 时间倒序

    def test_empty(self):
        self.assertEqual(hist_mod.recent(self.path), [])


class TestMainRunOnce(unittest.TestCase):
    """run_once 端到端（mock 采集与 LLM，真实执行编排/质检/输出）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_run_")
        ww_main.DATA_DIR = self.tmp
        ww_main.HISTORY_PATH = os.path.join(self.tmp, "history.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_items(self):
        return [_item(), _item("AI Agent 工具盘点", "https://e.com/2", "36氪")]

    def _fake_llm(self, messages, **kw):
        text = messages[-1]["content"]
        if "提炼" in text:
            return '[{"name": "AI Agent 工具盘点", "angle": "效率", "related": [0, 1]}]'
        if "大纲" in text:
            return '{"titles": ["悬念标题", "数字标题", "价值标题"], "lead": "导语", "sections": [{"h": "一", "k": "a"}, {"h": "二", "k": "b"}], "conclusion": "c"}'
        if "润色" in text:
            return "## 悬念标题\n\n导语。\n\n## 一\n" + "内容甲" * 300 + "\n## 二\n" + "内容乙" * 300 + "\n## 参考资料\n- " + self._fake_items()[0].url
        if "判断以下候选" in text or "判断以下文章" in text:
            return '{"duplicate": false}'
        if "质检意见" in text:
            return "## 悬念标题\n\n修正内容。\n\n## 一\n" + "内容甲" * 300 + "\n## 二\n" + "内容乙" * 300 + "\n## 参考资料\n- x"
        return "## 悬念标题\n\n正文。\n\n## 一\n" + "内容甲" * 300 + "\n## 二\n" + "内容乙" * 300 + "\n## 参考资料\n- x"

    def test_success_dry_run(self):
        with mock.patch("wechat_writer.main.src_mod.collect_all", return_value=self._fake_items()), \
             mock.patch.object(llm_mod, "chat", side_effect=self._fake_llm):
            result = ww_main.run_once(dry_run=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["title"], "悬念标题")
        self.assertGreaterEqual(result["chars"], 600)
        self.assertEqual(result["paths"], {})

    def test_success_real_writes_and_history(self):
        with mock.patch("wechat_writer.main.src_mod.collect_all", return_value=self._fake_items()), \
             mock.patch.object(llm_mod, "chat", side_effect=self._fake_llm):
            result = ww_main.run_once(dry_run=False)
        self.assertTrue(result["ok"], result)
        self.assertTrue(os.path.exists(result["paths"]["draft_path"]))
        self.assertTrue(os.path.exists(result["paths"]["archive_path"]))
        self.assertTrue(os.path.exists(ww_main.HISTORY_PATH))
        rec = json.load(open(ww_main.HISTORY_PATH, encoding="utf-8"))["items"]
        self.assertEqual(rec[-1]["title"], "悬念标题")

    def test_no_material_fails_without_writing(self):
        with mock.patch("wechat_writer.main.src_mod.collect_all", return_value=[]):
            result = ww_main.run_once(dry_run=False)
        self.assertFalse(result["ok"])
        self.assertIn("无素材", result["errors"][0])
        self.assertFalse(os.path.exists(ww_main.HISTORY_PATH))


class TestRegisteredTool(unittest.TestCase):
    def test_tool_registered(self):
        import deepseek_client as dc

        names = [t["function"]["name"] for t in dc.TOOLS]
        self.assertIn("run_wechat_writer", names)
        self.assertIn("run_wechat_writer", dc.TOOL_CALL_MAP)

    def test_tool_wraps_run_once(self):
        import deepseek_client as dc

        # dc.run_wechat_writer 内 `from wechat_writer import run_once` 绑定的是
        # wechat_writer/__init__.py 的 run_once（导入时副本），必须 mock 该名字
        with mock.patch("wechat_writer.run_once",
                        return_value={"ok": True, "topic": "t", "title": "标题", "chars": 1500,
                                      "quality": {"score": 95}, "paths": {"draft_path": "d.md"}}):
            out = dc.run_wechat_writer()
        self.assertIn("✅", out)
        self.assertIn("标题", out)
        self.assertIn("d.md", out)

    def test_tool_error_summary(self):
        import deepseek_client as dc

        with mock.patch("wechat_writer.run_once",
                        return_value={"ok": False, "quality": {"reasons": ["当日无素材"]}}):
            out = dc.run_wechat_writer()
        self.assertIn("未完成", out)
        self.assertIn("无素材", out)


if __name__ == "__main__":
    unittest.main()
