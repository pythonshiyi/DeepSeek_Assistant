import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc


def setup_module_paths(tmpdir):
    dc.MEMORY_FILE = os.path.join(tmpdir, "memory.json")
    dc.EMAIL_CONFIG_FILE = os.path.join(tmpdir, "email_config.json")


class TestGetDate(unittest.TestCase):
    def test_returns_datetime_with_timezone(self):
        result = dc.get_date()
        self.assertRegex(result, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

    def test_schema_updated(self):
        tool = next(t for t in dc.TOOLS if t["function"]["name"] == "get_date")
        self.assertIn("时间", tool["function"]["description"])


class TestMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_mem_")
        dc.MEMORY_FILE = os.path.join(self.tmp, "memory.json")

    def test_write_and_read_roundtrip(self):
        r = dc.write_memory("用户喜欢简洁回答", "偏好")
        self.assertIn("已写入", r)
        out = dc.read_memory()
        self.assertIn("用户喜欢简洁回答", out)
        self.assertIn("偏好", out)

    def test_dedup(self):
        dc.write_memory("同一内容")
        r = dc.write_memory("同一内容")
        self.assertIn("已存在", r)

    def test_read_keyword_filter(self):
        dc.write_memory("喜欢咖啡", "偏好")
        dc.write_memory("项目 A 明天上线", "工作")
        out = dc.read_memory(keyword="咖啡")
        self.assertIn("咖啡", out)
        self.assertNotIn("项目 A", out)

    def test_empty_text_rejected(self):
        self.assertIn("错误", dc.write_memory("   "))

    def test_long_text_truncated(self):
        dc.write_memory("x" * 5000)
        data = json.load(open(dc.MEMORY_FILE, encoding="utf-8"))
        self.assertLessEqual(len(data["facts"][0]["value"]), dc.MEMORY_MAX_TEXT + 1)

    def test_compatible_with_main_format(self):
        # 与 main 的 {"enabled", "facts"} 格式兼容
        dc.write_memory("记忆内容", "标签")
        data = json.load(open(dc.MEMORY_FILE, encoding="utf-8"))
        self.assertIn("enabled", data)
        self.assertIn("facts", data)
        self.assertIn("notes", data)

    def test_read_existing_facts(self):
        with open(dc.MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "facts": [{"key": "称呼", "value": "王工"}]}, f)
        out = dc.read_memory()
        self.assertIn("王工", out)


class TestReadFileRange(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_rfr_")
        self.path = os.path.join(self.tmp, "log.txt")
        with open(self.path, "w", encoding="utf-8") as f:
            for i in range(1, 101):
                f.write(f"line{i}\n")
        # 权限：直接 mock 权限检查放行
        self.patch = mock.patch("deepseek_client.permissions.check_filesystem", return_value=(True, ""))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_read_range(self):
        out = dc.read_file(self.path, start_line=10, max_lines=5)
        self.assertIn("line10", out)
        self.assertIn("line14", out)
        self.assertNotIn("line15", out)
        self.assertIn("第 10-14 行", out)

    def test_read_range_invalid_start(self):
        self.assertIn("错误", dc.read_file(self.path, start_line="abc"))

    def test_read_range_negative_clamped(self):
        out = dc.read_file(self.path, max_lines=-5)
        self.assertIn("line1", out)

    def test_read_past_end(self):
        out = dc.read_file(self.path, start_line=500, max_lines=10)
        self.assertIn("无内容", out)

    def test_read_whole_file_still_works(self):
        out = dc.read_file(self.path)
        self.assertIn("line1", out)
        self.assertIn("line100", out)


class TestDatabaseQuery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_db_")
        self.db = os.path.join(self.tmp, "test.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO users VALUES (1, '张三')")
        conn.execute("INSERT INTO users VALUES (2, '李四')")
        conn.commit()
        conn.close()
        self.patch = mock.patch("deepseek_client.permissions.check_filesystem", return_value=(True, ""))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_select_query(self):
        out = dc.database_query(self.db, "SELECT * FROM users")
        self.assertIn("张三", out)
        self.assertIn("李四", out)

    def test_non_select_rejected(self):
        out = dc.database_query(self.db, "DELETE FROM users")
        self.assertIn("只读", out)

    def test_missing_db(self):
        out = dc.database_query(os.path.join(self.tmp, "nope.db"), "SELECT 1")
        self.assertIn("错误", out)

    def test_missing_args(self):
        self.assertIn("错误", dc.database_query("", "SELECT 1"))
        self.assertIn("错误", dc.database_query(self.db, ""))


class TestSendEmail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_mail_")
        dc.EMAIL_CONFIG_FILE = os.path.join(self.tmp, "email_config.json")

    def test_no_config_hint(self):
        out = dc.send_email("a@b.com", "标题", "正文")
        self.assertIn("未配置", out)
        self.assertIn("email_config", out)

    def test_invalid_recipient(self):
        with open(dc.EMAIL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"smtp_host": "smtp.x.com", "smtp_port": 465, "user": "u@x.com", "password": "p"}, f)
        out = dc.send_email("bad-address", "标题", "正文")
        self.assertIn("错误", out)

    def test_send_success(self):
        with open(dc.EMAIL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"smtp_host": "smtp.x.com", "smtp_port": 465, "user": "u@x.com", "password": "p"}, f)
        with mock.patch("smtplib.SMTP_SSL") as smtp_ssl, mock.patch("smtplib.SMTP") as smtp_plain:
            smtp_ssl.return_value = mock.MagicMock()
            smtp_plain.return_value = mock.MagicMock()
            out = dc.send_email("a@b.com", "标题", "正文")
        self.assertIn("已发送", out)


