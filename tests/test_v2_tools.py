# -*- coding: utf-8 -*-
"""v2 能力层回归测试：任务调度 / 文件闭环 / 知识库 RAG / 数据库写 / 检查点 / 洞察 / 长结果落盘。"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

import main as m
import deepseek_client as dc
import permissions


class TestScheduleTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_sched_")
        dc.SCHEDULES_FILE = os.path.join(self.tmp, "schedules.json")

    def tearDown(self):
        dc.SCHEDULES_FILE = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_cron(self):
        r = dc.schedule_task(expr_type="cron", expr="30 9 * * 1", content="生成周报", name="周报")
        self.assertIn("已创建", r)
        data = json.load(open(dc.SCHEDULES_FILE, encoding="utf-8"))
        self.assertEqual(data[0]["cron"], "30 9 * * 1")
        self.assertEqual(data[0]["action"], "message")
        self.assertEqual(data[0]["name"], "周报")

    def test_create_time_and_every(self):
        dc.schedule_task(expr_type="time", expr="09:00", content="早报提醒", action="notify")
        dc.schedule_task(expr_type="every", expr="30", content="巡检")
        data = json.load(open(dc.SCHEDULES_FILE, encoding="utf-8"))
        self.assertEqual(data[0]["time"], "09:00")
        self.assertEqual(data[0]["action"], "notify")
        self.assertEqual(data[1]["every"], 30)

    def test_validation(self):
        self.assertIn("错误", dc.schedule_task(expr_type="cron", expr="bad expr", content="x"))
        self.assertIn("错误", dc.schedule_task(expr_type="time", expr="25:99", content="x"))
        self.assertIn("错误", dc.schedule_task(expr_type="every", expr="0", content="x"))
        self.assertIn("错误", dc.schedule_task(expr_type="cron", expr="30 9 * * 1", content=""))
        self.assertIn("错误", dc.schedule_task(expr_type="cron", expr="30 9 * * 1", content="x", action="fly"))

    def test_list_and_cancel(self):
        dc.schedule_task(expr_type="cron", expr="30 9 * * 1", content="A", name="甲")
        dc.schedule_task(expr_type="cron", expr="0 9 * * 2", content="B", name="乙")
        out = dc.list_schedules()
        self.assertIn("甲", out)
        self.assertIn("乙", out)
        self.assertIn("共 2", out)
        r = dc.cancel_schedule("甲")
        self.assertIn("已取消", r)
        out2 = dc.list_schedules()
        self.assertNotIn("甲", out2)
        self.assertIn("乙", out2)
        self.assertIn("错误", dc.cancel_schedule("不存在"))

    def test_cancel_by_id(self):
        dc.schedule_task(expr_type="every", expr="10", content="x")
        sid = json.load(open(dc.SCHEDULES_FILE, encoding="utf-8"))[0]["id"]
        self.assertIn("已取消", dc.cancel_schedule(sid))
        self.assertEqual(json.load(open(dc.SCHEDULES_FILE, encoding="utf-8")), [])


class TestFileLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_fl_")
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws, exist_ok=True)
        permissions.init(os.path.join(self.tmp, "perm.json"), ws)
        data = permissions.get_data()
        data["filesystem"]["blocked_dirs"] = [
            d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
        ]
        permissions.set_full_auto(True)
        self.ws = ws

    def tearDown(self):
        permissions.set_full_auto(False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_archive_extract_roundtrip(self):
        src1 = os.path.join(self.ws, "a.txt")
        src2 = os.path.join(self.ws, "b.txt")
        with open(src1, "w", encoding="utf-8") as f:
            f.write("内容A")
        with open(src2, "w", encoding="utf-8") as f:
            f.write("内容B")
        zip_path = os.path.join(self.ws, "out.zip")
        r = dc.archive_files([src1, src2], zip_path)
        self.assertIn("已打包", r)
        self.assertTrue(os.path.exists(zip_path))
        dest = os.path.join(self.ws, "unpacked")
        r2 = dc.extract_archive(zip_path, dest)
        self.assertIn("已解压", r2)
        self.assertEqual(open(os.path.join(dest, "a.txt"), encoding="utf-8").read(), "内容A")

    def test_archive_rejects_traversal(self):
        import zipfile

        evil = os.path.join(self.ws, "evil.zip")
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../../escape.txt", "bad")
        dest = os.path.join(self.ws, "out")
        r = dc.extract_archive(evil, dest)
        self.assertIn("越界", r)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))

    def test_batch_rename_dry_and_real(self):
        for i in range(3):
            with open(os.path.join(self.ws, f"report_{i}.md"), "w", encoding="utf-8") as f:
                f.write("x")
        r = dc.batch_rename(self.ws, "report_", "draft_", dry_run=True)
        self.assertIn("预览", r)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "report_0.md")))
        r2 = dc.batch_rename(self.ws, "report_", "draft_")
        self.assertIn("3 个文件", r2)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "draft_0.md")))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "report_0.md")))

    def test_delete_permanent(self):
        p = os.path.join(self.ws, "del.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        r = dc.delete_file(p, permanent=True)
        self.assertIn("已物理删除", r)
        self.assertFalse(os.path.exists(p))

    def test_delete_recycle_bin_fallback(self):
        p = os.path.join(self.ws, "del2.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        r = dc.delete_file(p)  # 默认回收站（测试环境可能走物理兜底）
        self.assertIn("已", r)
        self.assertFalse(os.path.exists(p))

    def test_delete_outside_allowed(self):
        permissions.set_full_auto(False)
        p = os.path.join(self.tmp, "nope.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        r = dc.delete_file(p)
        self.assertIn("权限拒绝", r)


class TestKnowledgeRAG(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_kb_")
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws, exist_ok=True)
        permissions.init(os.path.join(self.tmp, "perm.json"), ws)
        data = permissions.get_data()
        data["filesystem"]["blocked_dirs"] = [
            d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
        ]
        permissions.set_full_auto(True)
        dc.KNOWLEDGE_INDEX_FILE = os.path.join(self.tmp, "knowledge_index.json")
        self.ws = ws

    def tearDown(self):
        permissions.set_full_auto(False)
        dc.KNOWLEDGE_INDEX_FILE = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_index_and_semantic_search(self):
        with open(os.path.join(self.ws, "预算.md"), "w", encoding="utf-8") as f:
            f.write("2026 年市场部预算为五十万元，用于广告投放与线下活动。")
        with open(os.path.join(self.ws, "备份.md"), "w", encoding="utf-8") as f:
            f.write("数据备份策略：每日凌晨全量备份到异地机房。")
        r = dc.knowledge_index(self.ws)
        self.assertIn("已索引 2", r)
        out = dc.knowledge_search("今年投了多少钱做广告")
        self.assertIn("预算", out)
        self.assertNotIn("备份策略", out)
        out2 = dc.knowledge_search("数据安全")
        self.assertIn("备份", out2)

    def test_search_without_index(self):
        dc.KNOWLEDGE_INDEX_FILE = None
        self.assertIn("尚未建立索引", dc.knowledge_search("x"))

    def test_index_empty_dir(self):
        empty = os.path.join(self.ws, "empty")
        os.makedirs(empty, exist_ok=True)
        self.assertIn("错误", dc.knowledge_index(empty))

    def test_index_incremental_reuse(self):
        """增量建索引：未变化的文档复用旧文本（mtime/size 相同）。"""
        f = os.path.join(self.ws, "增量.md")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("第一次内容：预算二十万")
        r1 = dc.knowledge_index(self.ws)
        self.assertIn("已索引 1", r1)
        r2 = dc.knowledge_index(self.ws)
        self.assertIn("复用 1", r2)
        # 修改文件后重建 → 不再复用
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("第二次内容：预算五十万")
        r3 = dc.knowledge_index(self.ws)
        self.assertIn("新增 1", r3)
        out = dc.knowledge_search("五十万")
        self.assertIn("预算", out)


class TestDatabaseExecute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_dbw_")
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws, exist_ok=True)
        permissions.init(os.path.join(self.tmp, "perm.json"), ws)
        data = permissions.get_data()
        data["filesystem"]["blocked_dirs"] = [
            d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
        ]
        permissions.set_full_auto(True)

    def tearDown(self):
        permissions.set_full_auto(False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sqlite_write_with_backup(self):
        import sqlite3

        db = os.path.join(self.tmp, "ws", "test.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO users VALUES (1, '张三')")
        conn.execute("INSERT INTO users VALUES (2, '李四')")
        conn.commit()
        conn.close()
        r = dc.database_execute(db_type="sqlite", connection=db, sql="UPDATE users SET name='王五' WHERE id=1")
        self.assertIn("已执行", r)
        self.assertIn("变更预览：命中 1 行", r)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT name FROM users WHERE id=1").fetchone()
        conn.close()
        self.assertEqual(row[0], "王五")
        backups = os.listdir(os.path.join(self.tmp, "ws", "db_backups"))
        self.assertTrue(any(b.endswith(".bak") for b in backups))

    def test_rejects_readonly_sql(self):
        db = os.path.join(self.tmp, "test2.db")
        import sqlite3

        sqlite3.connect(db).close()
        self.assertIn("错误", dc.database_execute(db_type="sqlite", connection=db, sql="SELECT 1"))


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_cp_")
        dc.CHECKPOINT_FILE = os.path.join(self.tmp, "checkpoint.json")

    def tearDown(self):
        dc.CHECKPOINT_FILE = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_load(self):
        r = dc.task_checkpoint_save(
            name="搭建网站", status="进行中",
            pending=["创建 index.html", "启动服务器", "截图验证"],
            notes="已完成目录结构",
        )
        self.assertIn("已保存", r)
        out = dc.task_checkpoint_load()
        self.assertIn("搭建网站", out)
        self.assertIn("启动服务器", out)

    def test_auto_checkpoint_flag(self):
        """自动断点：auto=True 落盘标记；load 显示标注；正常完成 clear 清除。"""
        r = dc.task_checkpoint_save(name="自动任务", status="进行中", auto=True)
        self.assertIn("已保存", r)
        data = json.load(open(dc.CHECKPOINT_FILE, encoding="utf-8"))
        self.assertTrue(data.get("auto"))
        out = dc.task_checkpoint_load()
        self.assertIn("自动断点", out)
        # 正常完成 → 自动断点被清除
        self.assertIn("已清除", dc.task_checkpoint_clear())
        self.assertIn("没有任务检查点", dc.task_checkpoint_load())

    def test_manual_checkpoint_not_cleared(self):
        """手动断点：task_checkpoint_clear 不删除。"""
        dc.task_checkpoint_save(name="手动任务", status="进行中")
        self.assertIn("手动保存", dc.task_checkpoint_clear())
        self.assertIn("手动任务", dc.task_checkpoint_load())

    def test_load_empty(self):
        self.assertIn("没有任务检查点", dc.task_checkpoint_load())


class TestUsageReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_ur_")
        dc.STATS_FILE = os.path.join(self.tmp, "stats.json")
        data = {
            "2026-08-05": {
                "deepseek-v4-flash": {"prompt": 10000, "completion": 2000, "cache_hit": 8000, "cache_miss": 2000}
            }
        }
        with open(dc.STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def tearDown(self):
        dc.STATS_FILE = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_report(self):
        import stats as stats_mod

        stats_mod.PRICING  # 引用确保模块可加载
        out = dc.usage_report(days=90)
        self.assertIn("用量报告", out)
        self.assertIn("10,000", out)
        self.assertIn("deepseek-v4-flash", out)

    def test_no_data(self):
        dc.STATS_FILE = os.path.join(self.tmp, "none.json")
        self.assertIn("暂无", dc.usage_report())


class TestDailyBrief(unittest.TestCase):
    """每日简报：采集 → LLM 提炼 → 落盘（mock 采集与客户端，真实执行编排）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_brief_")
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws, exist_ok=True)
        permissions.init(os.path.join(self.tmp, "perm.json"), ws)
        data = permissions.get_data()
        data["filesystem"]["blocked_dirs"] = [
            d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
        ]
        permissions.set_full_auto(True)
        self.ws = ws

    def tearDown(self):
        permissions.set_full_auto(False)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_items(self):
        from types import SimpleNamespace

        return [
            SimpleNamespace(title="OpenAI 发布新模型", url="https://e.com/1",
                            source="机器之心", summary="性能大幅提升", published="2026-08-13 09:00",
                            fetched=True, full_text=""),
            SimpleNamespace(title="AI Agent 工具盘点", url="https://e.com/2",
                            source="量子位", summary="开源工具汇总", published="2026-08-13 10:00",
                            fetched=True, full_text=""),
        ]

    def _fake_client(self, brief_text):
        class _Msg:
            content = brief_text

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        class _Completions:
            def create(self, **kw):
                return _Resp()

        class _Client:
            model = "deepseek-v4-flash"

            class client:
                chat = type("Chat", (), {"completions": _Completions()})()

        return _Client()

    def test_tool_registered(self):
        names = [t["function"]["name"] for t in dc.TOOLS]
        self.assertIn("daily_brief", names)
        self.assertIn("daily_brief", dc.TOOL_CALL_MAP)

    def test_generate_and_persist(self):
        brief = "## 今日 AI 动态\n\n- OpenAI 发布新模型：值得关注\n\n## 今日趋势\n国产模型加速追赶。"
        old = dc._CLIENT_HOLDER["client"]
        try:
            dc._CLIENT_HOLDER["client"] = self._fake_client(brief)
            with mock.patch("wechat_writer.sources.collect_all", return_value=self._fake_items()):
                out = dc.daily_brief()
        finally:
            dc._CLIENT_HOLDER["client"] = old
        self.assertIn("今日简报", out)
        self.assertIn("OpenAI 发布新模型", out)
        # 落盘工作区 briefs/
        briefs = os.path.join(self.ws, "briefs")
        files = os.listdir(briefs) if os.path.isdir(briefs) else []
        self.assertTrue(any(f.startswith("brief_") and f.endswith(".md") for f in files))

    def test_topic_filter(self):
        brief = "## 过滤后简报"
        old = dc._CLIENT_HOLDER["client"]
        try:
            dc._CLIENT_HOLDER["client"] = self._fake_client(brief)
            with mock.patch("wechat_writer.sources.collect_all", return_value=self._fake_items()):
                out = dc.daily_brief(topic="不存在关键词xyz")
        finally:
            dc._CLIENT_HOLDER["client"] = old
        self.assertIn("暂无资讯素材", out)

    def test_no_client_error(self):
        old = dc._CLIENT_HOLDER["client"]
        try:
            dc._CLIENT_HOLDER["client"] = None
            with mock.patch("wechat_writer.sources.collect_all", return_value=self._fake_items()):
                out = dc.daily_brief()
        finally:
            dc._CLIENT_HOLDER["client"] = old
        self.assertIn("没有可用客户端", out)


