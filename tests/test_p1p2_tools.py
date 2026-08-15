# -*- coding: utf-8 -*-
"""P1/P2 能力扩展测试：download_file / 文件格式扩展 / IM 通道 / 浏览器动作补全。"""
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc
import permissions
import wecom_aibot


def _init_blacklist_perm(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(ws, exist_ok=True)
    permissions.init(os.path.join(tmp, "perm.json"), ws, audit_dir=tmp)
    data = permissions.get_data()
    data["security_mode"] = "blacklist"
    data["filesystem"]["blocked_dirs"] = [
        d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
    ]
    permissions.set_full_auto(True)
    return ws


class _FakeStreamCM:
    def __init__(self, chunks=b"", status=200, headers=None):
        self.chunks = [chunks] if isinstance(chunks, bytes) else chunks
        self.status_code = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self, _n):
        yield from self.chunks


class TestDownloadFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_p1_dl_")
        self.ws = _init_blacklist_perm(self.tmp)

    def tearDown(self):
        permissions.set_full_auto(False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_download_to_default_dir(self):
        with mock.patch("deepseek_client._safe_url", return_value=""), \
             mock.patch("deepseek_client._safe_stream", return_value=_FakeStreamCM(b"hello-binary")) as ss:
            out = dc.download_file("https://example.com/a.png")
        self.assertIn("已下载", out)
        self.assertIn("a.png", out)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "downloads", "a.png")))

    def test_download_size_capped(self):
        with mock.patch.object(dc, "DOWNLOAD_MAX_BYTES", 1024), \
             mock.patch("deepseek_client._safe_url", return_value=""), \
             mock.patch("deepseek_client._safe_stream", return_value=_FakeStreamCM(chunks=[b"x" * 1024, b"y" * 1024])):
            out = dc.download_file("https://example.com/big.bin")
        self.assertIn("上限", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "downloads", "big.bin")))

    def test_download_ssrf_rejected(self):
        out = dc.download_file("ftp://example.com/a")
        self.assertIn("http", out)


