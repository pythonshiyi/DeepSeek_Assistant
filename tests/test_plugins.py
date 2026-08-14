# -*- coding: utf-8 -*-
"""插件体系（.wtplugin）测试：格式校验 / 安装合并 / 卸载精确移除 / 启停 / 依赖自检。

插件 = 工具/技能/流程/场景的组合包，安装合并进 user_tools.json / prompts.json /
workflows.json（条目带 _source: plugin:<slug> 标记），卸载仅移除本插件条目。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deepseek_client as dc
import main as m
import plugins as plugins_mod


def _make_plugin(name="测试插件", with_tools=True, with_skills=True, with_workflows=True,
                 with_scenario=False, requires=None):
    plugin = {
        "format": "wtplugin",
        "version": 1,
        "meta": {
            "name": name,
            "description": "用于测试的插件",
            "author": "tester",
            "version": "1.0.0",
        },
        "requires": requires or [],
        "contents": {},
    }
    if with_tools:
        plugin["contents"]["tools"] = [{
            "type": "function",
            "function": {
                "name": "xhs_writer",
                "description": "小红书文案",
                "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]},
                "endpoint": "https://example.com/xhs",
            },
        }]
    if with_skills:
        plugin["contents"]["skills"] = [{"name": "爆款标题", "text": "请为 {{TEXT}} 生成 5 个爆款标题"}]
    if with_workflows:
        plugin["contents"]["workflows"] = {"每日巡检": {"steps": [{"text": "检查磁盘"}, {"text": "汇报"}]}}
    if with_scenario:
        plugin["contents"]["scenario"] = {"name": "小红书场景", "thinking": "high",
                                           "enabled_tools": ["write_file"]}
    return plugin


class TestPluginFormat(unittest.TestCase):
    def test_validate_ok(self):
        ok, err = plugins_mod.validate_plugin(_make_plugin())
        self.assertTrue(ok, err)

    def test_validate_missing_name(self):
        p = _make_plugin()
        p["meta"]["name"] = "  "
        ok, err = plugins_mod.validate_plugin(p)
        self.assertFalse(ok)
        self.assertIn("名称", err)

    def test_validate_wrong_format(self):
        p = _make_plugin()
        p["format"] = "other"
        ok, err = plugins_mod.validate_plugin(p)
        self.assertFalse(ok)

    def test_validate_empty_contents(self):
        p = _make_plugin(with_tools=False, with_skills=False, with_workflows=False)
        ok, err = plugins_mod.validate_plugin(p)
        self.assertFalse(ok)
        self.assertIn("任何能力", err)

    def test_validate_tool_missing_endpoint(self):
        p = _make_plugin()
        del p["contents"]["tools"][0]["function"]["endpoint"]
        ok, err = plugins_mod.validate_plugin(p)
        self.assertFalse(ok)
        self.assertIn("endpoint", err)

    def test_slug_safe(self):
        self.assertNotIn("!", plugins_mod._slug("小红书 文案!@#$%^"))
        self.assertEqual(plugins_mod._slug("a" * 100), "a" * 40)
        self.assertEqual(plugins_mod._slug(""), "plugin")
        self.assertEqual(plugins_mod._slug("普通-中文_插件"), "普通-中文_插件")


class TestPluginLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_plugin_")
        self.paths = {
            "plugins_dir": os.path.join(self.tmp, "plugins"),
            "user_tools": os.path.join(self.tmp, "user_tools.json"),
            "prompts": os.path.join(self.tmp, "prompts.json"),
            "workflows": os.path.join(self.tmp, "workflows.json"),
        }
        # 预置手动添加的同名工具（验证卸载不误删用户条目）
        with open(self.paths["user_tools"], "w", encoding="utf-8") as f:
            json.dump([{
                "type": "function",
                "function": {
                    "name": "manual_tool",
                    "description": "用户手动添加",
                    "parameters": {"type": "object", "properties": {}},
                    "endpoint": "https://example.com/manual",
                },
            }], f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_merges_and_tags(self):
        plugin = _make_plugin()
        res = plugins_mod.apply_plugin(plugin, self.paths)
        self.assertTrue(res["ok"], res)
        self.assertTrue(os.path.exists(os.path.join(self.paths["plugins_dir"], "测试插件.wtplugin")))

        tools = json.load(open(self.paths["user_tools"], encoding="utf-8"))
        self.assertEqual(len(tools), 2)  # 手动工具 + 插件工具
        xhs = next(t for t in tools if t["function"]["name"] == "xhs_writer")
        self.assertEqual(xhs["_source"], "plugin:测试插件")

        prompts = json.load(open(self.paths["prompts"], encoding="utf-8"))
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["_source"], "plugin:测试插件")

        wf = json.load(open(self.paths["workflows"], encoding="utf-8"))
        self.assertIn("每日巡检", wf)
        self.assertEqual(len(wf["每日巡检"]["steps"]), 2)

    def test_unapply_removes_only_plugin_items(self):
        plugins_mod.apply_plugin(_make_plugin(), self.paths)
        # 从目录重读插件（携带 _file / applied 记录，与真实 UI 路径一致）
        plugin = plugins_mod.list_plugins(self.paths["plugins_dir"])[0]
        res = plugins_mod.unapply_plugin(plugin, self.paths)
        self.assertTrue(res["ok"])
        self.assertIn("xhs_writer", res["removed"]["tools"])
        self.assertIn("爆款标题", res["removed"]["skills"])
        self.assertIn("每日巡检", res["removed"]["workflows"])
        # 手动工具保留
        tools = json.load(open(self.paths["user_tools"], encoding="utf-8"))
        self.assertEqual([t["function"]["name"] for t in tools], ["manual_tool"])

    def test_list_plugins(self):
        plugins_mod.apply_plugin(_make_plugin(name="插件A"), self.paths)
        plugins_mod.apply_plugin(_make_plugin(name="插件B", with_workflows=False), self.paths)
        items = plugins_mod.list_plugins(self.paths["plugins_dir"])
        self.assertEqual(len(items), 2)
        names = {p["meta"]["name"] for p in items}
        self.assertEqual(names, {"插件A", "插件B"})
        self.assertTrue(all(p.get("enabled", True) for p in items))

    def test_parse_plugin_file(self):
        p = _make_plugin(name="文件插件")
        path = os.path.join(self.tmp, "文件插件.wtplugin")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False)
        parsed, err = plugins_mod.parse_plugin_file(path)
        self.assertIsNotNone(parsed, err)
        self.assertEqual(parsed["meta"]["name"], "文件插件")
        # 损坏文件
        bad = os.path.join(self.tmp, "bad.wtplugin")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{broken")
        parsed2, err2 = plugins_mod.parse_plugin_file(bad)
        self.assertIsNone(parsed2)
        self.assertIn("解析失败", err2)

    def test_missing_requires(self):
        plugin = _make_plugin(requires=["playwright", "definitely_not_a_real_pkg_xyz"])
        with mock.patch("importlib.util.find_spec",
                        side_effect=lambda mod: None if mod in ("playwright", "definitely_not_a_real_pkg_xyz") else object()):
            missing = plugins_mod.missing_requires(plugin)
        self.assertIn("playwright", missing)
        self.assertIn("definitely_not_a_real_pkg_xyz", missing)

    def test_export_roundtrip(self):
        plugin = _make_plugin(name="往返插件")
        plugins_mod.apply_plugin(plugin, self.paths)
        items = plugins_mod.list_plugins(self.paths["plugins_dir"])
        self.assertEqual(len(items), 1)
        p = items[0]
        export = {k: p.get(k) for k in ("format", "version", "meta", "requires", "contents")}
        path = os.path.join(self.tmp, "out.wtplugin")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False, indent=2)
        parsed, err = plugins_mod.parse_plugin_file(path)
        self.assertIsNotNone(parsed, err)
        self.assertEqual(parsed["meta"]["name"], "往返插件")


class TestCreatePluginTool(unittest.TestCase):
    """插件工坊：AI 通过 create_plugin 工具生成并安装插件。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_cp_")
        dc.PLUGIN_PATHS = {
            "plugins_dir": os.path.join(self.tmp, "plugins"),
            "user_tools": os.path.join(self.tmp, "user_tools.json"),
            "prompts": os.path.join(self.tmp, "prompts.json"),
            "workflows": os.path.join(self.tmp, "workflows.json"),
        }

    def tearDown(self):
        dc.PLUGIN_PATHS = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tool_registered(self):
        names = [t["function"]["name"] for t in dc.TOOLS]
        self.assertIn("create_plugin", names)
        self.assertIn("create_plugin", dc.TOOL_CALL_MAP)

    def test_generate_full_plugin(self):
        out = dc.create_plugin(
            name="小红书助手",
            description="生成小红书文案",
            tools=[{
                "name": "xhs_gen",
                "endpoint": "https://example.com/xhs",
                "description": "生成文案",
                "params": "topic, style",
                "method": "POST",
            }],
            skills=[{"name": "爆款标题", "text": "为 {{TEXT}} 生成标题"}],
            workflows={"每日发文": {"steps": [{"text": "生成草稿"}, {"text": "存入草稿箱"}]}},
            scenario={"name": "创作场景", "thinking": "high", "enabled_tools": ["create_doc"]},
        )
        self.assertIn("已生成并安装", out)
        self.assertIn("xhs_gen", out)
        # 简化工具描述被转换为完整 schema
        tools = json.load(open(dc.PLUGIN_PATHS["user_tools"], encoding="utf-8"))
        fn = tools[0]["function"]
        self.assertEqual(fn["name"], "xhs_gen")
        self.assertEqual(fn["method"], "POST")
        self.assertEqual(list(fn["parameters"]["properties"].keys()), ["topic", "style"])
        self.assertEqual(fn["parameters"]["required"], ["topic", "style"])
        # 技能/流程/插件文件
        prompts = json.load(open(dc.PLUGIN_PATHS["prompts"], encoding="utf-8"))
        self.assertEqual(prompts[0]["name"], "爆款标题")
        wf = json.load(open(dc.PLUGIN_PATHS["workflows"], encoding="utf-8"))
        self.assertIn("每日发文", wf)
        installed = plugins_mod.list_plugins(dc.PLUGIN_PATHS["plugins_dir"])
        self.assertEqual(len(installed), 1)
        self.assertEqual(installed[0]["meta"]["name"], "小红书助手")

    def test_missing_name_rejected(self):
        out = dc.create_plugin(name="  ")
        self.assertIn("错误", out)

    def test_no_contents_rejected(self):
        out = dc.create_plugin(name="空插件")
        self.assertIn("至少一项能力", out)

    def test_invalid_tool_skipped(self):
        out = dc.create_plugin(name="坏工具插件", tools=[{"name": "no_endpoint"}])
        # 无效工具被跳过 → 无能力 → 报错
        self.assertIn("至少一项能力", out)

    def test_uninstall_after_ai_install(self):
        dc.create_plugin(name="临时插件", tools=[{"name": "tmp_tool", "endpoint": "https://e.com/x"}])
        plugin = plugins_mod.list_plugins(dc.PLUGIN_PATHS["plugins_dir"])[0]
        res = plugins_mod.unapply_plugin(plugin, dc.PLUGIN_PATHS)
        self.assertIn("tmp_tool", res["removed"]["tools"])
        tools = json.load(open(dc.PLUGIN_PATHS["user_tools"], encoding="utf-8"))
        self.assertEqual(tools, [])