class TestWechatToolDefaults(unittest.TestCase):
    """公众号写作能力默认可用（防再次被移出默认启用集/运行时不可见）。"""

    def test_builtin_defaults_include_wechat_tools(self):
        """标准模式默认启用集包含公众号工具（run_wechat_writer / publish_draft）。"""
        self.assertIn("run_wechat_writer", m.BUILTIN_TOOL_NAMES)
        self.assertIn("publish_draft", m.BUILTIN_TOOL_NAMES)

    def test_normalize_merges_wechat_tools(self):
        """旧配置升级：缺失的公众号工具被自动补入 enabled_tools。"""
        cfg = m.DEFAULT_CONFIG.copy()
        cfg["enabled_tools"] = ["get_date", "fetch_url"]
        m.normalize_config(cfg)
        self.assertIn("run_wechat_writer", cfg["enabled_tools"])
        self.assertIn("publish_draft", cfg["enabled_tools"])

    def test_wechat_tool_in_schema_and_map(self):
        """工具 schema 与调用映射完整（模型可见、可执行）。"""
        names = [t["function"]["name"] for t in dc.TOOLS]
        self.assertIn("run_wechat_writer", names)
        self.assertIn("publish_draft", names)
        self.assertIn("run_wechat_writer", dc.TOOL_CALL_MAP)
        self.assertIn("publish_draft", dc.TOOL_CALL_MAP)
        # schema 合法：无 array 参数缺 items
        self.assertNotIn("array", dc.TOOLS[[i for i, t in enumerate(dc.TOOLS)
                                            if t["function"]["name"] == "run_wechat_writer"][0]]
                         ["function"]["parameters"]["properties"].get("dry_run", {}).get("type", "boolean"))


