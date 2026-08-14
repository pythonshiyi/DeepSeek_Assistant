# -*- coding: utf-8 -*-
"""本次优化修复的回归测试：
run_python ast 加固 / SSRF DNS 重绑定 / 自定义工具 SSRF / 自我进化白名单 /
共享失败前缀常量 / search_local 控制流。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc
import permissions
import main as m


class TestRunPythonAstGuard(unittest.TestCase):
    """run_python 双层防线：正则 + ast（修复 from-import / 动态导入 / 反射 / 写 open 绕过）。"""

    def setUp(self):
        permissions.set_full_auto(False)

    def tearDown(self):
        permissions.set_full_auto(False)

    def test_from_os_import_bypass_blocked(self):
        # 修复前：from os import system 不匹配任何词边界正则，可绕过沙箱
        for code in (
            "from os import system; system('calc')",
            "from os import remove; remove('x.txt')",
            "from os import kill; kill(1, 9)",
        ):
            self.assertTrue(dc._run_python_blocked(code), code)

    def test_dynamic_import_blocked(self):
        # 修复前：字符串拼接的动态导入绕过 subprocess 词面匹配
        for code in (
            "import importlib; importlib.import_module('sub' + 'process')",
            "__import__('subprocess')",
            "import importlib; importlib.import_module('socket')",
        ):
            self.assertTrue(dc._run_python_blocked(code), code)

    def test_import_forbidden_modules_blocked(self):
        for code in (
            "import subprocess",
            "import ctypes",
            "import socket",
            "from subprocess import run",
            "import socket.ssl",
        ):
            self.assertTrue(dc._run_python_blocked(code), code)

    def test_reflective_call_blocked(self):
        # 修复前：os['system'] / getattr(os,'system') 绕过点式正则
        for code in (
            "import os; os['system']('x')",
            "import os; getattr(os, 'system')('x')",
        ):
            self.assertTrue(dc._run_python_blocked(code), code)

    def test_write_open_blocked(self):
        for code in (
            "open('f.txt', 'w').write('x')",
            "open('f.txt', 'a')",
            "open('f.txt', 'xb')",
            "open('f.txt', 'r+')",
            "open('f.txt', mode='wb')",
        ):
            self.assertTrue(dc._run_python_blocked(code), code)

    def test_pathlib_write_blocked(self):
        self.assertTrue(dc._run_python_blocked("from pathlib import Path; Path('f').write_text('x')"))
        self.assertTrue(dc._run_python_blocked("import pathlib; pathlib.Path('f').write_bytes(b'x')"))

    def test_read_open_allowed(self):
        # 回归：合法读文件不被误拦
        self.assertEqual(dc._run_python_blocked("with open('f.txt') as f: print(f.read())"), "")
        self.assertEqual(dc._run_python_blocked("open('f.txt', 'rb')"), "")
        self.assertEqual(dc._run_python_blocked("from os import getcwd, path; print(getcwd())"), "")
        self.assertEqual(dc._run_python_blocked("from os import listdir; print(listdir('.')[:3])"), "")

    def test_legit_code_allowed(self):
        # 1.10.0 教训回归：合法代码零误伤
        for code in (
            "a = 1; b = 2; print(a + b)",
            "text = 'xml data'; print(len(text))",
            "items = [x * 2 for x in range(10)]",
            "print(sum(range(100)))",
            "import json; print(json.dumps({'k': 'v'}))",
            "from datetime import datetime; print(datetime.now())",
            "import math; print(math.sqrt(2))",
        ):
            self.assertEqual(dc._run_python_blocked(code), "", code)

    def test_syntax_error_not_blocked(self):
        # 语法错误交给子进程报错，不误判为安全拦截
        self.assertEqual(dc._run_python_blocked("def broken(:"), "")

    def test_full_auto_bypass_kept(self):
        permissions.set_full_auto(True)
        try:
            self.assertEqual(dc._run_python_blocked("import os; os.system('x')"), "")
        finally:
            permissions.set_full_auto(False)


class TestSSRFDnsRebinding(unittest.TestCase):
    """SSRF DNS 重绑定防护：域名解析结果落内网即拦截（修复前只查主机名字面）。"""

    def _patch_getaddrinfo(self, ips):
        import socket as _socket

        def fake_getaddrinfo(host, port, *a, **kw):
            out = []
            for ip in ips:
                fam = _socket.AF_INET6 if ":" in ip else _socket.AF_INET
                out.append((fam, _socket.SOCK_STREAM, 6, "", (ip, 80)))
            return out

        return mock.patch("socket.getaddrinfo", side_effect=fake_getaddrinfo)

    def test_domain_resolving_to_loopback(self):
        """解析到回环：默认放行（本地开发验证）；严格场景（allow_loopback=False）拦截。"""
        with self._patch_getaddrinfo(["127.0.0.1"]):
            self.assertFalse(dc._is_private_host("evil.example.com"))
            self.assertTrue(dc._is_private_host("evil.example.com", allow_loopback=False))

    def test_domain_resolving_to_private_blocked(self):
        with self._patch_getaddrinfo(["10.0.0.5", "8.8.8.8"]):
            # 任一解析结果内网即拦截
            self.assertTrue(dc._is_private_host("mixed.example.com"))

    def test_domain_resolving_to_public_allowed(self):
        with self._patch_getaddrinfo(["8.8.8.8", "2606:4700:4700::1111"]):
            self.assertFalse(dc._is_private_host("public.example.com"))

    def test_metadata_hostname_blocked(self):
        # 云元数据地址永远阻止（白名单不可豁免）；回环默认放行（本地开发验证）
        self.assertTrue(dc._is_private_host("169.254.169.254"))
        self.assertFalse(dc._is_private_host("localhost"))

    def test_loopback_allowed_but_private_blocked(self):
        """回环（localhost/127/::1）默认放行；内网/保留网段仍阻止。"""
        for host in ("127.0.0.1", "::1", "127.0.0.9"):
            self.assertFalse(dc._is_private_host(host), host)
        for host in ("10.1.2.3", "172.16.5.5", "192.168.0.1", "0.0.0.0", "169.254.1.2"):
            self.assertTrue(dc._is_private_host(host), host)

    def test_loopback_strict_when_disallowed(self):
        """严格场景（搜索过滤等外部注入源）：回环也阻止。"""
        self.assertTrue(dc._is_private_host("127.0.0.1", allow_loopback=False))
        self.assertTrue(dc._safe_url("http://localhost:3000/", allow_loopback=False))

    def test_trusted_whitelist_allows_private(self):
        """SSRF 信任白名单：内网 IP/CIDR/主机名 显式信任后放行；元数据仍阻止。"""
        dc.set_ssrf_trusted(["192.168.1.10", "10.0.0.0/8", "nas.home.arpa"])
        try:
            self.assertFalse(dc._is_private_host("192.168.1.10"))
            self.assertFalse(dc._is_private_host("10.2.3.4"))       # CIDR
            self.assertFalse(dc._is_private_host("NAS.HOME.ARPA"))  # 大小写不敏感
            self.assertTrue(dc._is_private_host("192.168.2.9"))     # 未信任
            self.assertTrue(dc._is_private_host("169.254.169.254"))  # 元数据永不豁免
        finally:
            dc.set_ssrf_trusted([])

    def test_plain_ip_variants_still_blocked(self):
        for host in ("10.1.2.3", "172.16.5.5", "192.168.0.1", "0.0.0.0"):
            self.assertTrue(dc._is_private_host(host), host)

    def test_fetch_url_allows_localhost(self):
        """fetch_url 默认放行回环（本地服务器验证），内网仍拒绝。"""
        self.assertFalse(dc._safe_url("http://localhost:3000/"))
        self.assertFalse(dc._safe_url("http://127.0.0.1:5173/"))
        self.assertTrue(dc._safe_url("http://192.168.1.5/"))

    def test_fetch_url_rejects_internal_after_resolve(self):
        with self._patch_getaddrinfo(["10.0.0.5"]):
            out = dc.fetch_url("http://rebind.example.com/")
        self.assertIn("SSRF", out) or self.assertIn("阻止", out)


class TestCustomToolSSRF(unittest.TestCase):
    def test_endpoint_loopback_allowed(self):
        """自定义工具 endpoint 为回环（用户注册的本地服务）默认放行。"""
        handler = {"function": {"name": "t", "endpoint": "http://127.0.0.1:8080/api"}}
        with mock.patch("deepseek_client._http_client") as http:
            http.return_value.post.return_value.raise_for_status.return_value = None
            http.return_value.post.return_value.text = "ok"
            out = dc.DeepSeekClient._run_custom_tool(handler, "{}")
        self.assertEqual(out, "ok")

    def test_endpoint_internal_rejected(self):
        handler = {"function": {"name": "t", "endpoint": "http://192.168.1.10:8080/api"}}
        out = dc.DeepSeekClient._run_custom_tool(handler, "{}")
        self.assertIn("endpoint", out)

    def test_endpoint_public_allowed(self):
        handler = {"function": {"name": "t", "endpoint": "https://example.com/api"}}
        with mock.patch("deepseek_client._http_client") as http:
            http.return_value.post.return_value.raise_for_status.return_value = None
            http.return_value.post.return_value.text = "ok"
            out = dc.DeepSeekClient._run_custom_tool(handler, "{}")
        self.assertEqual(out, "ok")


class TestEvolutionWriteExts(unittest.TestCase):
    def test_bat_removed_from_whitelist(self):
        # 修复：.bat 可执行文件不再允许写入提案（防与 run_command 联动提权）
        self.assertNotIn(".bat", dc.EVO_WRITE_EXTS)

    def test_create_evolution_rejects_bat(self):
        out = dc.create_evolution("ev_test", [{"path": "x.bat", "content": "@echo off"}])
        self.assertIn("不支持的文件类型", out)

    def test_py_md_still_allowed(self):
        out = dc.create_evolution("ev_test2", [{"path": "a.py", "content": "print(1)"}])
        self.assertIn("已创建", out)
        import shutil

        branch = out.split("\n")[0].split("：")[-1].strip()
        shutil.rmtree(branch, ignore_errors=True)


class TestSharedFailPrefixes(unittest.TestCase):
    def test_constant_defined(self):
        self.assertIsInstance(dc.TOOL_RESULT_FAIL_PREFIXES, tuple)
        self.assertTrue(dc.TOOL_RESULT_FAIL_PREFIXES)

    def test_failure_detection_consistent(self):
        for sample in ("错误：xxx", "权限拒绝：yyy", "超时", "（用户停止生成）"):
            self.assertTrue(sample.startswith(dc.TOOL_RESULT_FAIL_PREFIXES))
        for sample in ("成功", "已写入", "✅ done", "结果：42"):
            self.assertFalse(sample.startswith(dc.TOOL_RESULT_FAIL_PREFIXES))


class TestSearchLocalControlFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_sl_")
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws, exist_ok=True)
        permissions.init(os.path.join(self.tmp, "perm.json"), ws)
        data = permissions.get_data()
        data["filesystem"]["blocked_dirs"] = [
            d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
        ]
        self.ws = ws

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hit_and_miss(self):
        with open(os.path.join(self.ws, "a.txt"), "w", encoding="utf-8") as f:
            f.write("包含关键词的文本")
        out = dc.search_local(self.ws, "关键词")
        self.assertIn("a.txt", out)
        out2 = dc.search_local(self.ws, "不存在的词xyz")
        self.assertIn("未找到", out2)


class TestFailurePatterns(unittest.TestCase):
    """失败模式库：记录去重 / 注入文本 / 上限裁剪（静态方法，无实例依赖）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_fail_")
        m.FAILURES_PATH = os.path.join(self.tmp, "failures.json")

    def tearDown(self):
        import shutil

        m.FAILURES_PATH = os.path.join(m.DATA_DIR, "failures.json")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_dedup_and_cap(self):
        m.AssistantApp._append_failures([
            {"tool": "write_file", "args": "{}", "error": "内容超过大小限制", "ts": "t1"},
            {"tool": "write_file", "args": "{}", "error": "内容超过大小限制", "ts": "t2"},
            {"tool": "fetch_url", "args": "{}", "error": "网络错误", "ts": "t3"},
        ])
        items = m.AssistantApp._load_failures()
        self.assertEqual(len(items), 2)  # 同工具同错误去重
        self.assertEqual(items[0]["ts"], "t2")  # 去重时更新时间戳

    def test_injection_text(self):
        m.AssistantApp._append_failures([
            {"tool": "edit_file", "args": "{}", "error": "目标文本未找到", "ts": "t"},
        ])
        text = m.AssistantApp._failure_patterns_text()
        self.assertIn("edit_file", text)
        self.assertIn("目标文本未找到", text)
        self.assertIn("已知失败模式", text)

    def test_empty_no_injection(self):
        self.assertEqual(m.AssistantApp._failure_patterns_text(), "")