class TestTimelineItems(unittest.TestCase):
    """会话轨迹：blocks → 混合时间线条目（消息/思考/工具/事件）。"""

    def test_mixed_timeline(self):
        blocks = [
            ("note", "[正在生成...]\n", "time"),
            ("user", "帮我总结\n"),
            ("note", "[00:01] 助手\n"),
            ("thinking", "先分析需求"),
            ("tool", ("write_file", {"path": "a.md"}, "已写入")),
            ("content", "总结如下\n", 2),
            ("note", "[任务完成] ✅ 工具 1 成功 / 0 失败\n"),
            ("plain", "\n"),
        ]
        items = m.AssistantApp._timeline_items(blocks)
        # 去空白 plain 后共 7 项：note/user/note/thinking/tool/content/note
        self.assertEqual(len(items), 7)
        kinds = [txt[:1] for _i, txt, _j in items]
        self.assertEqual(kinds[0], "📌")   # [正在生成]
        self.assertEqual(kinds[1], "💬")   # 用户
        self.assertEqual(kinds[2], "📌")   # 助手时间头
        self.assertEqual(kinds[3], "🧠")   # 思考
        self.assertEqual(kinds[4], "🔧")   # 工具
        self.assertEqual(kinds[5], "🤖")   # 助手回复
        self.assertEqual(kinds[6], "📌")   # 任务完成
        # 只有 content 块可跳转且带正确 msg_idx
        jumpable = [(i, j) for i, _t, j in items if j]
        self.assertEqual(jumpable, [(2, True)])
        # 工具行含名称与参数
        tool_line = next(t for _i, t, _j in items if t.startswith("🔧"))
        self.assertIn("write_file", tool_line)
        self.assertIn("a.md", tool_line)

    def test_failed_tool_mark(self):
        blocks = [("tool", ("fetch_url", {}, "错误：超时"))]
        items = m.AssistantApp._timeline_items(blocks)
        self.assertTrue(items[0][1].startswith("🔧 ❌"))


