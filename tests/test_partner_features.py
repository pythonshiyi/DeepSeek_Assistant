# -*- coding: utf-8 -*-
"""伙伴化功能测试：RPA / 密钥保险箱 / 高风险审批 / 记忆扩容 / 邮件汇总。"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc
import permissions
import main as m


class TestRpaSurface(unittest.TestCase):
    def test_rpa_ready_and_schema(self):
        names = {t["function"]["name"] for t in dc.TOOLS}
        for name in ("rpa_click", "rpa_type", "rpa_hotkey", "rpa_move",
                     "rpa_scroll", "rpa_screenshot", "rpa_screen_size"):
            self.assertIn(name, names)
            self.assertIn(name, dc.TOOL_CALL_MAP)
        ok, hint = dc._rpa_ready()
        self.assertTrue(ok, hint)


class TestSecretVault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_secret_")
        dc.SECRETS_FILE = os.path.join(self.tmp, "secrets.json")
        permissions.init(os.path.join(self.tmp, "perm.json"), os.path.join(self.tmp, "ws"),
                         audit_dir=os.path.join(self.tmp, "logs"))
        permissions.set_full_auto(True)

    def tearDown(self):
        dc.SECRETS_FILE = None
        permissions.set_full_auto(False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_set_get_list_delete(self):
        self.assertIn("已加密保存", dc.secret_store("set", "api_key", "sk-123"))
        self.assertEqual(dc.secret_store("get", "api_key"), "sk-123")
        self.assertIn("api_key", dc.secret_store("list"))
        self.assertNotIn("sk-123", dc.secret_store("list"))
        self.assertIn("已删除", dc.secret_store("delete", "api_key"))
        self.assertIn("未找到", dc.secret_store("get", "api_key"))


class TestPartnerDefaults(unittest.TestCase):
    def test_high_risk_approval_defaults(self):
        data = permissions.DEFAULT_PERMISSIONS
        for name in ("delete_file", "run_command", "rpa_click", "rpa_hotkey", "webdav"):
            self.assertIn(name, data["approval_actions"])

    def test_memory_capacity(self):
        self.assertEqual(dc.MEMORY_MAX_ITEMS, 2000)

    def test_email_summary_delegates(self):
        with mock.patch("deepseek_client.read_email", return_value="邮件清单"):
            out = dc.email_summary(limit=3)
        self.assertIn("邮件清单", out)


if __name__ == "__main__":
    unittest.main()
