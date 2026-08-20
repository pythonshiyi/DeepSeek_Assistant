# -*- coding: utf-8 -*-
"""Tool SDK 与本地 API 沙箱测试。"""
import json
import sys
import os
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tool_sdk
import api_server
import plugins


class TestToolSDK:
    def test_validate_rejects_bad_endpoint(self):
        tool = {"type": "function", "function": {
            "name": "x", "description": "d",
            "parameters": {"type": "object", "properties": {}},
            "endpoint": "ftp://bad",
        }}
        ok, err = tool_sdk.validate_tool(tool)
        assert not ok
        assert "http(s)" in err

    def test_generate_template(self):
        tool = tool_sdk.generate_tool_template("my_api", "desc", "https://e.com/x", "a,b", "POST")
        assert tool["function"]["name"] == "my_api"
        assert set(tool["function"]["parameters"]["required"]) == {"a", "b"}
        assert tool["function"]["endpoint"] == "https://e.com/x"

    def test_validate_user_tools(self, tmp_path):
        p = tmp_path / "user_tools.json"
        p.write_text(json.dumps([{"function": {"name": "ok", "endpoint": "https://e.com/x",
                                                "parameters": {"type": "object", "properties": {}}}}]),
                     encoding="utf-8")
        assert tool_sdk.validate_user_tools(str(p)) == []


class TestAPIServer:
    def test_start_stop_and_auth(self):
        port, token, err = api_server.start_server(8746, "test-secret", lambda: ["get_date"], None)
        assert err is None
        assert token == "test-secret"
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Authorization": "Bearer wrong"})
            try:
                urllib.request.urlopen(req, timeout=5)
                noauth = False
            except urllib.error.HTTPError as e:
                noauth = e.code == 401
            assert noauth

            req2 = urllib.request.Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Authorization": "Bearer test-secret"})
            with urllib.request.urlopen(req2, timeout=5) as r:
                assert r.status == 200
        finally:
            api_server.stop_server()


class TestPluginRating:
    def test_save_and_summary(self, tmp_path):
        plugins_dir = str(tmp_path)
        ok, err = plugins.save_rating(plugins_dir, "my_plug", 5, "好用")
        assert ok, err
        ok2, _ = plugins.save_rating(plugins_dir, "my_plug", 4)
        assert ok2
        summary = plugins.plugin_rating_summary(plugins_dir, "my_plug")
        assert summary is not None
        assert summary["count"] == 2
        assert summary["avg"] == 4.5