class TestPipInstall(unittest.TestCase):
    def _patch_popen(self, rc=0, output=""):
        """pip_install 输出走 SpooledTemporaryFile + Popen（与 run_python 同款防 OOM）。"""
        patcher = mock.patch("deepseek_client.subprocess.Popen")
        popen = patcher.start()
        popen.return_value.returncode = rc
        popen.return_value.wait.return_value = rc
        self.addCleanup(patcher.stop)
        return popen

    def test_whitelist_enforced_when_list(self):
        dc.PIP_ALLOWLIST = ["requests"]
        try:
            out = dc.pip_install("malicious-pkg-xyz")
        finally:
            dc.PIP_ALLOWLIST = None
        self.assertIn("白名单", out)

    def test_open_mode_allows_any(self):
        self.assertIsNone(dc.PIP_ALLOWLIST)
        self._patch_popen(rc=0)
        out = dc.pip_install("some-new-package")
        self.assertIn("已安装", out)

    def test_empty(self):
        self.assertIn("错误", dc.pip_install(""))

    def test_success(self):
        self._patch_popen(rc=0)
        out = dc.pip_install("requests")
        self.assertIn("已安装", out)

    def test_failure(self):
        self._patch_popen(rc=1, output="error: 无法解析")
        out = dc.pip_install("pandas")
        self.assertIn("失败", out)


class TestRunPythonWithSite(unittest.TestCase):
    # run_python 输出走 SpooledTemporaryFile + Popen（防刷屏打印 OOM）
    def _patch_popen(self):
        patcher = mock.patch("deepseek_client.subprocess.Popen")
        popen = patcher.start()
        popen.return_value.wait.return_value = 0
        popen.return_value.returncode = 0
        self.addCleanup(patcher.stop)
        return popen

    def test_with_site_false_default(self):
        popen = self._patch_popen()
        dc.run_python("print(1)")
        argv = popen.call_args.args[0]
        self.assertIn("-S", argv)

    def test_with_site_true(self):
        popen = self._patch_popen()
        dc.run_python("print(1)", with_site=True)
        argv = popen.call_args.args[0]
        self.assertNotIn("-S", argv)
        self.assertIn("-I", argv)


class TestToolRegistration(unittest.TestCase):
    def test_new_tools_in_schema(self):
        names = [t["function"]["name"] for t in dc.TOOLS]
        for n in ("ask_user", "read_memory", "write_memory", "database_query", "send_email", "pip_install"):
            self.assertIn(n, names)

    def test_calculate_removed(self):
        names = [t["function"]["name"] for t in dc.TOOLS]
        self.assertNotIn("calculate", names)
        self.assertNotIn("calculate", dc.TOOL_CALL_MAP)

    def test_ask_user_marked_special(self):
        self.assertIsNone(dc.TOOL_CALL_MAP.get("ask_user"))


