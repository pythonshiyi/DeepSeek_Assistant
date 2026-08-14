# -*- coding: utf-8 -*-
"""v2 能力层回归测试：任务调度 / 文件闭环 / 知识库 RAG / 数据库写 / 检查点 / 洞察 / 长结果落盘。"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
