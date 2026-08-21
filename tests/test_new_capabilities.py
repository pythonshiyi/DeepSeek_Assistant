# -*- coding: utf-8 -*-
"""新能力回归测试：cron 调度 / 记忆语义检索与图谱 / 数据工具 / 自评闭环 / V4 正式版适配。"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc
import main as m
import permissions
import stats
import tokens


class TestCronMatch(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(m.cron_match("0 3 * * *", datetime(2026, 8, 6, 3, 0)))
        self.assertFalse(m.cron_match("30 3 * * *", datetime(2026, 8, 6, 3, 0)))
        self.assertFalse(m.cron_match("0 3 * * *", datetime(2026, 8, 6, 3, 1)))

    def test_step(self):
        self.assertTrue(m.cron_match("*/15 * * * *", datetime(2026, 8, 6, 10, 30)))
        self.assertFalse(m.cron_match("*/15 * * * *", datetime(2026, 8, 6, 10, 31)))

    def test_range_weekday(self):
        self.assertTrue(m.cron_match("0 9-18 * * 1-5", datetime(2026, 8, 6, 10, 0)))   # 周四
        self.assertFalse(m.cron_match("0 9-18 * * 1-5", datetime(2026, 8, 8, 10, 0)))  # 周六

    def test_comma(self):
        self.assertTrue(m.cron_match("0 3,15 * * *", datetime(2026, 8, 6, 15, 0)))
        self.assertFalse(m.cron_match("0 3,15 * * *", datetime(2026, 8, 6, 16, 0)))

    def test_invalid(self):
        self.assertFalse(m.cron_match("not a cron", datetime.now()))
        self.assertFalse(m.cron_match("", datetime.now()))


class TestMemorySemantic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_mem2_")
        dc.MEMORY_FILE = os.path.join(self.tmp, "memory.json")

    def test_write_structured(self):
        r = dc.write_memory(
            "张三负责数据备份系统", type="项目", entities="张三,数据备份系统",
            relations="张三-负责-数据备份系统",
        )
        self.assertIn("已写入", r)
        data = json.load(open(dc.MEMORY_FILE, encoding="utf-8"))
        f = data["facts"][0]
        self.assertEqual(f["type"], "项目")
        self.assertEqual(f["entities"], ["张三", "数据备份系统"])
        self.assertEqual(f["relations"], [{"rel": "负责", "to": "数据备份系统"}])

    def test_semantic_retrieval(self):
        dc.write_memory("张三负责公司的数据备份系统开发", type="项目")
        dc.write_memory("李四喜欢喝咖啡", type="偏好")
        out = dc.read_memory(keyword="谁在搞备份")
        self.assertIn("张三", out)
        self.assertNotIn("李四", out)

    def test_exact_match_priority(self):
        dc.write_memory("张三负责备份系统")
        dc.write_memory("李四喜欢咖啡")
        out = dc.read_memory(keyword="咖啡")
        self.assertIn("李四", out)
        self.assertNotIn("张三", out)

    def test_graph_query(self):
        dc.write_memory("张三负责数据备份系统", entities="张三,数据备份系统", relations="张三-负责-数据备份系统")
        dc.write_memory("李四参与测试工作", entities="李四", relations="李四-参与-测试工作")
        g = dc.query_memory_graph(entity="张三")
        self.assertIn("张三", g)
        self.assertNotIn("李四", g)
        g2 = dc.query_memory_graph(relation="参与")
        self.assertIn("李四", g2)

    def test_type_filter(self):
        dc.write_memory("喜欢咖啡", type="偏好")
        dc.write_memory("项目 A 明天上线", type="项目")
        out = dc.read_memory(type="偏好")
        self.assertIn("咖啡", out)
        self.assertNotIn("上线", out)


class TestDataTools(unittest.TestCase):
    def setUp(self):
        # tempfile 默认位于 AppData\Local\Temp（在权限阻止列表内），测试环境移除该阻止项
        self.tmp = tempfile.mkdtemp(prefix="dsa_tools_")
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws, exist_ok=True)
        permissions.init(os.path.join(self.tmp, "perm.json"), ws)
        data = permissions.get_data()
        data["filesystem"]["blocked_dirs"] = [
            d for d in data["filesystem"]["blocked_dirs"] if "AppData" not in d
        ]
        # 写工具测试在"完全智能"语义下执行（写开关默认关，需显式放行）
        permissions.set_full_auto(True)
        dc.MEMORY_FILE = os.path.join(self.tmp, "memory.json")

    def tearDown(self):
        import shutil

        permissions.set_full_auto(False)  # 还原全局态，防跨用例漂移
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_csv_roundtrip(self):
        p = os.path.join(self.tmp, "ws", "d.csv")
        self.assertIn("已写入", dc.write_csv(p, [[1, "甲"], [2, "乙"]], headers="id,名称"))
        out = dc.read_csv(p)
        self.assertIn("甲", out)
        self.assertIn("乙", out)

    def test_csv_object_rows(self):
        p = os.path.join(self.tmp, "ws", "o.csv")
        self.assertIn("已写入", dc.write_csv(p, [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]))
        out = dc.read_csv(p)
        self.assertIn("x", out)

    def test_excel_roundtrip(self):
        p = os.path.join(self.tmp, "ws", "d.xlsx")
        self.assertIn("已写入", dc.write_excel(p, [["id", "val"], [1, 10], [2, 20]]))
        out = dc.read_excel(p)
        self.assertIn("10", out)

    def test_chart_png(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib 未安装")
        p = os.path.join(self.tmp, "ws", "c.png")
        self.assertIn("已生成", dc.chart_data([[1, 10], [2, 25]], p, kind="bar"))
        self.assertTrue(os.path.exists(p))

    def test_mysql_missing_config(self):
        r = dc.database_query_mysql(sql="SELECT 1")
        self.assertIn("错误", r)  # 未配置时明确报错而非崩溃


class TestSelfVerify(unittest.TestCase):
    def test_verify_output_similar(self):
        r = dc.verify_output("数据备份需保留三十天", "备份数据保留30天")
        self.assertIn("评估", r)

    def test_verify_output_missing(self):
        r = dc.verify_output("需要用户登录与注册功能", "功能已完成")
        self.assertIn("未通过", r)

    def test_verify_output_empty(self):
        r = dc.verify_output("预期内容", "")
        self.assertIn("0%", r)


class TestMemoryInjection(unittest.TestCase):
    def test_format_fact_with_graph(self):
        line = m.AssistantApp._format_memory_fact(
            {"key": "项目", "value": "张三负责备份", "type": "项目",
             "entities": ["张三", "备份"], "relations": [{"rel": "负责", "to": "备份"}]}
        )
        self.assertIn("实体:张三", line)
        self.assertIn("关系:负责→备份", line)

    def test_format_fact_plain(self):
        line = m.AssistantApp._format_memory_fact({"key": "偏好", "value": "咖啡"})
        self.assertEqual(line, "- 偏好: 咖啡")


class TestWebhookPayload(unittest.TestCase):
    def test_serverchan_form_fields(self):
        payload = dc._webhook_payload("serverchan", "标题", "正文")
        self.assertEqual(payload, {"title": "标题", "desp": "正文"})

    def test_dingtalk_payload(self):
        payload = dc._webhook_payload("dingtalk", "标题", "正文")
        self.assertEqual(payload["msgtype"], "text")
        self.assertIn("标题", payload["text"]["content"])

    def test_no_config_clear_error(self):
        old = dc.WEBHOOK_CONFIG_FILE
        dc.WEBHOOK_CONFIG_FILE = os.path.join(tempfile.mkdtemp(), "none.json")
        try:
            self.assertIn("未配置", dc.send_webhook_notify("测试"))
        finally:
            dc.WEBHOOK_CONFIG_FILE = old


class TestV4GaAdaptation(unittest.TestCase):
    """V4 正式版适配（DeepSeek-V4-Pro-0813 / Flash-0731）：
    新峰谷定价 / 思考档位 low·high·max / 模型版本 / 输出上限。"""

    def test_peak_rates_match_official(self):
        p = stats.pricing()
        self.assertEqual(p["deepseek-v4-flash"], {"prompt": 3.0, "completion": 9.0, "cache_hit": 0.10})
        self.assertEqual(p["deepseek-v4-pro"], {"prompt": 9.0, "completion": 27.0, "cache_hit": 0.30})

    def test_estimate_cost_peak_hour(self):
        usage = {"prompt": 1_000_000, "completion": 0, "cache_hit": 0, "cache_miss": 1_000_000}
        with mock.patch("stats.is_peak_hour", return_value=True):
            self.assertAlmostEqual(stats.estimate_cost(usage, "deepseek-v4-flash"), 3.0)
            self.assertAlmostEqual(stats.estimate_cost(usage, "deepseek-v4-pro"), 9.0)

    def test_estimate_cost_offpeak_half(self):
        """官方峰谷定价：空闲时段价格为高峰时段的一半。"""
        usage = {"prompt": 1_000_000, "completion": 0, "cache_hit": 0, "cache_miss": 1_000_000}
        with mock.patch("stats.is_peak_hour", return_value=False):
            self.assertAlmostEqual(stats.estimate_cost(usage, "deepseek-v4-flash"), 1.5)
            self.assertAlmostEqual(stats.estimate_cost(usage, "deepseek-v4-pro"), 4.5)

    def test_cache_hit_pricing(self):
        usage = {"prompt": 1_000_000, "completion": 0, "cache_hit": 1_000_000, "cache_miss": 0}
        with mock.patch("stats.is_peak_hour", return_value=True):
            self.assertAlmostEqual(stats.estimate_cost(usage, "deepseek-v4-pro"), 0.30)

    def test_shared_peak_hours(self):
        from shared import is_peak_hour as sp

        self.assertTrue(sp(datetime(2026, 8, 13, 10, 0)))
        self.assertFalse(sp(datetime(2026, 8, 13, 13, 0)))
        self.assertTrue(sp(datetime(2026, 8, 13, 15, 0)))
        self.assertFalse(sp(datetime(2026, 8, 13, 20, 0)))

    def test_thinking_modes_ga(self):
        """思考档位：官方完整映射表（none/low/medium/high/xhigh/max）+ auto 智能路由。"""
        for k in ("none", "low", "medium", "high", "xhigh", "max", "auto"):
            self.assertIn(k, dc.THINKING_MODES)

    def test_thinking_effort_ga_levels(self):
        """官方 effort 映射表：low→low · medium→high · high→high · xhigh→high · max→max。"""
        self.assertEqual(dc.EFFORT_BY_THINKING["low"], "low")
        self.assertEqual(dc.EFFORT_BY_THINKING["medium"], "high")
        self.assertEqual(dc.EFFORT_BY_THINKING["high"], "high")
        self.assertEqual(dc.EFFORT_BY_THINKING["xhigh"], "high")
        self.assertEqual(dc.EFFORT_BY_THINKING["max"], "max")

    def test_model_versions_and_output_cap(self):
        self.assertEqual(dc.MODELS["deepseek-v4-flash"]["version"], "DeepSeek-V4-Flash-0731")
        self.assertEqual(dc.MODELS["deepseek-v4-pro"]["version"], "DeepSeek-V4-Pro-0813")
        self.assertEqual(dc.MODELS["deepseek-v4-pro"]["max_output_tokens"], 384 * 1024)
        self.assertEqual(dc.MODELS["deepseek-v4-flash"]["max_context_tokens"], 1_000_000)


class TestOffPeakDefer(unittest.TestCase):
    """高峰错峰：定时任务在高峰时段命中时顺延到最近空闲时段。"""

    def test_defer_until_peak_hours(self):
        from datetime import datetime as _dt

        # 上午高峰 10:00 → 顺延 12:00
        ts = m.AssistantApp._defer_until(_dt(2026, 8, 13, 10, 0))
        self.assertEqual(_dt.fromtimestamp(ts).strftime("%H:%M"), "12:00")
        # 下午高峰 15:00 → 顺延 18:00
        ts = m.AssistantApp._defer_until(_dt(2026, 8, 13, 15, 30))
        self.assertEqual(_dt.fromtimestamp(ts).strftime("%H:%M"), "18:00")
        # 高峰末期 11:59 → 顺延 12:00
        ts = m.AssistantApp._defer_until(_dt(2026, 8, 13, 11, 59))
        self.assertEqual(_dt.fromtimestamp(ts).strftime("%H:%M"), "12:00")

    def test_defer_until_after_peak(self):
        """高峰已过但仍在触发窗口（极端情况）→ 次日 0:00，且日期正确。"""
        from datetime import datetime as _dt

        ts = m.AssistantApp._defer_until(_dt(2026, 8, 13, 13, 30))
        d = _dt.fromtimestamp(ts)
        self.assertEqual(d.strftime("%H:%M"), "00:00")
        self.assertGreater(d.date().isoformat(), "2026-08-13")  # 次日

    def test_schedule_off_peak_flag(self):
        r = dc.schedule_task(expr_type="cron", expr="30 9 * * 1", content="生成周报",
                             action="message", off_peak=True)
        self.assertIn("已创建", r)
        data = json.load(open(dc.SCHEDULES_FILE, encoding="utf-8"))
        self.assertTrue(data[0].get("off_peak"))
        # 默认不开启
        dc.schedule_task(expr_type="cron", expr="0 9 * * *", content="x")
        data = json.load(open(dc.SCHEDULES_FILE, encoding="utf-8"))
        self.assertFalse(data[1].get("off_peak"))


class TestStrictTools(unittest.TestCase):
    """strict 工具模式（Beta）：schema 规范化 + chat 请求应用。"""

    def test_strictify_schema_nested(self):
        schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    },
                },
            },
        }
        st = dc._strictify_schema(schema)
        # 修复后：不自动把可选属性全部设为 required，也不强制封闭自由对象
        self.assertNotIn("required", st)
        self.assertNotIn("additionalProperties", st)
        items = st["properties"]["files"]["items"]
        self.assertNotIn("required", items)
        self.assertNotIn("additionalProperties", items)

    def test_strictify_tools_all_strict(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            },
        }]
        out = dc._strictify_tools(tools)
        self.assertTrue(out[0]["function"]["strict"])
        self.assertEqual(out[0]["function"]["parameters"]["required"], ["location"])
        # 修复后：不额外强制 additionalProperties=false（原 schema 未声明则不添加）
        self.assertNotIn("additionalProperties", out[0]["function"]["parameters"])
        # 原 schema 不被修改（浅拷贝）
        self.assertNotIn("strict", tools[0]["function"])

    def test_chat_strict_tools_applied(self):
        client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")
        client.client.chat.completions.create = mock.MagicMock()
        captured = {}
        client.client.chat.completions.create.side_effect = (
            lambda **kw: (captured.update(kw) or client.client.chat.completions.create.return_value)
        )
        client.client.chat.completions.create.return_value = type("S", (), {
            "__iter__": lambda self: iter([]),
            "usage": type("U", (), {"prompt_tokens": 1, "completion_tokens": 1,
                                    "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1})(),
            "close": lambda self: None,
        })()
        client.chat([{"role": "user", "content": "hi"}], tools_enabled=True, strict_tools=True)
        tools = captured["tools"]
        self.assertTrue(all(t["function"].get("strict") for t in tools))
        for t in tools:
            params = t["function"]["parameters"]
            # 修复后：不强制所有属性必填，也不强制封闭自由对象
            required = params.get("required", [])
            self.assertTrue(set(required) <= set(params.get("properties", {}).keys()))
            # 自由对象（如 call_api.params/json_body）不应被锁成空对象
            if "properties" not in params:
                self.assertNotEqual(params.get("additionalProperties"), False)


def _make_png_bytes():
    """生成 1x1 最小合法 PNG（魔数正确）。"""
    import struct
    import zlib

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr = b"\x00\x00\x00\rIHDR" + ihdr_data + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
    raw = b"\x00\xff\x00\x00"
    idat = b"\x00\x00\x00\x0dIDAT" + zlib.compress(raw) + struct.pack(">I", zlib.crc32(b"IDAT" + zlib.compress(raw)) & 0xFFFFFFFF)
    iend = b"\x00\x00\x00\x00IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return sig + ihdr + idat + iend


class TestVisionModel(unittest.TestCase):
    """DeepSeek-V4-Flash-Vision-Exp 图像理解适配。"""

    def test_vision_model_registered(self):
        self.assertIn("deepseek-v4-flash-vision-exp", dc.MODELS)
        info = dc.MODELS["deepseek-v4-flash-vision-exp"]
        self.assertEqual(info["version"], "DeepSeek-V4-Flash-Vision-Exp")
        self.assertTrue(info["vision"])
        self.assertEqual(info["max_context_tokens"], 1_000_000)
        self.assertEqual(info["max_output_tokens"], 384 * 1024)

    def test_is_vision_model(self):
        self.assertTrue(dc.is_vision_model("deepseek-v4-flash-vision-exp"))
        self.assertTrue(dc.is_vision_model(dc.VISION_MODEL))
        self.assertFalse(dc.is_vision_model("deepseek-v4-flash"))
        self.assertFalse(dc.is_vision_model("deepseek-v4-pro"))
        self.assertFalse(dc.is_vision_model(""))

    def test_detect_image_mime(self):
        png = _make_png_bytes()
        self.assertEqual(dc._detect_image_mime(png[:16]), "image/png")
        self.assertEqual(dc._detect_image_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 12), "image/jpeg")
        self.assertEqual(dc._detect_image_mime(b"GIF89a" + b"\x00" * 10), "image/gif")
        self.assertEqual(dc._detect_image_mime(b"RIFF\x00\x00\x00\x00WEBP"), "image/webp")

    def test_embed_local_image_not_pollute_original(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.png")
            with open(p, "wb") as f:
                f.write(_make_png_bytes())
            msgs = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "这是什么", "images": [p], "time": "10:00"},
            ]
            out = dc.embed_message_images(msgs, "deepseek-v4-flash-vision-exp")
            self.assertIsInstance(out[1]["content"], list)
            types = [b["type"] for b in out[1]["content"]]
            self.assertEqual(types, ["text", "image_url"])
            url = out[1]["content"][1]["image_url"]["url"]
            self.assertTrue(url.startswith("data:image/png;base64,"))
            # 原消息对象不被污染（UI/存档仍为文本 + 路径）
            self.assertEqual(msgs[1]["content"], "这是什么")
            self.assertEqual(msgs[1]["images"], [p])

    def test_embed_non_vision_model_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.png")
            with open(p, "wb") as f:
                f.write(_make_png_bytes())
            msgs = [{"role": "user", "content": "hi", "images": [p]}]
            with self.assertRaises(ValueError):
                dc.embed_message_images(msgs, "deepseek-v4-flash")

    def test_embed_missing_file_raises(self):
        msgs = [{"role": "user", "content": "hi", "images": [os.path.join("no", "such.png")]}]
        with self.assertRaises(ValueError):
            dc.embed_message_images(msgs, "deepseek-v4-flash-vision-exp")

    def test_restore_text_content(self):
        msg = {"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        r = dc._restore_text_content(msg)
        self.assertEqual(r["content"], "a\nb")
        # 无 content 列表的消息原样返回
        plain = {"role": "user", "content": "x"}
        self.assertIs(dc._restore_text_content(plain), plain)

    def test_chat_sends_image_blocks_and_restores_history(self):
        client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash-vision-exp")
        client.client.chat.completions.create = mock.MagicMock()
        captured = {}
        client.client.chat.completions.create.side_effect = (
            lambda **kw: (captured.update(kw) or client.client.chat.completions.create.return_value)
        )
        client.client.chat.completions.create.return_value = type("S", (), {
            "__iter__": lambda self: iter([]),
            "usage": type("U", (), {"prompt_tokens": 1, "completion_tokens": 1,
                                    "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1})(),
            "close": lambda self: None,
        })()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.png")
            with open(p, "wb") as f:
                f.write(_make_png_bytes())
            msgs = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "看下这张图", "images": [p]},
            ]
            client.chat(msgs, tools_enabled=False)
        # 请求体：user 消息 content 为 text + image_url 块
        sent = captured["messages"]
        user_block = next(m for m in sent if m["role"] == "user")
        self.assertIsInstance(user_block["content"], list)
        self.assertTrue(any(
            b.get("type") == "image_url" and str(b.get("image_url", {}).get("url", "")).startswith("data:image/png;base64,")
            for b in user_block["content"]
        ))
        # 调用方历史还原为纯文本 + images 路径（不残留 base64）
        self.assertEqual(msgs[1]["content"], "看下这张图")
        self.assertIsInstance(msgs[1]["content"], str)
        self.assertEqual(msgs[1]["images"], [p])

    def test_chat_non_vision_with_images_raises(self):
        client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.png")
            with open(p, "wb") as f:
                f.write(_make_png_bytes())
            msgs = [{"role": "user", "content": "hi", "images": [p]}]
            with self.assertRaises(ValueError):
                client.chat(msgs, tools_enabled=False)


class TestVisionTools(unittest.TestCase):
    """视觉 Agent 工具：screen_see / chart_read / screenshot_to_html / debug_screenshot / scan_read / image_batch。"""

    VISION_TOOLS = {
        "screen_see", "chart_read", "screenshot_to_html",
        "debug_screenshot", "scan_read", "image_batch",
    }

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_vision_")
        permissions.init(os.path.join(self.tmp, "perm.json"), self.tmp)
        self.png = os.path.join(self.tmp, "x.png")
        with open(self.png, "wb") as f:
            f.write(_make_png_bytes())

    def test_tools_registered(self):
        for n in self.VISION_TOOLS:
            self.assertIn(n, dc.TOOL_CALL_MAP, n)
            self.assertTrue(any(t["function"]["name"] == n for t in dc.TOOLS), n)
            self.assertIn(n, dc._TOOL_ACTION_PHRASES, n)
            self.assertIn(n, dc._LONG_TOOL_NAMES, n)
        # 视觉自审白名单覆盖图片产出工具
        self.assertIn("image_generate", dc._IMAGE_PRODUCING_TOOLS)
        self.assertIn("chart_data", dc._IMAGE_PRODUCING_TOOLS)
        self.assertIn("web_screenshot", dc._IMAGE_PRODUCING_TOOLS)
        # 默认关闭（控成本）
        self.assertFalse(dc.VISION_SELF_REVIEW)

    def test_chart_read_delegates_to_vision(self):
        with mock.patch.object(dc, "image_understand", return_value="折线图：营收 100→150") as mu:
            r = dc.chart_read(self.png)
        self.assertIn("折线图", r)
        self.assertTrue(mu.called)
        self.assertEqual(mu.call_args[0][0], self.png)

    def test_chart_read_requires_path(self):
        self.assertIn("path 必填", dc.chart_read(""))

    def test_debug_screenshot(self):
        with mock.patch.object(dc, "image_understand", return_value="错误：KeyError 'x'") as mu:
            r = dc.debug_screenshot(self.png)
        self.assertIn("KeyError", r)
        self.assertTrue(mu.called)

    def test_scan_read(self):
        with mock.patch.object(dc, "image_understand", return_value="扫描文档内容…"):
            r = dc.scan_read(self.png)
        self.assertIn("扫描文档内容", r)

    def test_screenshot_to_html_saves_file(self):
        html = "<html><body>Hello</body></html>"
        with mock.patch.object(dc, "image_understand", return_value="```html\n" + html + "\n```"):
            r = dc.screenshot_to_html(self.png, out_path=os.path.join(self.tmp, "out.html"))
        self.assertIn("已根据截图生成 HTML", r)
        saved = os.path.join(self.tmp, "out.html")
        self.assertTrue(os.path.exists(saved))
        with open(saved, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), html)

    def test_screenshot_to_html_returns_code_only(self):
        with mock.patch.object(dc, "image_understand", return_value="<html>..</html>"):
            r = dc.screenshot_to_html(self.png)
        self.assertIn("<html>", r)

    def test_image_batch_summary(self):
        with open(os.path.join(self.tmp, "y.png"), "wb") as f:
            f.write(_make_png_bytes())
        with mock.patch.object(dc, "image_understand", return_value="内容A"):
            r = dc.image_batch(self.tmp, question="描述")
        self.assertIn("x.png", r)
        self.assertIn("y.png", r)
        self.assertIn("共分析 2 张图片", r)

    def test_image_batch_no_images(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.assertIn("没有匹配", dc.image_batch(empty))

    def test_image_batch_bad_dir(self):
        self.assertIn("目录不存在", dc.image_batch(os.path.join(self.tmp, "nope")))

    def test_extract_image_path(self):
        self.assertEqual(dc._extract_image_path(f"已生成图片保存至 {self.png}（1 KB）"), self.png)
        self.assertIsNone(dc._extract_image_path("没有图片路径"))

    def test_extract_image_path_with_space(self):
        sp = os.path.join(self.tmp, "my folder")
        os.makedirs(sp, exist_ok=True)
        p = os.path.join(sp, "x.png")
        with open(p, "wb") as f:
            f.write(_make_png_bytes())
        self.assertEqual(dc._extract_image_path(f"已生成图片保存至 {p}（1 KB）"), p)

    def test_image_batch_respects_max(self):
        with open(os.path.join(self.tmp, "y.png"), "wb") as f:
            f.write(_make_png_bytes())
        with mock.patch.object(dc, "image_understand", return_value="内容"):
            r = dc.image_batch(self.tmp, max=1)
        self.assertIn("共分析 1 张图片", r)

    def test_image_batch_blocks_traversal(self):
        # pattern 含 .. 越界：越界文件必须被丢弃（不允许读取 base 之外）
        with mock.patch.object(dc, "image_understand", return_value="内容"):
            r = dc.image_batch(self.tmp, pattern="..\\..\\*.png")
        self.assertIn("没有匹配", r)

    def test_screenshot_to_html_does_not_write_on_error(self):
        with mock.patch.object(dc, "image_understand", return_value="错误：图片不存在：x"):
            r = dc.screenshot_to_html(self.png, out_path=os.path.join(self.tmp, "bad.html"))
        self.assertIn("错误", r)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "bad.html")))


class TestToolCostOptimization(unittest.TestCase):
    """工具定义成本优化：按组激活 / 关键词预激活 / 内容指纹缓存 / 压缩保结构。"""

    # ---- 流式 mock 辅助（与 test_agent_tools 同构）----
    @staticmethod
    def _stream(content="", finish_reason=None, tool_calls=None):
        def _delta():
            return type("D", (), {
                "reasoning_content": None,
                "content": content or None,
                "tool_calls": tool_calls,
            })()

        class S(list):
            usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1,
                                    "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1})()

        c = type("C", (), {
            "delta": _delta(),
            "finish_reason": finish_reason,
            "choices": [type("CC", (), {"delta": _delta(), "finish_reason": finish_reason})()],
        })()
        return S([c])

    @staticmethod
    def _tool(tc_id, name, args="{}"):
        return type("T", (), {
            "index": 0,
            "id": tc_id,
            "function": type("F", (), {"name": name, "arguments": args})(),
        })()

    def test_group_name_map_both_forms(self):
        """组名映射：emoji 原文与裸组名都可用。"""
        emoji = dc._TOOL_GROUP_NAME_MAP["📊 数据与文档"]
        bare = dc._TOOL_GROUP_NAME_MAP["数据与文档"]
        self.assertEqual(emoji, bare)
        self.assertGreater(len(bare), 5)
        self.assertIn("read_excel", bare)
        self.assertIn("database_query", bare)

    def test_expand_activation_group_and_single(self):
        """activate_tools 支持组名一次激活整组 + 单个工具名。"""
        avail = set(dc.TOOL_CALL_MAP)
        act = set()
        dc._expand_activation(["数据与文档", "get_weather", "不存在的工具"], avail, act)
        self.assertIn("read_excel", act)
        self.assertIn("database_query", act)
        self.assertIn("get_weather", act)
        self.assertEqual(len(act), len(dc._TOOL_GROUP_NAME_MAP["数据与文档"]) + 1)
        self.assertNotIn("image_generate", act)  # 未混入其他组

    def test_keyword_preactivation(self):
        act = set()
        dc._preactivate_from_messages(
            [{"role": "system", "content": "x"}, {"role": "user", "content": "帮我搜索最新AI新闻"}], act
        )
        self.assertIn("search_web", act)
        self.assertIn("search_realtime", act)
        act2 = set()
        dc._preactivate_from_messages([{"role": "user", "content": "今天天气怎么样"}], act2)
        self.assertIn("get_weather", act2)
        # 无 user 消息 → 不激活
        act3 = set()
        dc._preactivate_from_messages([{"role": "system", "content": "x"}], act3)
        self.assertEqual(act3, set())

    def test_keyword_preactivation_with_image_blocks(self):
        """图片内联内容块（list content）也能被预激活扫描。"""
        act = set()
        dc._preactivate_from_messages(
            [{"role": "user", "content": [{"type": "text", "text": "看看这张图表"}]}], act
        )
        self.assertIn("chart_read", act)

    def test_build_tool_index_fingerprint_cache(self):
        """缓存键为内容指纹：深拷贝列表（id 不同）仍命中；内容变化才重建。"""
        i1 = dc.build_tool_index()
        i2 = dc.build_tool_index(json.loads(json.dumps(dc.TOOLS)))  # 深拷贝，id 不同
        i3 = dc.build_tool_index()
        self.assertEqual(i1, i2)
        self.assertEqual(i1, i3)
        # 内容变化 → 重建（不命中旧缓存）
        subset = json.loads(json.dumps(
            [next(t for t in dc.TOOLS if t["function"]["name"] == "get_date")]
        ))
        self.assertNotEqual(dc.build_tool_index(subset), i1)

    def test_compact_preserves_structure(self):
        """压缩只动 description，name/required/properties/type 全部保留。"""
        tool = next(t for t in dc.TOOLS if t["function"]["name"] == "database_query")
        c = dc.compact_tool_schema(tool)
        self.assertEqual(c["function"]["name"], "database_query")
        params = c["function"]["parameters"]
        self.assertEqual(
            set(params["properties"].keys()),
            set(tool["function"]["parameters"]["properties"].keys()),
        )
        self.assertEqual(params.get("required"), tool["function"]["parameters"].get("required"))
        for name in params["properties"]:
            self.assertEqual(
                params["properties"][name].get("type"),
                tool["function"]["parameters"]["properties"][name].get("type"),
            )
        # 参数描述收紧到 ≤40 + 省略号
        for p in params["properties"].values():
            if "description" in p:
                self.assertLessEqual(len(p["description"]), 41)
        # 原 schema 不被修改
        orig = next(t for t in dc.TOOLS if t["function"]["name"] == "database_query")
        self.assertEqual(orig["function"]["description"], tool["function"]["description"])

    def test_chat_first_round_only_activate_and_preset(self):
        """首轮请求只注入 activate_tools + 预激活工具，绝非 109 个完整 schema。"""
        client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")
        client.client.chat.completions.create = mock.MagicMock()
        calls = []
        s_done = self._stream(content="完成", finish_reason="stop")
        client.client.chat.completions.create.side_effect = (
            lambda **kw: (calls.append(kw) or s_done)
        )
        msgs = [{"role": "user", "content": "帮我搜索最新AI新闻"}]
        client.chat(msgs, tools_enabled=True, smart_tools=True)
        names = [t["function"]["name"] for t in (calls[0].get("tools") or [])]
        self.assertIn("activate_tools", names)
        self.assertIn("search_web", names)     # 关键词预激活
        self.assertIn("search_realtime", names)
        self.assertLess(len(names), 40)        # 绝非 109 全量

    def test_chat_group_activation_flow(self):
        """模型按组点菜：下一轮注入整组工具，且不混入其他组。"""
        client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")
        client.client.chat.completions.create = mock.MagicMock()
        calls = []
        streams = [
            self._stream(tool_calls=[self._tool("t1", "activate_tools", '{"tools": ["数据与文档"]}')]),
            self._stream(content="完成", finish_reason="stop"),
        ]

        def fake_create(**kw):
            calls.append(kw)
            return streams.pop(0)

        client.client.chat.completions.create.side_effect = fake_create
        msgs = [{"role": "user", "content": "分析表格"}]
        client.chat(msgs, tools_enabled=True, smart_tools=True)
        self.assertGreaterEqual(len(calls), 2)
        names = {t["function"]["name"] for t in (calls[1].get("tools") or [])}
        self.assertIn("read_excel", names)
        self.assertIn("database_query", names)
        self.assertNotIn("image_generate", names)   # 其他组工具不注入
        self.assertNotIn("activate_tools", names)   # 激活后移除点菜工具

    def test_non_smart_keeps_full_tools(self):
        """非点菜模式（smart_tools=False）保持全量工具注入，不受影响。"""
        client = dc.DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash")
        client.client.chat.completions.create = mock.MagicMock()
        captured = {}
        client.client.chat.completions.create.side_effect = (
            lambda **kw: (captured.update(kw) or client.client.chat.completions.create.return_value)
        )
        client.client.chat.completions.create.return_value = self._stream(content="好", finish_reason="stop")
        client.chat([{"role": "user", "content": "hi"}], tools_enabled=True, smart_tools=False)
        names = {t["function"]["name"] for t in (captured.get("tools") or [])}
        self.assertIn("write_file", names)
        self.assertNotIn("activate_tools", names)


class TestToolsDefinitionEstimate(unittest.TestCase):
    """状态栏「工具定义≈N」真实性：按工作模式估算，而非 109 全量最坏值（≈16086）。"""

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk

            _probe = tk.Tk()
            _probe.destroy()
        except Exception:
            raise unittest.SkipTest("tkinter 不可用")
        cls.tmpdir = tempfile.mkdtemp(prefix="dsa_tdef_")
        m.CONFIG_PATH = os.path.join(cls.tmpdir, "config.json")
        m.HISTORY_DIR = cls.tmpdir
        m.SNAPSHOT_PATH = os.path.join(cls.tmpdir, "snap.json")
        m.SESSIONS_DIR = os.path.join(cls.tmpdir, "sessions")
        m.STATS_PATH = os.path.join(cls.tmpdir, "stats.json")
        m.PROMPTS_PATH = os.path.join(cls.tmpdir, "prompts.json")
        m.USER_TOOLS_PATH = os.path.join(cls.tmpdir, "ut.json")
        m.ARCHIVES_DIR = os.path.join(cls.tmpdir, "archives")
        m.CLEAN_EXIT_FLAG = os.path.join(cls.tmpdir, ".clean_exit")
        os.makedirs(m.SESSIONS_DIR, exist_ok=True)
        os.makedirs(m.ARCHIVES_DIR, exist_ok=True)
        with open(m.CLEAN_EXIT_FLAG, "w", encoding="utf-8") as f:
            f.write("ok")
        with open(m.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"welcomed": True, "restore_session": False}, f)
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = m.AssistantApp(cls.root)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.on_close()
        except Exception:
            pass
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self.app.messages = [{"role": "system", "content": self.app.cfg["system_prompt"]}]
        self.app._tools_def_cache = None

    def test_smart_mode_reflects_real_injection(self):
        """完全智能：估算 = 能力地图 + activate_tools + 预激活工具压缩版（远小于 16086）。"""
        self.app.cfg["full_auto"] = True
        self.app.cfg["pure_chat"] = False
        self.app.messages.append({"role": "user", "content": "帮我搜索最新AI新闻"})
        val = self.app._tools_definition_tokens()
        self.assertGreater(val, 0)
        full_raw = tokens.estimate_text_tokens(json.dumps(dc.TOOLS, ensure_ascii=False))
        self.assertLess(val, full_raw)  # 绝非 109 全量原始 schema 最坏值
        self.assertLess(val, 8000)
        index_only = tokens.estimate_text_tokens(dc.build_tool_index())
        self.assertGreater(val, index_only)  # 命中关键词 → 计入预激活工具定义

    def test_pure_chat_zero(self):
        self.app.cfg["full_auto"] = False
        self.app.cfg["pure_chat"] = True
        self.app.messages.append({"role": "user", "content": "你好"})
        self.assertEqual(self.app._tools_definition_tokens(), 0)

    def test_cache_follows_input_change(self):
        """缓存键含输入文本：预激活随输入变化，估算不陈旧。"""
        self.app.cfg["full_auto"] = True
        self.app.cfg["pure_chat"] = False
        self.app.messages.append({"role": "user", "content": "帮我搜索"})
        v1 = self.app._tools_definition_tokens()
        self.app.messages[-1]["content"] = "今天天气怎么样"
        v2 = self.app._tools_definition_tokens()
        self.assertNotEqual(v1, v2)


if __name__ == "__main__":
    unittest.main()