class TestBudgetThinking(unittest.TestCase):
    """预算感知思考降档：接近预算 80% 时 auto/max 自动降 high，尊重手动档位。"""

    def test_auto_downgraded_near_budget(self):
        eff, near = m.AssistantApp._budget_thinking(100.0, 90.0, "auto")
        self.assertEqual(eff, "high")
        self.assertTrue(near)

    def test_max_downgraded_near_budget(self):
        eff, near = m.AssistantApp._budget_thinking(100.0, 80.0, "max")
        self.assertEqual(eff, "high")
        self.assertTrue(near)

    def test_manual_levels_respected(self):
        """用户显式选择 low/medium/high 不被降档。"""
        for level in ("low", "medium", "high"):
            eff, near = m.AssistantApp._budget_thinking(100.0, 99.0, level)
            self.assertEqual(eff, level, level)
            self.assertTrue(near)

    def test_within_budget_no_change(self):
        eff, near = m.AssistantApp._budget_thinking(100.0, 50.0, "max")
        self.assertEqual(eff, "max")
        self.assertFalse(near)

    def test_no_budget_no_change(self):
        eff, near = m.AssistantApp._budget_thinking(0.0, 999.0, "auto")
        self.assertEqual(eff, "auto")
        self.assertFalse(near)


class TestAutostart(unittest.TestCase):
    """开机自启命令构造与注册表写入（mock winreg，不触碰真实注册表）。"""

    def test_command_contains_main(self):
        if getattr(sys, "frozen", False):
            self.skipTest("打包环境命令为 exe 自身")
        cmd = m.AssistantApp._autostart_command()
        self.assertIn("main.py", cmd)
        self.assertIn("pythonw", cmd)

    def test_set_autostart_writes_registry(self):
        with mock.patch("winreg.OpenKey", return_value=object()) as ok, \
             mock.patch("winreg.SetValueEx") as sv, \
             mock.patch("winreg.DeleteValue") as dv, \
             mock.patch("winreg.CloseKey"):
            self.assertTrue(m.AssistantApp._set_autostart(True))
            sv.assert_called_once()
            dv.assert_not_called()
            ok.assert_called_once()
            self.assertTrue(m.AssistantApp._set_autostart(False))
            dv.assert_called_once()
            sv.assert_called_once()  # 关闭时不再写入


if __name__ == "__main__":
    unittest.main()