class TestBrowserMode(unittest.TestCase):
    def setUp(self):
        # 共享浏览器全局状态清理：防止测试间复用上一次的 mock 实例
        dc._BROWSER = None
        dc._BROWSER_PW = None
        dc._BROWSER_PAGE = None
        dc.BROWSER_PROFILE_DIR = None
        dc.BROWSER_HEADLESS = True
        self.client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")

    def test_default_headless(self):
        self.assertTrue(dc.BROWSER_HEADLESS)
        self.assertTrue(dc._browser_headless())

    def test_toggle_injected(self):
        dc.BROWSER_HEADLESS = False
        try:
            self.assertFalse(dc._browser_headless())
        finally:
            dc.BROWSER_HEADLESS = True

    def _launch_mock(self):
        from unittest import mock

        fake_playwright = mock.MagicMock()
        fake_playwright.__enter__ = mock.MagicMock(return_value=fake_playwright)
        fake_playwright.__exit__ = mock.MagicMock(return_value=False)
        # 新实现：共享浏览器复用（sync_playwright().start() 返回同实例）
        fake_playwright.start.return_value = fake_playwright
        fake_browser = mock.MagicMock()
        fake_page = mock.MagicMock()
        fake_page.title.return_value = "测试页"
        fake_page.url = "http://x"
        fake_browser.new_page.return_value = fake_page
        fake_browser.pages = [fake_page]
        fake_playwright.chromium.launch.return_value = fake_browser
        return fake_playwright

    def test_browser_navigate_headless_launch(self):
        from unittest import mock

        fake_playwright = self._launch_mock()
        with mock.patch("playwright.sync_api.sync_playwright", return_value=fake_playwright):
            with mock.patch("deepseek_client._playwright_ready", return_value=(True, "")):
                dc.BROWSER_HEADLESS = True
                r = dc.browser_navigate("http://x", action="open")
        self.assertIn("测试页", r)
        kwargs = fake_playwright.chromium.launch.call_args.kwargs
        self.assertTrue(kwargs.get("headless", True))

    def test_browser_navigate_headed_launch(self):
        from unittest import mock

        fake_playwright = self._launch_mock()
        with mock.patch("playwright.sync_api.sync_playwright", return_value=fake_playwright):
            with mock.patch("deepseek_client._playwright_ready", return_value=(True, "")):
                dc.BROWSER_HEADLESS = False
                r = dc.browser_navigate("http://x", action="open")
        kwargs = fake_playwright.chromium.launch.call_args.kwargs
        self.assertFalse(kwargs.get("headless", True))
        dc.BROWSER_HEADLESS = True


class TestAskUserInChat(unittest.TestCase):
    def setUp(self):
        self.client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")
        self.client.client.chat.completions.create = mock.MagicMock()

    def _fake_stream(self, tool_name, args):
        class D:
            reasoning_content = None
            content = None
            tool_calls = [
                type("T", (), {"index": 0, "id": "c1", "function": type("F", (), {"name": tool_name, "arguments": args})()})
            ]

        class C:
            delta = D()
            choices = [type("C", (), {"delta": D()})()]

        class S(list):
            usage = type("U", (), {"prompt_tokens": 0, "completion_tokens": 0, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0})()

        return S([C()])

    def _fake_ask_then_done(self, tool_name, args):
        """第一次返回工具调用，之后返回正常内容。"""
        call_no = {"n": 0}

        def fake_create(**kw):
            call_no["n"] += 1
            if call_no["n"] == 1:
                return self._fake_stream(tool_name, args)
            return self._fake_stream_content("done")

        return fake_create

    @staticmethod
    def _fake_stream_content(text):
        class D:
            reasoning_content = None
            content = text
            tool_calls = None

        class C:
            delta = D()
            choices = [type("C", (), {"delta": D()})()]

        class S(list):
            usage = type("U", (), {"prompt_tokens": 0, "completion_tokens": 0, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0})()

        return S([C()])

    def test_ask_user_calls_callback(self):
        calls = []
        self.client.client.chat.completions.create.side_effect = self._fake_ask_then_done(
            "ask_user", '{"prompt": "你确认吗？"}'
        )
        msgs = [{"role": "user", "content": "hi"}]

        def on_ask(prompt):
            calls.append(prompt)
            return "我确认"

        result = self.client.chat(msgs, tools_enabled=True, on_ask=on_ask)
        self.assertTrue(result)
        self.assertEqual(calls, ["你确认吗？"])
        self.assertIn("我确认", msgs[-2]["content"])

    def test_ask_user_without_callback(self):
        self.client.client.chat.completions.create.side_effect = self._fake_ask_then_done(
            "ask_user", '{"prompt": "问题"}'
        )
        msgs = [{"role": "user", "content": "hi"}]
        self.client.chat(msgs, tools_enabled=True)
        self.assertIn("无法询问用户", msgs[-2]["content"])


