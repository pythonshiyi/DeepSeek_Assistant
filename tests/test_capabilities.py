# -*- coding: utf-8 -*-
"""能力增强回归测试：call_api / system_status / 自动经验复盘。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc


class FakeResp:
    def __init__(self, content=b"{}", status_code=200, ctype="application/json"):
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": ctype}

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self._resp

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._resp


class TestCallApi(unittest.TestCase):
    def test_get_success(self):
        fc = FakeClient(FakeResp(content=b'{"ok": true}', ctype="application/json"))
        with mock.patch("deepseek_client._http_client", return_value=fc):
            out = dc.call_api("https://api.example.com/v1", params={"a": 1})
        self.assertIn("HTTP 200", out)
        self.assertIn('"ok"', out)
        method, url, kw = fc.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(kw["params"], {"a": 1})

    def test_post_json_and_headers(self):
        fc = FakeClient(FakeResp(content=b'{}'))
        with mock.patch("deepseek_client._http_client", return_value=fc):
            dc.call_api("https://api.example.com/v1", method="POST",
                        json_body={"x": 1}, headers={"Authorization": "Bearer t"})
        method, url, kw = fc.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(kw["json"], {"x": 1})
        self.assertEqual(kw["headers"]["Authorization"], "Bearer t")

    def test_ssrf_blocked(self):
        for bad in ("http://10.0.0.1/x", "http://192.168.1.1/x", "http://169.254.169.254/"):
            out = dc.call_api(bad)
            self.assertIn("已阻止", out, bad)

    def test_scheme_rejected(self):
        for bad in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"):
            out = dc.call_api(bad)
            self.assertIn("错误", out, bad)

    def test_method_whitelist(self):
        self.assertIn("错误", dc.call_api("https://example.com", method="TRACE"))
        self.assertIn("错误", dc.call_api("https://example.com", method="FOO"))
        # 大小写/空白容错
        fc = FakeClient(FakeResp(content=b'{}'))
        with mock.patch("deepseek_client._http_client", return_value=fc):
            out = dc.call_api("https://example.com", method="get ")
        self.assertIn("HTTP 200", out)
        self.assertEqual(fc.calls[0][0], "GET")

    def test_header_crlf_rejected(self):
        out = dc.call_api("https://example.com", headers={"X-A": "a\r\nInjected: 1"})
        self.assertIn("错误", out)

    def test_header_name_validated(self):
        out = dc.call_api("https://example.com", headers={"bad name": "v"})
        self.assertIn("错误", out)

    def test_headers_limit(self):
        out = dc.call_api("https://example.com",
                          headers={f"H{i}": "v" for i in range(12)})
        self.assertIn("错误", out)

    def test_timeout_clamped(self):
        fc = FakeClient(FakeResp(content=b'{}'))
        with mock.patch("deepseek_client._http_client", return_value=fc):
            dc.call_api("https://example.com", timeout=999)
        self.assertEqual(fc.calls[0][2]["timeout"], 60)


class TestSystemStatus(unittest.TestCase):
    def test_basic_output(self):
        out = dc.system_status()
        self.assertIn("系统状态", out)
        self.assertIn("磁盘", out)
        self.assertIn("网络", out)

    def test_degrades_without_psutil(self):
        with mock.patch.dict("sys.modules", {"psutil": None}):
            out = dc.system_status()
        self.assertIn("psutil", out)  # CPU/内存降级提示
        self.assertIn("磁盘", out)

    def test_network_probe(self):
        with mock.patch("socket.create_connection", side_effect=OSError("offline")):
            out = dc.system_status()
        self.assertIn("✗", out)


class TestReflection(unittest.TestCase):
    def test_record_reflection_writes_memory(self):
        """失败任务自动写复盘记忆（含失败工具与错误首行）。"""
        import main as m

        app = mock.MagicMock()
        app.cfg = {"privacy_mode": False}
        app.messages = [
            {"role": "user", "content": "帮我整理数据并生成图表"},
            {"role": "assistant", "content": "好的"},
        ]
        fail_items = [
            {"tool": "chart_data", "error": "错误：kind 不合法", "args": "{}", "ts": "t"},
            {"tool": "chart_data", "error": "错误：kind 不合法", "args": "{}", "ts": "t"},
            {"tool": "read_csv", "error": "错误：文件不存在", "args": "{}", "ts": "t"},
        ]
        with mock.patch.object(dc, "write_memory") as wm:
            m.AssistantApp._record_reflection(app, 1, 2, fail_items)
        self.assertEqual(wm.call_count, 1)
        text, category = wm.call_args[0]
        self.assertIn("任务复盘：帮我整理数据并生成图表", text)
        self.assertIn("工具 1 成功 / 2 失败", text)
        self.assertIn("chart_data", text)
        self.assertIn("read_csv", text)
        self.assertEqual(category, "经验复盘")

    def test_reflection_skips_in_privacy(self):
        import main as m

        app = mock.MagicMock()
        app.cfg = {"privacy_mode": True}
        with mock.patch.object(dc, "write_memory") as wm:
            m.AssistantApp._record_reflection(app, 0, 1, [])
        wm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
