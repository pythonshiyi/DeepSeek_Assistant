# -*- coding: utf-8 -*-
"""新增修复/功能回归：Ctrl+Backspace、流式 call_api/WebDAV、DNS 重绑定、
原子写、HTTP 客户端退出竞态、长任务线程池隔离、自定义主题/快捷键配置入口。"""
import contextlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import tkinter as tk
    _probe = tk.Tk()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False

import deepseek_client as dc
import fetch_blocked as fb
import net_utils
import permissions
import main as m


class TestCtrlBackspace(unittest.TestCase):
    """Ctrl+Backspace 多行/多词删除范围修复。"""

    def _run_delete(self, content, insert="end"):
        if not TK_AVAILABLE:
            self.skipTest("tkinter 不可用")
        root = tk.Tk()
        root.withdraw()
        try:
            text = tk.Text(root)
            text.pack()
            text.insert("1.0", content)
            text.mark_set("insert", insert)
            dummy = type("Dummy", (), {
                "input_text": text,
                "_clear_placeholder": lambda *a: None,
                "focus_set": lambda *a: None,
            })()
            m.AssistantApp._on_input_delete_word(dummy)
            return text.get("1.0", "end-1c")
        finally:
            root.destroy()

    def test_single_line_deletes_last_word(self):
        self.assertEqual(self._run_delete("hello world"), "hello ")

    def test_multiline_uses_char_offset_not_line_number(self):
        # 旧实现 f"{pos+1}.0" 在多行时会删错位置；修复后应只删最后单词
        self.assertEqual(self._run_delete("第一行\nhello world"), "第一行\nhello ")

    def test_trailing_spaces_swallowed(self):
        self.assertEqual(self._run_delete("hello   "), "")


class _FakeStreamResp:
    def __init__(self, chunks=b"", status=200, headers=None):
        self._chunks = [chunks] if isinstance(chunks, bytes) else chunks
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self, _size):
        yield from self._chunks


class _FakeStreamCM:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self.resp

    def __exit__(self, *exc):
        return False


class TestCallApiStreaming(unittest.TestCase):
    def test_streams_response_and_truncates(self):
        big = b"a" * (600 * 1024)
        fake = _FakeStreamResp(chunks=[big], headers={"content-type": "text/plain"})
        cm = _FakeStreamCM(fake)
        with mock.patch("deepseek_client._safe_url", return_value=""), \
             mock.patch("deepseek_client._safe_request", side_effect=AssertionError("不应走 _safe_request")), \
             mock.patch("deepseek_client._safe_stream", return_value=cm) as ss:
            out = dc.call_api("https://example.com/api", timeout=5)
        self.assertIn("截断", out)
        self.assertTrue(ss.called)


class TestWebdavStreamingUpload(unittest.TestCase):
    def test_upload_uses_stream_when_available(self):
        tmp = tempfile.mkdtemp(prefix="dsa_wd_stream_")
        try:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws, exist_ok=True)
            permissions.init(os.path.join(tmp, "perms.json"), ws, audit_dir=tmp)
            data = permissions.get_data()
            data["filesystem"]["blocked_dirs"] = [
                d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
            ]
            permissions.set_full_auto(True)
            src = os.path.join(ws, "up.bin")
            with open(src, "wb") as f:
                f.write(b"x" * 4096)
            cfg_path = os.path.join(tmp, "webdav.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"url": "https://dav.example.com", "username": "u", "password": "p"}, f)
            old_cfg = dc.WEBDAV_CONFIG_FILE
            dc.WEBDAV_CONFIG_FILE = cfg_path

            class FakeClient:
                stream = True  # 标记支持流式

            fake_resp = _FakeStreamResp(status=201)
            cm = _FakeStreamCM(fake_resp)
            with mock.patch("deepseek_client._http_client", return_value=FakeClient()), \
                 mock.patch("deepseek_client._safe_stream", return_value=cm) as ss:
                out = dc.webdav(action="upload", remote_path="/up.bin", local_path=src)
            self.assertIn("已上传", out)
            self.assertTrue(ss.called)
            content = ss.call_args.kwargs.get("content")
            self.assertFalse(isinstance(content, bytes), "流式上传不应把整个文件读成 bytes")
            self.assertTrue(hasattr(content, "__iter__"))
        finally:
            dc.WEBDAV_CONFIG_FILE = None
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestFetchBlockedDnsRebinding(unittest.TestCase):
    def _set_mode(self, mode):
        data = permissions.get_data()
        if data is None:
            data = {"security_mode": mode, "network": {"blocklist": []}}
            permissions.set_data(data)
            return "blacklist"
        old = data.get("security_mode", "blacklist")
        data["security_mode"] = mode
        return old

    def test_whitelist_mode_blocks_private_dns(self):
        old = self._set_mode("whitelist")
        try:
            def fake_getaddrinfo(host, port, family=0, type_=0, proto=0, flags=0):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.99", 80))]

            import socket
            with mock.patch("fetch_blocked.socket.getaddrinfo", side_effect=fake_getaddrinfo):
                self.assertTrue(fb._is_blocked_host("evil.example.com"))
        finally:
            self._set_mode(old)

    def test_blacklist_mode_allows_private_dns(self):
        old = self._set_mode("blacklist")
        try:
            data = permissions.get_data()
            data.setdefault("network", {})["blocklist"] = []

            def fake_getaddrinfo(host, port, family=0, type_=0, proto=0, flags=0):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.99", 80))]

            import socket
            with mock.patch("fetch_blocked.socket.getaddrinfo", side_effect=fake_getaddrinfo):
                self.assertFalse(fb._is_blocked_host("evil.example.com"))
        finally:
            self._set_mode(old)

    def test_hostname_resolving_to_loopback_stays_allowed(self):
        old = self._set_mode("whitelist")
        try:
            def fake_getaddrinfo(host, port, family=0, type_=0, proto=0, flags=0):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]

            import socket
            with mock.patch("fetch_blocked.socket.getaddrinfo", side_effect=fake_getaddrinfo):
                self.assertFalse(fb._is_blocked_host("local.example.com"))
        finally:
            self._set_mode(old)