class TestTruncationDetection(unittest.TestCase):
    """修复回归：生成被截断/中断时 chat() 必须通过 on_truncated 告知 UI，
    不得静默返回 True 让 UI 误报「任务完成」（此前 max_tokens 截断即静默完成）。"""

    def setUp(self):
        self.client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")
        self.client.client.chat.completions.create = mock.MagicMock()

    @staticmethod
    def _stream(content="", finish_reason=None, tool_calls=None):
        c_val = content or None
        fr_val = finish_reason
        tc_val = tool_calls

        class D:
            reasoning_content = None
            content = c_val
            tool_calls = tc_val

        class C:
            delta = D()
            finish_reason = fr_val
            choices = [type("CC", (), {"delta": D(), "finish_reason": fr_val})()]

        class S(list):
            usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1,
                                   "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1})()

        return S([C()])

    def test_length_finish_reason_notifies(self):
        """输出达 max_tokens 上限（finish_reason=length）→ on_truncated 被调用。"""
        self.client.client.chat.completions.create.return_value = self._stream(
            content="前半段回复", finish_reason="length"
        )
        reasons = []
        msgs = [{"role": "user", "content": "hi"}]
        result = self.client.chat(msgs, on_truncated=reasons.append)
        self.assertTrue(result)
        self.assertEqual(len(reasons), 1)
        self.assertIn("截断", reasons[0])

    def test_normal_stop_no_notify(self):
        """正常结束（finish_reason=stop）→ on_truncated 不被调用。"""
        self.client.client.chat.completions.create.return_value = self._stream(
            content="完整回复", finish_reason="stop"
        )
        reasons = []
        msgs = [{"role": "user", "content": "hi"}]
        result = self.client.chat(msgs, on_truncated=reasons.append)
        self.assertTrue(result)
        self.assertEqual(reasons, [])

    @staticmethod
    def _tool(tc_id, name, args="{}"):
        return type("T", (), {
            "index": 0,
            "id": tc_id,
            "function": type("F", (), {"name": name, "arguments": args})(),
        })()

    def test_incomplete_tool_calls_notify(self):
        """工具调用流被截断（缺 id/name）→ on_truncated 被调用且不执行工具。"""
        self.client.client.chat.completions.create.return_value = self._stream(
            tool_calls=[self._tool("", "write_file")]
        )
        reasons = []
        msgs = [{"role": "user", "content": "hi"}]
        result = self.client.chat(msgs, tools_enabled=True, on_truncated=reasons.append)
        self.assertFalse(result)
        self.assertEqual(len(reasons), 1)
        self.assertIn("截断", reasons[0])
        self.assertNotIn("tool_calls", msgs[-1])  # 悬空 tool_call 被移除

    def test_rounds_exhausted_notify(self):
        """工具轮数耗尽 → on_truncated 被调用（此前静默返回 True）。"""
        # 每轮都返回工具调用（永远不结束）→ for 循环自然耗尽
        self.client.client.chat.completions.create.return_value = self._stream(
            tool_calls=[self._tool("c1", "get_date")]
        )
        reasons = []
        msgs = [{"role": "user", "content": "hi"}]
        with mock.patch("deepseek_client.TOOL_CALL_MAP", {"get_date": lambda: "ok"}):
            result = self.client.chat(msgs, tools_enabled=True, max_tool_rounds=2, on_truncated=reasons.append)
        self.assertTrue(result)
        self.assertEqual(len(reasons), 1)
        self.assertIn("轮数已达上限", reasons[0])

    def test_auto_simple_task_no_reasoning_effort(self):
        """V4 正式版适配：auto 档判定为简单任务时关闭思考，
        不得传 reasoning_effort='none'（非法参数），改走采样参数路径。"""
        self.client.client.chat.completions.create.return_value = self._stream(content="你好", finish_reason="stop")
        captured = {}

        def fake_create(**kw):
            captured.update(kw)
            return self.client.client.chat.completions.create.return_value

        self.client.client.chat.completions.create.side_effect = fake_create
        with mock.patch("deepseek_client._auto_effort", return_value="none"):
            self.client.chat([{"role": "user", "content": "你好"}], thinking="auto", tools_enabled=False)
        self.assertNotIn("reasoning_effort", captured)
        self.assertIn("temperature", captured)

    def test_ga_thinking_levels_passthrough(self):
        """正式版思考档位 low/high/max 直接透传。"""
        for level in ("low", "high", "max"):
            self.client.client.chat.completions.create.return_value = self._stream(content="x", finish_reason="stop")
            captured = {}
            self.client.client.chat.completions.create.side_effect = (
                lambda **kw: (captured.update(kw) or self.client.client.chat.completions.create.return_value)
            )
            self.client.chat([{"role": "user", "content": "hi"}], thinking=level, tools_enabled=False)
            self.assertEqual(captured.get("reasoning_effort"), level, level)

    def test_effort_mapping_medium_xhigh(self):
        """官方映射：medium→high · xhigh→high（此前 xhigh→max 是错误假设）。"""
        for level, expected in (("medium", "high"), ("xhigh", "high")):
            self.client.client.chat.completions.create.return_value = self._stream(content="x", finish_reason="stop")
            captured = {}
            self.client.client.chat.completions.create.side_effect = (
                lambda **kw: (captured.update(kw) or self.client.client.chat.completions.create.return_value)
            )
            self.client.chat([{"role": "user", "content": "hi"}], thinking=level, tools_enabled=False)
            self.assertEqual(captured.get("reasoning_effort"), expected, level)