class TestFileFormatExtensions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_p1_fmt_")
        self.ws = _init_blacklist_perm(self.tmp)

    def tearDown(self):
        permissions.set_full_auto(False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_archive_list_zip(self):
        p = os.path.join(self.ws, "a.zip")
        import zipfile
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("x.txt", "hello")
        out = dc.archive_list(p)
        self.assertIn("x.txt", out)

    def test_archive_list_tar(self):
        p = os.path.join(self.ws, "a.tar")
        with tarfile.open(p, "w") as tf:
            data = b"hello"
            info = tarfile.TarInfo("y.txt")
            info.size = len(data)
            tf.addfile(info, __import__("io").BytesIO(data))
        out = dc.archive_list(p)
        self.assertIn("y.txt", out)

    def test_extract_tar(self):
        p = os.path.join(self.ws, "a.tar")
        with tarfile.open(p, "w") as tf:
            data = b"tar-body"
            info = tarfile.TarInfo("inner/z.txt")
            info.size = len(data)
            tf.addfile(info, __import__("io").BytesIO(data))
        dest = os.path.join(self.ws, "out")
        out = dc.extract_archive(p, dest)
        self.assertIn("已解压", out)
        self.assertEqual(open(os.path.join(dest, "inner", "z.txt"), encoding="utf-8").read(), "tar-body")

    def test_epub_read_missing_dep(self):
        p = os.path.join(self.ws, "a.epub")
        with open(p, "w", encoding="utf-8") as f:
            f.write("not epub")
        with mock.patch.dict(sys.modules, {"ebooklib": None}):
            import builtins
            real_import = builtins.__import__

            def fake_import(name, *a, **kw):
                if name == "ebooklib" or name.startswith("ebooklib."):
                    raise ImportError("no ebooklib")
                return real_import(name, *a, **kw)

            with mock.patch("builtins.__import__", side_effect=fake_import):
                out = dc.epub_read(p)
        self.assertIn("需要 ebooklib", out)

    def test_msg_read_missing_dep(self):
        p = os.path.join(self.ws, "a.msg")
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        with mock.patch("builtins.__import__", side_effect=ImportError("no extract_msg")):
            out = dc.msg_read(p)
        self.assertIn("extract_msg", out)


class _FakeResp:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data if json_data is not None else {}
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class TestIMChannels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_im_")
        _init_blacklist_perm(self.tmp)
        self.cfg_path = os.path.join(self.tmp, "im_config.json")
        dc.IM_CONFIG_FILE = self.cfg_path

    def tearDown(self):
        dc.IM_CONFIG_FILE = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_cfg(self, data):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_im_send_missing_config(self):
        self.assertIn("未配置", dc.im_send("hi"))

    def test_im_send_telegram(self):
        self._write_cfg({"telegram_bot_token": "tok", "telegram_chat_id": "42"})
        fake = _FakeResp(status_code=200, json_data={"ok": True})
        with mock.patch("deepseek_client._http_client") as hc:
            hc.return_value.post.return_value = fake
            out = dc.im_send("hello", title="T")
        self.assertIn("telegram:✅", out)

    def test_telegram_poll_updates(self):
        self._write_cfg({"telegram_bot_token": "tok", "telegram_chat_id": "42"})
        fake = _FakeResp(status_code=200, json_data={
            "ok": True,
            "result": [
                {"update_id": 7, "message": {"chat": {"id": 42}, "from": {"username": "me"}, "text": "召唤"}}
            ],
        })
        with mock.patch("deepseek_client._http_client") as hc:
            hc.return_value.post.return_value = fake
            out = dc.telegram_poll_updates(timeout=1)
        self.assertIn("@me: 召唤", out)


class TestAgentMail(unittest.TestCase):
    def tearDown(self):
        dc.AGENT_MAIL_ENABLED = False
        dc.AGENT_MAIL_CLI = "agently-cli"

    def test_disabled_returns_tip(self):
        dc.AGENT_MAIL_ENABLED = False
        out = dc.agent_mail("me")
        self.assertIn("未启用", out)

    def test_me_action(self):
        dc.AGENT_MAIL_ENABLED = True
        with mock.patch("deepseek_client._agent_mail_run", return_value=(0, "whaletalk@agent.qq.com")):
            out = dc.agent_mail("me")
        self.assertIn("whaletalk@agent.qq.com", out)

    def test_send_requires_to_and_subject(self):
        dc.AGENT_MAIL_ENABLED = True
        self.assertIn("to", dc.agent_mail("send", body="x"))

    def test_confirmation_hint(self):
        dc.AGENT_MAIL_ENABLED = True
        with mock.patch("deepseek_client._agent_mail_run", return_value=(8, "ctk_1")):
            out = dc.agent_mail("send", to="a@b.com", subject="t", body="b")
        self.assertIn("需要用户确认", out)

    def test_resolve_cmd_shim(self):
        with mock.patch("shutil.which", return_value=r"C:\Users\me\AppData\Roaming\npm\agently-cli.CMD"):
            resolved = dc._resolve_agent_mail_cli("agently-cli")
        self.assertTrue(resolved.lower().endswith(".cmd"))

    def test_run_does_not_duplicate_cli(self):
        dc.AGENT_MAIL_CLI = "agently-cli"
        with mock.patch("shutil.which", return_value=r"C:\npm\agently-cli.CMD"), \
             mock.patch("subprocess.run", return_value=type("P", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()) as run:
            code, out = dc._agent_mail_run(["+me"])
        self.assertEqual(run.call_args.args[0][0], r"C:\npm\agently-cli.CMD")
        self.assertEqual(run.call_args.args[0][1], "+me")


class TestWecomAibotChannel(unittest.TestCase):
    def test_parse_text_frame(self):
        got = []
        listener = wecom_aibot.AibotListener(
            "bid", "sec",
            on_message=lambda chatid, user, text, frame: got.append((chatid, user, text)),
        )
        listener._on_text({
            "body": {
                "msgtype": "text",
                "chatid": "chat-1",
                "from": {"name": "张三", "userid": "u1"},
                "text": {"content": "鲸语在吗"},
            },
        })
        self.assertEqual(got, [("chat-1", "张三", "鲸语在吗")])

    def test_reply_requires_client(self):
        listener = wecom_aibot.AibotListener("bid", "sec")
        self.assertFalse(listener.reply_text({"headers": {"req_id": "r1"}}, "hi"))

    def test_reply_body_uses_stream_type(self):
        body = wecom_aibot.AibotListener._reply_body("hello")
        self.assertEqual(body["msgtype"], "stream")
        self.assertTrue(body["stream"]["finish"])
        self.assertEqual(body["stream"]["content"], "hello")

    def test_send_body_uses_markdown_type(self):
        body = wecom_aibot.AibotListener._send_body("hello")
        self.assertEqual(body["msgtype"], "markdown")
        self.assertEqual(body["markdown"]["content"], "hello")


class TestToolRegistration(unittest.TestCase):
    def test_new_tools_registered(self):
        names = {t["function"]["name"] for t in dc.TOOLS}
        for name in ("im_send", "telegram_poll_updates", "download_file",
                     "epub_read", "mobi_read", "doc_read", "msg_read", "archive_list"):
            self.assertIn(name, names)
            self.assertIn(name, dc.TOOL_CALL_MAP)


if __name__ == "__main__":
    unittest.main()
