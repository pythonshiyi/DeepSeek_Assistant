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
        self.assertEqual(sorted(st["required"]), ["files", "path"])
        self.assertFalse(st["additionalProperties"])
        items = st["properties"]["files"]["items"]
        self.assertEqual(sorted(items["required"]), ["content", "path"])
        self.assertFalse(items["additionalProperties"])

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
        self.assertFalse(out[0]["function"]["parameters"]["additionalProperties"])
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
            self.assertFalse(params.get("additionalProperties", False))
            self.assertEqual(sorted(params.get("required", [])), sorted(params["properties"].keys()))


if __name__ == "__main__":
    unittest.main()