class TestReasoningCostOptimization(unittest.TestCase):
    """超越官方开箱体验：无工具轮次剥离思考内容（省输入 token）+ 缓存友好布局 + trailing 注入。"""

    def setUp(self):
        self.client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")
        self.client.client.chat.completions.create = mock.MagicMock()

    @staticmethod
    def _stream(content="", finish_reason="stop", tool_calls=None):
        c_val = content or None
        fr_val = finish_reason
        tc_val = tool_calls

        class D:
            reasoning_content = None
            content = c_val
            tool_calls = tc_val

        class C:
            delta = D()
            finish_reason = fr_val
            choices = [type("CC", (), {"delta": D(), "finish_reason": fr_val})()]

        class S(list):
            usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1,
                                   "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1})()

        return S([C()])

    def _capture(self):
        captured = {}
        self.client.client.chat.completions.create.side_effect = (
            lambda **kw: (captured.update(kw) or self.client.client.chat.completions.create.return_value)
        )
        return captured

    def test_no_tool_round_reasoning_pruned(self):
        """历史中无工具调用的 assistant 轮次：发送时剥离 reasoning_content（API 忽略，省 token）。"""
        self.client.client.chat.completions.create.return_value = self._stream(content="好", finish_reason="stop")
        captured = self._capture()
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "答1", "reasoning_content": "思考了5000字"},  # 无工具 → 应剥离
            {"role": "user", "content": "q2"},
        ]
        self.client.chat(msgs, tools_enabled=False)
        sent = captured["messages"]
        sent_assistant = [m for m in sent if m.get("role") == "assistant"]
        self.assertEqual(len(sent_assistant), 1)
        self.assertNotIn("reasoning_content", sent_assistant[0])
        # 内存中的原始历史不被破坏（UI/存档仍需 reasoning）
        self.assertEqual(msgs[2]["reasoning_content"], "思考了5000字")

    def test_tool_round_reasoning_kept(self):
        """带工具调用的轮次：reasoning_content 必须完整回传（官方要求，缺失会 400）。"""
        self.client.client.chat.completions.create.return_value = self._stream(content="好", finish_reason="stop")
        captured = self._capture()
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": None, "reasoning_content": "先查日期",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "get_date", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "2026-08-13"},
            {"role": "user", "content": "q2"},
        ]
        self.client.chat(msgs, tools_enabled=False)
        sent = captured["messages"]
        sent_assistant = [m for m in sent if m.get("role") == "assistant"]
        self.assertEqual(len(sent_assistant), 1)
        self.assertEqual(sent_assistant[0].get("reasoning_content"), "先查日期")

    def test_cache_friendly_layout(self):
        """缓存友好布局：恒定的 json_hint 在前，可变的记忆注入在末尾（前缀稳定）。"""
        self.client.client.chat.completions.create.return_value = self._stream(
            content='{"ok": 1}', finish_reason="stop"
        )
        captured = self._capture()
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "q1"}]
        self.client.chat(msgs, json_output=True, memory_text="记忆内容（可能变）", tools_enabled=False)
        sent = captured["messages"]
        roles = [m.get("role") for m in sent]
        contents = [m.get("content") for m in sent]
        self.assertEqual(contents[0], dc.JSON_HINT_MESSAGE)   # json_hint 恒定在最前
        # 记忆注入在末尾（此后仅追加模型回复）
        self.assertEqual(sent[-1]["content"], "记忆内容（可能变）")
        self.assertEqual(roles.count("system"), 3)

    def test_trailing_text_appended_to_user(self):
        """动态注入（trailing_text）追加到最近一条 user 消息尾部，不污染历史。"""
        self.client.client.chat.completions.create.return_value = self._stream(content="好", finish_reason="stop")
        captured = self._capture()
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "q1"}]
        self.client.chat(msgs, trailing_text="[动态] 项目上下文摘要", tools_enabled=False)
        sent = captured["messages"]
        last_user = [m for m in sent if m.get("role") == "user"][-1]
        self.assertIn("[动态] 项目上下文摘要", last_user["content"])
        # 内存历史中的 user 消息不被污染（chat 只会在末尾追加模型回复）
        users = [m for m in msgs if m.get("role") == "user"]
        self.assertEqual(users[-1]["content"], "q1")


