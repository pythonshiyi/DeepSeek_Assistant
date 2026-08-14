import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc
import permissions as perm


class TestAddToWhitelist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_perm_")
        # 注意：tempfile 默认位于 AppData\Local\Temp（在阻止列表内），
        # 测试环境移除 AppData 阻止项以模拟真实工作区（Documents）行为
        self.ws = os.path.join(self.tmp, "workspace")
        perm.init(
            os.path.join(self.tmp, "permissions.json"),
            self.ws,
            audit_dir=os.path.join(self.tmp, "logs"),
        )
        data = perm.get_data()
        data["filesystem"]["blocked_dirs"] = [
            d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
        ]
        perm.set_full_auto(False)
        self.data_dir = os.path.join(self.ws, "data")

    def test_add_dir(self):
        path = self.data_dir
        ok, msg = perm.add_to_whitelist("dir", path)
        self.assertTrue(ok)
        data = perm.get_data()
        self.assertIn(perm.resolve(path), data["filesystem"]["allowed_dirs"])
        # 之后 check_filesystem 放行
        ok2, _ = perm.check_filesystem(os.path.join(path, "x.txt"))
        self.assertTrue(ok2)

    def test_add_command(self):
        ok, msg = perm.add_to_whitelist("command", "curl")
        self.assertTrue(ok)
        data = perm.get_data()
        self.assertIn("curl", data["shell"]["whitelist"])
        ok2, _, _ = perm.check_shell("curl -s https://x.com")
        self.assertTrue(ok2)

    def test_add_write(self):
        ok, msg = perm.add_to_whitelist("write", "")
        self.assertTrue(ok)
        self.assertTrue(perm.get_data()["filesystem"]["allow_write"])

    def test_command_in_blocklist_rejected(self):
        ok, msg = perm.add_to_whitelist("command", "rm")
        self.assertFalse(ok)
        self.assertIn("阻止列表", msg)

    def test_unknown_type_rejected(self):
        ok, msg = perm.add_to_whitelist("bogus", "x")
        self.assertFalse(ok)

    def test_dir_blank_rejected(self):
        ok, msg = perm.add_to_whitelist("dir", "")
        self.assertFalse(ok)

    def test_persisted(self):
        perm.add_to_whitelist("command", "git")
        perm.init(
            os.path.join(self.tmp, "permissions.json"),
            os.path.join(self.tmp, "workspace"),
        )
        self.assertIn("git", perm.get_data()["shell"]["whitelist"])

    def test_request_whitelist_with_callback(self):
        calls = []
        perm.set_whitelist_callback(lambda at, v: (calls.append((at, v)) or (True, "ok")))
        ok, msg = perm.request_whitelist("command", "git")
        self.assertTrue(ok)
        self.assertEqual(calls, [("command", "git")])

    def test_request_whitelist_denied(self):
        perm.set_whitelist_callback(lambda at, v: (False, "用户拒绝"))
        ok, msg = perm.request_whitelist("command", "git")
        self.assertFalse(ok)

    def test_request_whitelist_full_auto(self):
        perm.set_full_auto(True)
        try:
            perm.set_whitelist_callback(lambda at, v: (False, "不应被调用"))
            ok, msg = perm.request_whitelist("command", "git")
            self.assertTrue(ok)
        finally:
            perm.set_full_auto(False)  # 还原全局态，防跨用例漂移

    def test_denial_reason_hints(self):
        # 工作区外的路径 → "不在允许目录内"提示（含 request_permission 指引）
        outside = os.path.join(self.ws, "..", "outside.txt")
        ok, reason = perm.check_filesystem(outside)
        self.assertFalse(ok)
        self.assertIn("request_permission", reason)
        # 命令不在白名单 → 提示
        ok2, reason2, _ = perm.check_shell("somecmd")
        self.assertFalse(ok2)
        self.assertIn("request_permission", reason2)

    def test_blocked_dir_case_insensitive(self):
        # Windows 路径大小写不敏感：C:\Windows 的任意大小写形式都不得绕过阻止列表
        data = perm.get_data()
        data["filesystem"]["blocked_dirs"] = ["C:\\Windows"]
        data["filesystem"]["allowed_dirs"] = ["C:\\"]
        data["filesystem"]["allow_write"] = True
        ok, reason = perm.check_filesystem(r"c:\windows\system32\cmd.exe", write=True)
        self.assertFalse(ok)
        self.assertIn("阻止列表", reason)
        ok2, _ = perm.check_filesystem(r"C:\WINDOWS\Temp\x.exe")
        self.assertFalse(ok2)

    def test_resolve_anchors_relative_to_workspace(self):
        # 相对路径锚定工作区，不依赖进程 CWD
        resolved = perm.resolve("data/x.csv")
        expected = os.path.normcase(
            os.path.realpath(os.path.join(perm.WORKSPACE_DIR, "data", "x.csv"))
        )
        self.assertEqual(resolved, expected)

    def test_resolve_rejects_dotdot_escape(self):
        resolved = perm.resolve(os.path.join(self.ws, "..", "..", "evil.txt"))
        self.assertNotIn("..", resolved)
        self.assertFalse(perm.check_filesystem(os.path.join(self.ws, "..", "evil.txt"))[0])

    def test_shlex_windows_path_kept(self):
        # Windows 下反斜杠路径不得被 shlex 当转义符吞掉
        perm.add_to_whitelist("command", "python")  # 同时开启 allow_run_command
        ok, _, argv = perm.check_shell(r'python C:\Users\me\script.py -v')
        self.assertTrue(ok)
        self.assertIn(r"C:\Users\me\script.py", argv)


