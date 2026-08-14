# -*- coding: utf-8 -*-
"""工具精修回归测试：get_weather 日期 / send_email 收件人 / read_csv·read_excel 单元格截断 /
fetch_url 编码 / environment_info 包检测 / PG 语句超时 / cron 值域 / image 系列校验 /
run_command·start_process cwd / read_file 超长行 / schedule 值域。"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc
import permissions


def _init_perm(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(ws, exist_ok=True)
    permissions.init(os.path.join(tmp, "perm.json"), ws)
    data = permissions.get_data()
    data["filesystem"]["blocked_dirs"] = [
        d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
    ]
    permissions.set_full_auto(True)
    return ws


class _Resp:
    def __init__(self, text="", json_data=None, headers=None, status=200):
        self.text = text
        self._json = json_data
        self.headers = headers or {}
        self.status_code = status

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class TestGetWeather(unittest.TestCase):
    def test_date_passed_to_wttr(self):
        captured = {}

        class C:
            def get(self, url, timeout=0):
                captured["url"] = url
                return _Resp(json_data={"current_condition": [{"temp_C": "22", "lang_zh": [{"value": "晴"}]}]})

        with mock.patch("deepseek_client._http_client", return_value=C()):
            out = dc.get_weather("北京", "2026-08-12")
        self.assertIn("晴", out)
        self.assertIn("date=2026-08-12", captured["url"])
        self.assertIn("2026-08-12", out)

    def test_no_location_error(self):
        self.assertIn("错误", dc.get_weather("", "2026-08-12"))

    def test_today_without_date(self):
        captured = {}

        class C:
            def get(self, url, timeout=0):
                captured["url"] = url
                return _Resp(json_data={"current_condition": [{"temp_C": "10", "lang_zh": [{"value": "多云"}]}]})

        with mock.patch("deepseek_client._http_client", return_value=C()):
            out = dc.get_weather("上海", "")
        self.assertNotIn("date=", captured["url"])
        self.assertIn("多云", out)


class TestSendEmail(unittest.TestCase):
    def test_multiple_recipients_as_list(self):
        tmp = tempfile.mkdtemp(prefix="dsa_mail_")
        dc.EMAIL_CONFIG_FILE = os.path.join(tmp, "email_config.json")
        with open(dc.EMAIL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"smtp_host": "smtp.x.com", "smtp_port": 465,
                       "user": "u@x.com", "password": "p"}, f)
        server = mock.MagicMock()
        with mock.patch("smtplib.SMTP_SSL", return_value=server):
            out = dc.send_email("a@b.com, c@d.com", "标题", "正文")
        self.assertIn("已发送", out)
        args = server.sendmail.call_args[0]
        self.assertEqual(args[1], ["a@b.com", "c@d.com"])  # 修复前是 ["a@b.com, c@d.com"]


class TestReadCsvTruncation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_csv_")
        self.ws = _init_perm(self.tmp)
        self.p = os.path.join(self.ws, "t.csv")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wide_cell_truncated(self):
        with open(self.p, "w", encoding="utf-8", newline="") as f:
            f.write("a,b\n" + "x" * 5000 + ",y\n")
        out = dc.read_csv(self.p)
        # 5000 字符单元格被截到 101 字符以内（100 + 省略号）
        for line in out.splitlines()[1:]:
            self.assertLessEqual(max(len(c) for c in line.split(" | ")), 101)

    def test_multi_char_delimiter_rejected(self):
        with open(self.p, "w", encoding="utf-8") as f:
            f.write("a,b\n1,2\n")
        out = dc.read_csv(self.p, delimiter=";;")
        self.assertIn("单个字符", out)


class TestWriteCsvMixedRows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_csv2_")
        self.ws = _init_perm(self.tmp)
        self.p = os.path.join(self.ws, "m.csv")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dict_with_scalar_row(self):
        out = dc.write_csv(self.p, [{"a": 1, "b": 2}, "标量行"])
        self.assertIn("已写入", out)
        data = json.load(open(self.p, encoding="utf-8-sig")) if False else open(self.p, encoding="utf-8-sig").read()
        self.assertIn("1", data)


class TestFetchUrlEncoding(unittest.TestCase):
    def test_gbk_page_decoded(self):
        gbk_body = "中文网页内容测试".encode("gbk")

        class S:
            headers = {"content-type": "text/html; charset=gbk"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def raise_for_status(self):
                pass

            def iter_bytes(self, size):
                yield gbk_body

        class C:
            def stream(self, method, url, timeout=0):
                return S()

        with mock.patch("deepseek_client._http_client", return_value=C()):
            out = dc.fetch_url("https://example.com/gbk")
        self.assertIn("中文网页内容测试", out)
        self.assertNotIn("锟", out)  # 乱码特征

    def test_utf8_page_decoded(self):
        utf8_body = "UTF-8 内容".encode("utf-8")

        class S:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def raise_for_status(self):
                pass

            def iter_bytes(self, size):
                yield utf8_body

        class C:
            def stream(self, method, url, timeout=0):
                return S()

        with mock.patch("deepseek_client._http_client", return_value=C()):
            out = dc.fetch_url("https://example.com/utf8")
        self.assertIn("UTF-8 内容", out)


class TestEnvironmentInfo(unittest.TestCase):
    def test_pillow_mapped_to_PIL(self):
        def fake_spec(name):
            return object() if name == "PIL" else None

        with mock.patch("importlib.util.find_spec", side_effect=fake_spec):
            out = dc.environment_info()
        self.assertIn("pillow", out)
        self.assertNotIn("numpy", out)


class TestPostgresTimeout(unittest.TestCase):
    def test_statement_timeout_set(self):
        pg = mock.MagicMock()
        conn = mock.MagicMock()
        conn.cursor.return_value.description = None
        pg.connect.return_value = conn
        with mock.patch.dict(sys.modules, {"psycopg2": pg}):
            with mock.patch("deepseek_client._db_conn", return_value=({"host": "h", "user": "u", "password": "p"}, "")):
                dc.database_query_postgres(sql="SELECT 1")
        kwargs = pg.connect.call_args.kwargs
        self.assertIn("statement_timeout=15000", kwargs.get("options", ""))


class TestImageGenerateSize(unittest.TestCase):
    def test_invalid_size_rejected(self):
        dc.IMAGE_GEN_KEY = "k"
        try:
            out = dc.image_generate("一只猫", size="9999x9999")
            self.assertIn("size", out)
            out2 = dc.image_generate("一只猫", size="abc")
            self.assertIn("size", out2)
        finally:
            dc.IMAGE_GEN_KEY = None

    def test_valid_size_accepted(self):
        dc.IMAGE_GEN_KEY = "k"
        dc.IMAGE_GEN_BASE = "https://example.com"
        try:
            with mock.patch("deepseek_client._http_client") as http:
                http.return_value.post.return_value.raise_for_status.return_value = None
                http.return_value.post.return_value.json.return_value = {
                    "data": [{"b64_json": "aW1n"}]
                }
                with mock.patch("deepseek_client.permissions.check_filesystem", return_value=(True, "")):
                    with mock.patch("deepseek_client.permissions.resolve", side_effect=lambda p: p or os.path.join(tempfile.gettempdir(), "t.png")):
                        import tempfile as _tf

                        out = dc.image_generate("一只猫", path=os.path.join(_tf.gettempdir(), "g.png"), size="1024x1024")
            self.assertIn("已生成", out)
        finally:
            dc.IMAGE_GEN_KEY = None


class TestChartValidation(unittest.TestCase):
    def test_non_numeric_rejected(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib 未安装")
        with mock.patch("deepseek_client.permissions.check_filesystem", return_value=(True, "")):
            out = dc.chart_data([1, "abc", 3], os.path.join(tempfile.gettempdir(), "c.png"))
        self.assertIn("非数值", out)

    def test_pie_zero_rejected(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib 未安装")
        with mock.patch("deepseek_client.permissions.check_filesystem", return_value=(True, "")):
            out = dc.chart_data([0, 0, 0], os.path.join(tempfile.gettempdir(), "p.png"), kind="pie")
        self.assertIn("正值", out)

    def test_bad_kind_rejected(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib 未安装")
        with mock.patch("deepseek_client.permissions.check_filesystem", return_value=(True, "")):
            out = dc.chart_data([1, 2], os.path.join(tempfile.gettempdir(), "k.png"), kind="bogus")
        self.assertIn("kind", out)


class TestScheduleCronRange(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_sched2_")
        dc.SCHEDULES_FILE = os.path.join(self.tmp, "schedules.json")

    def tearDown(self):
        dc.SCHEDULES_FILE = None
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_out_of_range_rejected(self):
        for expr in ("99 9 * * 1", "30 25 * * 1", "30 9 32 * 1", "30 9 * 13 1", "30 9 * * 8"):
            out = dc.schedule_task(expr_type="cron", expr=expr, content="x")
            self.assertIn("错误", out, expr)

    def test_range_ok(self):
        out = dc.schedule_task(expr_type="cron", expr="*/15 9-18 * * 1-5", content="x")
        self.assertIn("已创建", out)


class TestCommandCwd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_cwd_")
        self.ws = _init_perm(self.tmp)
        dc.WORKING_DIR = self.ws

    def tearDown(self):
        dc.WORKING_DIR = None
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_command_uses_working_dir(self):
        with mock.patch("deepseek_client.permissions.check_shell", return_value=(True, "", ["python", "-V"])):
            with mock.patch("deepseek_client.subprocess.Popen") as popen:
                popen.return_value.wait.return_value = 0
                popen.return_value.returncode = 0
                dc.run_command("python -V")
        self.assertEqual(popen.call_args.kwargs.get("cwd"), self.ws)

    def test_start_process_uses_working_dir(self):
        with mock.patch("deepseek_client.permissions.check_shell", return_value=(True, "", ["python", "-m", "http.server"])):
            with mock.patch("deepseek_client.subprocess.Popen") as popen:
                popen.return_value.poll.return_value = None
                popen.return_value.pid = 123
                dc.start_process("python -m http.server", name="srv")
        self.assertEqual(popen.call_args.kwargs.get("cwd"), self.ws)


class TestReadFileLongLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_rl_")
        self.ws = _init_perm(self.tmp)
        self.p = os.path.join(self.ws, "big.txt")
        with open(self.p, "w", encoding="utf-8") as f:
            f.write("x" * (300 * 1024) + "\nline2\n")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_long_line_truncated(self):
        out = dc.read_file(self.p, start_line=1, max_lines=1)
        self.assertIn("已截断", out)
        self.assertLess(len(out), 120 * 1024)  # 不整行进内存


class TestImageProcessOps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_img_")
        self.ws = _init_perm(self.tmp)
        from PIL import Image

        src = os.path.join(self.ws, "s.png")
        Image.new("RGB", (100, 80), "#336699").save(src)
        self.src = src
        self.out = os.path.join(self.ws, "o.png")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_crop_missing_args(self):
        out = dc.image_process(self.src, self.out, ops="crop=0,0")
        self.assertIn("crop", out)
        self.assertIn("错误", out)

    def test_resize_bad_format(self):
        out = dc.image_process(self.src, self.out, ops="resize=abc")
        self.assertIn("错误", out)

    def test_valid_ops(self):
        out = dc.image_process(self.src, self.out, ops="resize=50x40;rotate=90;quality=85")
        self.assertIn("已处理", out)
        self.assertIn("3 项操作", out)

    def test_unknown_ops_copy(self):
        out = dc.image_process(self.src, self.out, ops="bogus=1")
        self.assertIn("未做处理", out)


class TestSearchWebFilter(unittest.TestCase):
    def test_dangerous_links_filtered(self):
        results = [
            {"title": "恶意", "url": "javascript:alert(1)", "snippet": ""},
            {"title": "正常", "url": "https://example.com/a", "snippet": "说明"},
        ]
        with mock.patch("deepseek_client._search_bing", return_value=results):
            out = dc.search_web("测试")
        self.assertNotIn("javascript", out)
        self.assertIn("正常", out)


if __name__ == "__main__":
    unittest.main()