class TestJsonOutputSelfRetry(unittest.TestCase):
    """超越官方开箱体验：JSON 输出自校验，解析失败自动修正重试一次。"""

    def setUp(self):
        self.client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")
        self.client.client.chat.completions.create = mock.MagicMock()

    @staticmethod
    def _stream(content="", finish_reason="stop"):
        c_val = content or None
        fr_val = finish_reason

        class D:
            reasoning_content = None
            content = c_val
            tool_calls = None

        class C:
            delta = D()
            finish_reason = fr_val
            choices = [type("CC", (), {"delta": D(), "finish_reason": fr_val})()]

        class S(list):
            usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1,
                                   "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1})()

        return S([C()])

    def test_invalid_json_retried_with_fix_prompt(self):
        """第一次输出非法 JSON → 自动修正重试 → 第二次合法 → 成功。"""
        calls = []

        def fake_create(**kw):
            calls.append(kw["messages"])
            n = len(calls)
            if n == 1:
                return self._stream(content='{"a": 未闭合', finish_reason="stop")
            return self._stream(content='{"a": 1}', finish_reason="stop")

        self.client.client.chat.completions.create.side_effect = fake_create
        reasons = []
        msgs = [{"role": "user", "content": "给我 JSON"}]
        result = self.client.chat(msgs, json_output=True, tools_enabled=False, on_truncated=reasons.append)
        self.assertTrue(result)
        self.assertEqual(len(calls), 2)
        # 修正提示被追加进第二轮的请求
        self.assertIn("无法解析为合法 JSON", calls[1][-1]["content"])
        self.assertIn("JSON 输出解析失败", reasons[0])

    def test_valid_json_no_retry(self):
        self.client.client.chat.completions.create.return_value = self._stream(
            content='{"a": 1}', finish_reason="stop"
        )
        calls = []
        self.client.client.chat.completions.create.side_effect = (
            lambda **kw: (calls.append(1) or self.client.client.chat.completions.create.return_value)
        )
        msgs = [{"role": "user", "content": "给我 JSON"}]
        result = self.client.chat(msgs, json_output=True, tools_enabled=False)
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