class TestPluginGallery(unittest.TestCase):
    """插件画廊：内置示例插件扫描与安装。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_gal_")
        self.sample_dir = os.path.join(self.tmp, "sample_plugins")
        os.makedirs(self.sample_dir, exist_ok=True)
        p = _make_plugin(name="示例插件A", with_tools=False, with_workflows=False)
        with open(os.path.join(self.sample_dir, "示例插件A.wtplugin"), "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False)
        self.old_sample = m.SAMPLE_PLUGINS_DIR
        m.SAMPLE_PLUGINS_DIR = self.sample_dir
        self.old_plugins_dir = m.PLUGINS_DIR
        m.PLUGINS_DIR = os.path.join(self.tmp, "plugins")
        # 隔离数据文件（防止污染真实 Documents 数据）
        self.old_prompts = m.PROMPTS_PATH
        self.old_user_tools = m.USER_TOOLS_PATH
        m.PROMPTS_PATH = os.path.join(self.tmp, "prompts.json")
        m.USER_TOOLS_PATH = os.path.join(self.tmp, "user_tools.json")

    def tearDown(self):
        m.SAMPLE_PLUGINS_DIR = self.old_sample
        m.PLUGINS_DIR = self.old_plugins_dir
        m.PROMPTS_PATH = self.old_prompts
        m.USER_TOOLS_PATH = self.old_user_tools
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sample_plugins_valid(self):
        """内置示例插件文件全部合法（分发资产校验）。"""
        import glob

        for fn in glob.glob(os.path.join(self.sample_dir, f"*{plugins_mod.PLUGIN_EXT}")):
            p, err = plugins_mod.parse_plugin_file(fn)
            self.assertIsNotNone(p, f"{fn}: {err}")

    def test_gallery_install(self):
        """画廊安装示例插件：写入插件目录 + 合并数据文件。"""
        samples = []
        for fn in os.listdir(self.sample_dir):
            p, _ = plugins_mod.parse_plugin_file(os.path.join(self.sample_dir, fn))
            if p:
                samples.append(p)
        self.assertEqual(len(samples), 1)
        paths = {
            "plugins_dir": m.PLUGINS_DIR,
            "user_tools": m.USER_TOOLS_PATH,
            "prompts": m.PROMPTS_PATH,
            "workflows": None,
        }
        res = plugins_mod.apply_plugin(samples[0], paths)
        self.assertTrue(res["ok"])
        installed = plugins_mod.list_plugins(m.PLUGINS_DIR)
        self.assertEqual(len(installed), 1)
        self.assertEqual(installed[0]["meta"]["name"], "示例插件A")
        # 技能合并进 prompts
        prompts = json.load(open(m.PROMPTS_PATH, encoding="utf-8"))
        self.assertEqual(prompts[0]["name"], "爆款标题")


class TestPluginDetailText(unittest.TestCase):
    """插件详情渲染：能力明细 + 使用方式。"""

    def test_detail_contains_abilities(self):
        p = _make_plugin(name="详情插件", with_scenario=True)
        text = m.AssistantApp._plugin_detail_text(p)
        self.assertIn("🔧 工具：xhs_writer", text)
        self.assertIn("⚡ 技能：爆款标题", text)
        self.assertIn("🔁 流程：每日巡检（2 步）", text)
        self.assertIn("🎭 场景：小红书场景", text)
        self.assertIn("使用方式", text)

    def test_detail_icon_default(self):
        p = _make_plugin(name="无图标插件")
        text = m.AssistantApp._plugin_detail_text(p)
        self.assertIn("🧩 无图标插件", text)

    def test_detail_icon_custom(self):
        p = _make_plugin(name="带图标插件")
        p["meta"]["icon"] = "📕"
        text = m.AssistantApp._plugin_detail_text(p)
        self.assertIn("📕 带图标插件", text)


class TestPluginSkillsHint(unittest.TestCase):
    """插件技能注入：AI 知晓用户可用的技能模板。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsa_hint_")
        self.old_prompts = m.PROMPTS_PATH
        m.PROMPTS_PATH = os.path.join(self.tmp, "prompts.json")

    def tearDown(self):
        m.PROMPTS_PATH = self.old_prompts
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hint_lists_plugin_skills(self):
        with open(m.PROMPTS_PATH, "w", encoding="utf-8") as f:
            json.dump([
                {"name": "爆款标题", "text": "x", "_source": "plugin:小红书文案助手"},
                {"name": "中译英", "text": "y"},  # 内置模板不带 _source
            ], f, ensure_ascii=False)
        hint = m.AssistantApp._plugin_skills_hint()
        self.assertIn("爆款标题", hint)
        self.assertNotIn("中译英", hint)  # 内置模板不提示

    def test_no_plugin_skills_empty(self):
        with open(m.PROMPTS_PATH, "w", encoding="utf-8") as f:
            json.dump([{"name": "中译英", "text": "y"}], f, ensure_ascii=False)
        self.assertEqual(m.AssistantApp._plugin_skills_hint(), "")


if __name__ == "__main__":
    unittest.main()