class TestReadonlyStmt(unittest.TestCase):
    def test_allows_readonly(self):
        for sql in ("SELECT * FROM t", "SHOW TABLES", "PRAGMA journal_mode",
                    "EXPLAIN SELECT 1", "DESC t;"):
            self.assertTrue(dc._readonly_stmt(sql), sql)

    def test_rejects_writes(self):
        for sql in ("UPDATE t SET a=1", "DELETE FROM t", "DROP TABLE t",
                    "INSERT INTO t VALUES (1)", "SELECT 1; DROP TABLE t"):
            self.assertFalse(dc._readonly_stmt(sql), sql)

    def test_rejects_server_side_file_ops(self):
        # 前缀白名单可被服务器端功能绕过：必须显式拒绝
        for sql in (
            "SELECT * FROM t INTO OUTFILE 'C:/x.php'",
            "SELECT LOAD_FILE('/etc/passwd')",
            "SELECT lo_export(oid, '/tmp/x')",
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT SLEEP(30)",
            "SELECT BENCHMARK(1000000, MD5('x'))",
        ):
            self.assertFalse(dc._readonly_stmt(sql), sql)

    def test_rejects_embedded_semicolon(self):
        self.assertFalse(dc._readonly_stmt("SELECT 1; SELECT 2"))
        self.assertTrue(dc._readonly_stmt("SELECT 1;"))  # 单个尾部分号合法


class TestSSRFGuard(unittest.TestCase):
    def test_private_hosts_blocked(self):
        """内网/元数据/非 http 协议仍阻止；回环默认放行（本地开发验证）。"""
        for url in (
            "http://10.0.0.5/x",
            "http://192.168.1.1/",
            "http://172.16.3.4/",
            "http://169.254.169.254/latest/meta-data",
            "file:///C:/Windows/win.ini",
        ):
            self.assertTrue(dc._safe_url(url), url)
        for url in ("http://127.0.0.1/admin", "http://localhost:8080"):
            self.assertEqual(dc._safe_url(url), "", url)

    def test_public_hosts_allowed(self):
        for url in ("https://api.deepseek.com", "http://example.com/a", "https://www.baidu.com"):
            self.assertEqual(dc._safe_url(url), "", url)

    def test_fetch_url_blocks_private(self):
        out = dc.fetch_url("http://10.0.0.5:1234/")
        self.assertIn("SSRF", out) or self.assertIn("阻止", out)


class TestArrayItemsSchema(unittest.TestCase):
    """修复：array 参数缺 items 导致 API 400（missing field 'items'）。"""

    def test_all_builtin_arrays_have_items(self):
        """内置工具所有 array 参数均带 items（深层递归）。"""
        def check(node, path, out):
            if not isinstance(node, dict):
                return
            if node.get("type") == "array" and "items" not in node:
                out.append(path)
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    check(v, f"{path}.{k}", out)

        bad = []
        for t in dc.TOOLS:
            check(t["function"].get("parameters", {}), t["function"]["name"], bad)
        self.assertEqual(bad, [])

    def test_patch_fills_missing_items(self):
        """兜底：自定义工具/插件工具缺 items 时自动补齐（防 400）。"""
        tool = {
            "function": {
                "name": "my_tool",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rows": {"type": "array"},
                        "nested": {
                            "type": "object",
                            "properties": {"tags": {"type": "array"}},
                        },
                    },
                },
            }
        }
        dc._patch_array_items([tool])
        props = tool["function"]["parameters"]["properties"]
        self.assertIn("items", props["rows"])
        self.assertIn("items", props["nested"]["properties"]["tags"])

    def test_patch_keeps_existing_items(self):
        """已有 items 的 array 不被覆盖。"""
        tool = {
            "function": {
                "name": "t",
                "parameters": {
                    "type": "object",
                    "properties": {"tasks": {"type": "array", "items": {"type": "string"}}},
                },
            }
        }
        dc._patch_array_items([tool])
        self.assertEqual(
            tool["function"]["parameters"]["properties"]["tasks"]["items"], {"type": "string"}
        )