class TestPermissionsAtomicSave(unittest.TestCase):
    def test_save_writes_json_file(self):
        tmp = tempfile.mkdtemp(prefix="dsa_perm_atomic_")
        try:
            path = os.path.join(tmp, "permissions.json")
            old_path = permissions.PERMISSIONS_PATH
            old_ws = permissions.WORKSPACE_DIR
            permissions.PERMISSIONS_PATH = path
            permissions.WORKSPACE_DIR = os.path.join(tmp, "ws")
            try:
                self.assertTrue(permissions.save())
                self.assertTrue(os.path.exists(path))
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.assertIn("filesystem", data)
            finally:
                permissions.PERMISSIONS_PATH = old_path
                permissions.WORKSPACE_DIR = old_ws
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestHttpClientExitRace(unittest.TestCase):
    def test_no_new_client_after_shutdown(self):
        old_client = net_utils._HTTP_CLIENT
        old_flag = net_utils._HTTP_CLIENT_SHUTDOWN
        net_utils._HTTP_CLIENT = None
        net_utils._HTTP_CLIENT_SHUTDOWN = False
        try:
            net_utils._shutdown_http_client()
            with self.assertRaises(RuntimeError):
                net_utils._http_client()
        finally:
            net_utils._HTTP_CLIENT = old_client
            net_utils._HTTP_CLIENT_SHUTDOWN = old_flag


class TestLongTaskPoolSeparation(unittest.TestCase):
    def test_long_tools_route_to_long_pool(self):
        self.assertIs(dc._tool_executor_for("run_wechat_writer"), dc._LONG_TOOL_EXECUTOR)
        self.assertIs(dc._tool_executor_for("pip_install"), dc._LONG_TOOL_EXECUTOR)
        self.assertIs(dc._tool_executor_for("get_date"), dc._TOOL_EXECUTOR)
        self.assertIs(dc._tool_executor_for("read_file"), dc._TOOL_EXECUTOR)


class TestReadExcelReadOnly(unittest.TestCase):
    def test_load_workbook_uses_read_only(self):
        tmp = tempfile.mkdtemp(prefix="dsa_xlsx_ro_")
        try:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws, exist_ok=True)
            permissions.init(os.path.join(tmp, "perm.json"), ws, audit_dir=tmp)
            data = permissions.get_data()
            data["filesystem"]["blocked_dirs"] = [
                d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
            ]
            permissions.set_full_auto(True)
            path = os.path.join(ws, "book.xlsx")
            with open(path, "wb") as f:
                f.write(b"not real xlsx")

            class FakeCell:
                value = "x"

            class FakeRow:
                def __iter__(self):
                    return iter([FakeCell()])

            class FakeWS:
                def iter_rows(self, values_only=True):
                    yield [FakeCell()]

            class FakeWB:
                worksheets = [FakeWS()]
                active = FakeWS()

            with mock.patch("openpyxl.load_workbook", return_value=FakeWB()) as lw:
                out = dc.read_excel(path, max_rows=1)
            self.assertIn("x", out)
            self.assertTrue(lw.call_args.kwargs.get("read_only") is True)
            self.assertTrue(lw.call_args.kwargs.get("data_only") is True)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestNewConfigFeatures(unittest.TestCase):
    def test_defaults_include_new_keys(self):
        cfg = m.config_utils.normalize_config(dict(m.DEFAULT_CONFIG))
        self.assertIn("plugin_market_url", cfg)
        self.assertIn("custom_themes", cfg)
        self.assertIn("shortcuts", cfg)
        self.assertIn("update_public_key", cfg)
        self.assertEqual(cfg["custom_themes"], {})
        self.assertEqual(cfg["shortcuts"], {})

    def test_custom_theme_allowed_by_normalize(self):
        cfg = dict(m.DEFAULT_CONFIG)
        cfg["theme"] = "mytheme"
        cfg["custom_themes"] = {"mytheme": {"bg": "#123456", "name": "My"}}
        cfg = m.config_utils.normalize_config(cfg)
        self.assertEqual(cfg["theme"], "mytheme")
        self.assertIn("mytheme", cfg["custom_themes"])


if __name__ == "__main__":
    unittest.main()
