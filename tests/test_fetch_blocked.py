# -*- coding: utf-8 -*-
"""fetch_blocked（按需代理能力）单元测试：SSRF / 参数校验 / 节点解析 / 节点池缓存。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetch_blocked import (
    fetch_blocked,
    _is_blocked_host,
    _parse_yaml_nodes,
    _discover_nodes,
    _pick_node,
    _test_node,
)
import fetch_blocked as fb_mod
import permissions


def _set_security_mode(mode):
    if permissions.get_data() is None:
        import json as _json
        permissions.set_data(_json.loads(_json.dumps(permissions.DEFAULT_PERMISSIONS)))
    data = permissions.get_data()
    old = data.get("security_mode", "blacklist")
    data["security_mode"] = mode
    return old


SAMPLE_YAML = """
proxies:
  - name: "JP 1"
    type: http
    server: jp1.example.com
    port: 443
    username: u1
    password: p1
    tls: true
  - name: "HK 2"
    type: http
    server: hk2.example.com
    port: 80
    username: u2
    password: p2
    tls: false
  - name: "VMess"
    type: vmess
    server: vm.example.com
    port: 443
"""


class TestFetchBlockedValidation(unittest.TestCase):
    def test_illegal_scheme(self):
        self.assertIn("http://", fetch_blocked("ftp://x"))

    def test_bad_proxy_format(self):
        self.assertIn("proxy 格式", fetch_blocked("https://example.com/a", proxy="http://bad"))

    def test_ssrf_internal_blocked(self):
        old = _set_security_mode("whitelist")
        try:
            for u in ("http://192.168.1.5/x", "http://10.0.0.1/", "http://169.254.169.254/"):
                self.assertIn("SSRF", fetch_blocked(u), u)
        finally:
            _set_security_mode(old)

    def test_blacklist_mode_allows_internal(self):
        old = _set_security_mode("blacklist")
        try:
            data = permissions.get_data()
            data.setdefault("network", {})["blocklist"] = []
            for u in ("http://192.168.1.5/x", "http://10.0.0.1/"):
                self.assertNotIn("SSRF", fetch_blocked(u), u)
        finally:
            _set_security_mode(old)

    def test_ssrf_loopback_allowed(self):
        """回环放行——应进入后续抓取流程。"""
        with mock.patch("fetch_blocked._discover_nodes", return_value=[]):
            out = fetch_blocked("http://127.0.0.1:1/")
        self.assertNotIn("SSRF", out)

    def test_is_blocked_host(self):
        old = _set_security_mode("whitelist")
        try:
            self.assertTrue(_is_blocked_host("192.168.1.1"))
            self.assertTrue(_is_blocked_host("169.254.169.254"))
            self.assertTrue(_is_blocked_host("10.1.2.3"))
            with mock.patch("fetch_blocked.socket.getaddrinfo",
                            return_value=[(__import__("socket").AF_INET,
                                           __import__("socket").SOCK_STREAM, 6, "",
                                           ("31.13.72.36", 0))]):
                self.assertFalse(_is_blocked_host("linux.do"))
            self.assertFalse(_is_blocked_host("127.0.0.1"))
            self.assertFalse(_is_blocked_host("localhost"))
        finally:
            _set_security_mode(old)

    def test_blacklist_host_only_blocks_listed(self):
        old = _set_security_mode("blacklist")
        try:
            data = permissions.get_data()
            data.setdefault("network", {})["blocklist"] = ["10.1.2.3"]
            self.assertTrue(_is_blocked_host("10.1.2.3"))
            self.assertFalse(_is_blocked_host("192.168.1.1"))
        finally:
            _set_security_mode(old)


class TestNodeParsing(unittest.TestCase):
    def test_parse_yaml_http_only(self):
        nodes = _parse_yaml_nodes(SAMPLE_YAML)
        self.assertEqual(len(nodes), 2)  # vmess 被排除
        jp = next(n for n in nodes if n["server"] == "jp1.example.com")
        self.assertEqual(jp["username"], "u1")
        self.assertEqual(jp["password"], "p1")
        self.assertTrue(jp["tls"])

    def test_discover_nodes_skips_missing_dirs(self):
        """目录不存在时返回空（不抛异常，不触碰真实订阅）。"""
        with mock.patch("fetch_blocked.NODE_DIRS", [r"Z:\nonexistent_dir"]):
            self.assertEqual(_discover_nodes(), [])

    def test_discover_nodes_reads_yaml(self):
        """从订阅缓存目录读取并解析（用临时目录隔离，避免触碰真实订阅）。"""
        tmp = tempfile.mkdtemp(prefix="fb_nodes_")
        try:
            with open(os.path.join(tmp, "sub.yaml"), "w", encoding="utf-8") as f:
                f.write("proxies:\n" + SAMPLE_YAML)
            with mock.patch("fetch_blocked.NODE_DIRS", [tmp]):
                nodes = _discover_nodes()
            self.assertEqual(len(nodes), 2)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestNodePool(unittest.TestCase):
    def setUp(self):
        fb_mod._NODE_CACHE = {"ts": 0.0, "node": None}

    def tearDown(self):
        fb_mod._NODE_CACHE = {"ts": 0.0, "node": None}

    def test_pick_node_none_empty(self):
        self.assertIsNone(_pick_node([]))

    def test_pick_node_caches(self):
        """节点池 TTL 缓存：二次调用不再重测。"""
        node = {"server": "jp1.example.com", "port": 443, "username": "u", "password": "p", "name": "t"}
        with mock.patch("fetch_blocked._test_node", return_value=0.5) as tn:
            picked = _pick_node([node])
            self.assertIs(picked, node)
            _pick_node([node])
            self.assertEqual(tn.call_count, 1)  # 命中缓存

    def test_all_nodes_dead(self):
        node = {"server": "dead.example.com", "port": 1, "username": "u", "password": "p", "name": "d"}
        with mock.patch("fetch_blocked._test_node", return_value=None):
            self.assertIsNone(_pick_node([node]))


class TestFetchViaProxy(unittest.TestCase):
    def test_curl_path_impersonates_chrome(self):
        """主路径：curl_cffi + Chrome 指纹 + 代理参数正确传递。"""
        node = {"server": "jp1.example.com", "port": 443, "username": "u1", "password": "p1", "name": "t"}
        fake_cr = mock.MagicMock()
        fake_cr.get.return_value.raise_for_status.return_value = None
        fake_cr.get.return_value.content = "内容".encode("utf-8")
        fake_cr.get.return_value.headers = {"content-type": "text/html; charset=utf-8"}
        with mock.patch("fetch_blocked._cr", fake_cr):
            from fetch_blocked import _fetch_via_proxy_curl
            out = _fetch_via_proxy_curl("https://linux.do/t/1", node)
        self.assertIn("内容", out)
        args, kwargs = fake_cr.get.call_args
        self.assertEqual(kwargs["impersonate"], "chrome")
        self.assertIn("jp1.example.com", kwargs["proxy"])

    def test_no_curl_cffi_raises(self):
        """缺 curl_cffi 时抛 RuntimeError（由入口降级到标准库路径）。"""
        node = {"server": "x", "port": 443, "username": "u", "password": "p", "name": "t"}
        with mock.patch("fetch_blocked._cr", None):
            from fetch_blocked import _fetch_via_proxy_curl
            with self.assertRaises(RuntimeError):
                _fetch_via_proxy_curl("https://x.com/", node)

    def test_entry_degrades_to_stdlib_on_missing_curl(self):
        """入口自动降级：curl_cffi 缺失 → 标准库路径（返回错误而非崩溃）。"""
        node = {"server": "x", "port": 443, "username": "u", "password": "p", "name": "t"}
        with mock.patch("fetch_blocked._discover_nodes", return_value=[node]), \
             mock.patch("fetch_blocked._pick_node", return_value=node), \
             mock.patch("fetch_blocked._fetch_via_proxy_stdlib", return_value="stdlib ok"), \
             mock.patch("fetch_blocked._fetch_via_proxy_curl",
                        side_effect=RuntimeError("缺少 curl_cffi，请先 pip install curl_cffi")):
            out = fetch_blocked("https://linux.do/latest.rss")
        self.assertEqual(out, "stdlib ok")


if __name__ == "__main__":
    unittest.main()