class TestRunPythonGuard(unittest.TestCase):
    def setUp(self):
        perm.set_full_auto(False)

    def tearDown(self):
        perm.set_full_auto(False)

    def test_blocked_patterns(self):
        for code in (
            "import os; os.system('calc')",
            "import subprocess; subprocess.run(['dir'])",
            "os.remove('x.txt')",
            "eval('1+1')",
            "import ctypes",
            "import socket",
            "__import__('os')",
            "shutil.rmtree('x')",
        ):
            self.assertTrue(dc._run_python_blocked(code), code)

    def test_legit_code_allowed(self):
        # 1.10.0 教训：\|a\|x 误拦含字母的合法代码——回归保护
        for code in (
            "a = 1; b = 2; print(a + b)",
            "text = 'xml data'; print(len(text))",
            "items = [x * 2 for x in range(10)]",
            "print(sum(range(100)))",
            "with open('f.txt') as f: print(f.read())",  # 读 open 合法
        ):
            self.assertEqual(dc._run_python_blocked(code), "", code)

    def test_full_auto_bypass(self):
        perm.set_full_auto(True)
        try:
            self.assertEqual(dc._run_python_blocked("import os; os.system('x')"), "")
        finally:
            perm.set_full_auto(False)


class TestRequestPermissionTool(unittest.TestCase):
    def setUp(self):
        self.client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")
        self.client.client.chat.completions.create = mock.MagicMock()

    def _fake_then_done(self, tool_name, args):
        call_no = {"n": 0}

        def fake_create(**kw):
            call_no["n"] += 1
            if call_no["n"] == 1:
                return self._stream_tool(tool_name, args)
            return self._stream_content("done")

        return fake_create

    def _stream_tool(self, name, args):
        class D:
            reasoning_content = None
            content = None
            tool_calls = [
                type("T", (), {"index": 0, "id": "c1", "function": type("F", (), {"name": name, "arguments": args})()})
            ]

        class C:
            delta = D()
            choices = [type("C", (), {"delta": D()})()]

        class S(list):
            usage = type("U", (), {"prompt_tokens": 0, "completion_tokens": 0, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0})()

        return S([C()])

    def _stream_content(self, text):
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

    def test_tool_registered(self):
        names = [t["function"]["name"] for t in dc.TOOLS]
        self.assertIn("request_permission", names)
        self.assertIsNone(dc.TOOL_CALL_MAP.get("request_permission"))

    def test_approved_callback_result(self):
        calls = []
        self.client.client.chat.completions.create.side_effect = self._fake_then_done(
            "request_permission", '{"action_type": "dir", "value": "C:/x"}'
        )
        msgs = [{"role": "user", "content": "hi"}]

        def cb(at, v):
            calls.append((at, v))
            return True, "已加入白名单"

        self.client.chat(msgs, tools_enabled=True, on_request_permission=cb)
        self.assertEqual(calls, [("dir", "C:/x")])
        self.assertIn("已加入白名单", msgs[-2]["content"])

    def test_denied_callback_result(self):
        self.client.client.chat.completions.create.side_effect = self._fake_then_done(
            "request_permission", '{"action_type": "command", "value": "curl"}'
        )
        msgs = [{"role": "user", "content": "hi"}]

        def cb(at, v):
            return False, "用户拒绝"

        self.client.chat(msgs, tools_enabled=True, on_request_permission=cb)
        self.assertIn("用户拒绝", msgs[-2]["content"])

    def test_missing_action_type(self):
        self.client.client.chat.completions.create.side_effect = self._fake_then_done(
            "request_permission", "{}"
        )
        msgs = [{"role": "user", "content": "hi"}]
        self.client.chat(msgs, tools_enabled=True, on_request_permission=lambda a, v: (True, ""))
        self.assertIn("action_type", msgs[-2]["content"])

    def test_no_callback(self):
        self.client.client.chat.completions.create.side_effect = self._fake_then_done(
            "request_permission", '{"action_type": "dir", "value": "C:/x"}'
        )
        msgs = [{"role": "user", "content": "hi"}]
        self.client.chat(msgs, tools_enabled=True)
        self.assertIn("不支持白名单请求", msgs[-2]["content"])


if __name__ == "__main__":
    unittest.main()