class TestLongResultPersist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_lr_")
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws, exist_ok=True)
        permissions.init(os.path.join(self.tmp, "perm.json"), ws)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_persist_replaces_truncation(self):
        big = "x" * (dc._RESULT_INTO_CONTEXT_MAX + 5000) + "ENDMARK"
        out = dc._persist_long_result("fetch_url", big)
        self.assertIn("已保存至", out)
        self.assertIn("ENDMARK", out)  # 结尾摘要保留关键内容
        self.assertLess(len(out), dc._RESULT_INTO_CONTEXT_MAX)
        # 文件真实存在
        d = os.path.join(permissions.WORKSPACE_DIR, "long_results")
        files = os.listdir(d)
        self.assertTrue(any(f.startswith("fetch_url_") for f in files))

    def test_short_result_untouched(self):
        out = dc._persist_long_result("x", "short")
        self.assertEqual(out, "short")


class TestValidationOnly(unittest.TestCase):
    """无环境依赖的校验路径（不触发真实硬件/网络）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_val_")
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws, exist_ok=True)
        permissions.init(os.path.join(self.tmp, "perm.json"), ws)
        data = permissions.get_data()
        data["filesystem"]["blocked_dirs"] = [
            d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
        ]
        permissions.set_full_auto(True)

    def tearDown(self):
        permissions.set_full_auto(False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clipboard_empty_paths(self):
        self.assertIn("错误", dc.clipboard_set(""))
        self.assertIn("错误", dc.notify_desktop(text=""))

    def test_screen_capture_invalid_path(self):
        r = dc.screen_capture(path=os.path.join(self.tmp, "ws", "cap.png"), area="abc,def")
        self.assertIn("已截屏", r)  # area 非法时静默降级为全屏
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "ws", "cap.png")))

    def test_image_understand_missing_file(self):
        self.assertIn("错误", dc.image_understand("/nonexistent/x.png"))

    def test_speech_to_text_missing_file(self):
        self.assertIn("错误", dc.speech_to_text("/nonexistent/a.wav"))

    def test_image_generate_no_key(self):
        dc.IMAGE_GEN_KEY = None
        self.assertIn("未配置", dc.image_generate("一只猫"))

    def test_database_execute_bad_type(self):
        r = dc.database_execute(db_type="oracle", connection="x", sql="UPDATE t SET a=1")
        self.assertIn("错误", r)

    def test_workflow_not_initialized(self):
        dc.WORKFLOWS_FILE = None
        self.assertIn("错误", dc.run_workflow("x"))

    def test_workflow_reentrancy_guard(self):
        """防重：已有流程运行时拒绝启动新流程。"""
        self.tmp2 = tempfile.mkdtemp(prefix="dsa_wf_")
        dc.WORKFLOWS_FILE = os.path.join(self.tmp2, "workflows.json")
        with open(dc.WORKFLOWS_FILE, "w", encoding="utf-8") as f:
            json.dump({"测试流程": {"steps": [{"text": "步骤一"}]}}, f, ensure_ascii=False)
        dc.set_send_callback(lambda t: None)
        dc.set_busy_provider(lambda: True)  # 模拟正在生成 → 步骤卡在等待
        dc._WORKFLOW_RUNNING = True
        try:
            r = dc.run_workflow("测试流程")
            self.assertIn("已有流程正在运行", r)
        finally:
            dc._WORKFLOW_RUNNING = False
            dc.WORKFLOWS_FILE = None
            import shutil as _sh

            _sh.rmtree(self.tmp2, ignore_errors=True)

    def test_workflow_runs_steps(self):
        """正常流程：返回启动摘要，步骤在后台执行。"""
        import time as _t

        self.tmp2 = tempfile.mkdtemp(prefix="dsa_wf2_")
        dc.WORKFLOWS_FILE = os.path.join(self.tmp2, "workflows.json")
        with open(dc.WORKFLOWS_FILE, "w", encoding="utf-8") as f:
            json.dump({"巡检": {"steps": [{"text": "检查磁盘"}, {"text": "汇报状态"}]}}, f, ensure_ascii=False)
        sent = []
        dc.set_send_callback(lambda t: sent.append(t))
        dc.set_busy_provider(lambda: False)
        try:
            r = dc.run_workflow("巡检")
            self.assertIn("启动流程「巡检」（2 步）", r)
            _t.sleep(4.5)  # 等待后台线程下发（含步骤间 2s 等待）
            self.assertIn("检查磁盘", sent)
            self.assertIn("汇报状态", sent)
        finally:
            dc.WORKFLOWS_FILE = None
            import shutil as _sh

            _sh.rmtree(self.tmp2, ignore_errors=True)

    def test_workflow_not_found(self):
        self.tmp2 = tempfile.mkdtemp(prefix="dsa_wf3_")
        dc.WORKFLOWS_FILE = os.path.join(self.tmp2, "workflows.json")
        with open(dc.WORKFLOWS_FILE, "w", encoding="utf-8") as f:
            json.dump({"A": {"steps": [{"text": "x"}]}}, f, ensure_ascii=False)
        dc.set_send_callback(lambda t: None)
        try:
            self.assertIn("未找到流程", dc.run_workflow("不存在"))
        finally:
            dc.WORKFLOWS_FILE = None
            import shutil as _sh

            _sh.rmtree(self.tmp2, ignore_errors=True)

    def test_workflow_recipe_step_injects_chain(self):
        """三层打通：流程步骤引用配方（patterns.json）→ 发送文本注入已验证工具链。"""
        self.tmp2 = tempfile.mkdtemp(prefix="dsa_wf4_")
        dc.WORKFLOWS_FILE = os.path.join(self.tmp2, "workflows.json")
        dc.PATTERNS_FILE = os.path.join(self.tmp2, "patterns.json")
        with open(dc.WORKFLOWS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "周报流程": {"steps": [
                    {"recipe": "周报生成", "text": "汇总本周工作"},
                    {"text": "再检查一遍"},
                ]}
            }, f, ensure_ascii=False)
        with open(dc.PATTERNS_FILE, "w", encoding="utf-8") as f:
            json.dump([{"name": "周报生成", "chain": ["read_file", "create_doc"]}], f, ensure_ascii=False)
        sent = []
        dc.set_send_callback(lambda t: sent.append(t))
        dc.set_busy_provider(lambda: False)
        try:
            r = dc.run_workflow("周报流程")
            self.assertIn("启动流程", r)
            time.sleep(5.5)  # 步骤间有 2s 间隔，等后台线程下发完
            self.assertEqual(len(sent), 2)
            self.assertIn("read_file → create_doc", sent[0])   # 配方链注入
            self.assertIn("汇总本周工作", sent[0])              # 任务目标保留
            self.assertEqual(sent[1], "再检查一遍")
        finally:
            dc.WORKFLOWS_FILE = None
            dc.PATTERNS_FILE = None
            import shutil as _sh

            _sh.rmtree(self.tmp2, ignore_errors=True)

    def test_workflow_recipe_missing_falls_back_to_text(self):
        """配方不存在时降级为纯指令（不阻断流程）。"""
        self.tmp2 = tempfile.mkdtemp(prefix="dsa_wf5_")
        dc.WORKFLOWS_FILE = os.path.join(self.tmp2, "workflows.json")
        dc.PATTERNS_FILE = os.path.join(self.tmp2, "patterns.json")
        with open(dc.WORKFLOWS_FILE, "w", encoding="utf-8") as f:
            json.dump({"X": {"steps": [{"recipe": "不存在的配方", "text": "直接干"}]}}, f, ensure_ascii=False)
        with open(dc.PATTERNS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        sent = []
        dc.set_send_callback(lambda t: sent.append(t))
        dc.set_busy_provider(lambda: False)
        try:
            dc.run_workflow("X")
            time.sleep(3.5)
            self.assertEqual(len(sent), 1)
            self.assertIn("直接干", sent[0])
            self.assertIn("配方「不存在的配方」不存在", sent[0])
        finally:
            dc.WORKFLOWS_FILE = None
            dc.PATTERNS_FILE = None
            import shutil as _sh

            _sh.rmtree(self.tmp2, ignore_errors=True)

    def test_schedule_workflow_action(self):
        """定时任务支持 workflow 动作（到点自动运行流程）。"""
        self.tmp2 = tempfile.mkdtemp(prefix="dsa_wf6_")
        dc.SCHEDULES_FILE = os.path.join(self.tmp2, "schedules.json")
        r = dc.schedule_task(expr_type="cron", expr="30 9 * * 1", content="周报流程", action="workflow", name="每周流程")
        self.assertIn("已创建", r)
        data = json.load(open(dc.SCHEDULES_FILE, encoding="utf-8"))
        self.assertEqual(data[0]["action"], "workflow")
        self.assertEqual(data[0]["text"], "周报流程")
        # 缺流程名被拒绝
        self.assertIn("错误", dc.schedule_task(expr_type="cron", expr="0 9 * * *", content="", action="workflow"))
        self.assertIn("错误", dc.schedule_task(expr_type="cron", expr="0 9 * * *", content="x", action="fly"))

    def test_read_email_missing_config(self):
        dc.EMAIL_CONFIG_FILE = os.path.join(tempfile.gettempdir(), "dsa_missing_email.json")
        self.assertIn("错误", dc.read_email())

    def test_read_email_incomplete_imap(self):
        p = os.path.join(self.tmp, "email_config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"imap": {"host": "imap.example.com"}}, f)  # 缺 user/password
        dc.EMAIL_CONFIG_FILE = p
        self.assertIn("imap 配置不完整", dc.read_email())

    def test_speech_to_text_bad_model_fallback(self):
        """非法 model 名回退 base（不触发真实模型加载：未安装 faster-whisper 时报安装错误）。"""
        p = os.path.join(self.tmp, "ws", "a.wav")
        with open(p, "wb") as f:
            f.write(b"RIFF")
        r = dc.speech_to_text(p, model="超大模型")
        self.assertTrue("错误" in r)  # 要么提示安装，要么识别失败——都不崩溃

    def test_schedule_not_initialized(self):
        dc.SCHEDULES_FILE = None
        self.assertIn("错误", dc.schedule_task(expr_type="cron", expr="0 9 * * *", content="x"))


if __name__ == "__main__":
    unittest.main()
