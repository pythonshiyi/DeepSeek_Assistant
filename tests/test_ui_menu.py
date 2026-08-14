# -*- coding: utf-8 -*-
"""菜单体系测试：精确分类（导航按钮原则）——顶级菜单结构 + 级联分组 + 快捷键。"""
import json
import os
import sys
import tempfile
import tkinter as tk
import unittest
from unittest import mock

import main as m


def _make_app():
    tmpdir = tempfile.mkdtemp(prefix="dsa_menu_")
    m.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    m.HISTORY_DIR = tmpdir
    m.SNAPSHOT_PATH = os.path.join(tmpdir, "snap.json")
    m.SESSIONS_DIR = os.path.join(tmpdir, "sessions")
    m.STATS_PATH = os.path.join(tmpdir, "stats.json")
    m.PROMPTS_PATH = os.path.join(tmpdir, "prompts.json")
    m.USER_TOOLS_PATH = os.path.join(tmpdir, "ut.json")
    m.ARCHIVES_DIR = os.path.join(tmpdir, "archives")
    m.RECENT_PATH = os.path.join(tmpdir, "recent.json")
    m.CLEAN_EXIT_FLAG = os.path.join(tmpdir, ".clean_exit")
    m.DRAFT_PATH = os.path.join(tmpdir, "draft.json")
    m.PLUGINS_DIR = os.path.join(tmpdir, "plugins")
    m.WORKSPACE_DIR = os.path.join(tmpdir, "ws")
    os.makedirs(m.SESSIONS_DIR, exist_ok=True)
    os.makedirs(m.ARCHIVES_DIR, exist_ok=True)
    os.makedirs(m.WORKSPACE_DIR, exist_ok=True)
    with open(m.CLEAN_EXIT_FLAG, "w") as f:
        f.write("ok")
    with open(m.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"welcomed": True, "restore_session": False}, f)
    root = tk.Tk()
    root.withdraw()
    app = m.AssistantApp(root)
    return tmpdir, root, app


class TestMenuStructure(unittest.TestCase):
    """菜单 = 导航按钮：分类精确、直达目标。"""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir, cls.root, cls.app = _make_app()

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

    def _menu_labels(self, menu):
        """收集菜单项标签（command 与 cascade 项）。"""
        labels = []
        try:
            end = menu.index("end")
        except tk.TclError:
            return labels
        for i in range(end + 1):
            try:
                labels.append(str(menu.entrycget(i, "label")))
            except tk.TclError:
                continue
        return labels

    def _top_menu(self, title):
        """按标题定位顶级菜单（_menu_by_title 映射，_menus 列表含级联子菜单）。"""
        for k, v in self.app._menu_by_title.items():
            if title in k:
                return v
        return None

    def test_top_level_menus(self):
        """顶级菜单：文件/编辑/视图/工具/自动化/设置/帮助（7 个，精确分类）。"""
        labels = [b.cget("text") for b in self.app._menu_buttons]
        self.assertEqual(len(labels), 7)
        for expect in ("文件(F)", "编辑(E)", "视图(V)", "工具(T)", "自动化(A)", "设置(S)", "帮助(H)"):
            self.assertIn(expect, labels, expect)

    def test_automation_menu_exists(self):
        """「自动化」独立顶级菜单：定时/流程/知识库/检查点/任务记录。"""
        am = self._top_menu("自动化")
        self.assertIsNotNone(am)
        labels = self._menu_labels(am)
        joined = " ".join(labels)
        for kw in ("定时任务", "流程管理", "知识库", "任务检查点", "项目任务记录", "每日简报"):
            self.assertIn(kw, joined, kw)

    def test_tool_menu_grouped_cascades(self):
        """工具菜单：中心入口置顶 + 精确级联分组（账户/任务/文件/能力/进化/系统）。"""
        tm = self._top_menu("工具")
        self.assertIsNotNone(tm)
        labels = self._menu_labels(tm)
        self.assertIn("🛠 工具中心", labels)
        self.assertIn("🧩 插件中心", labels)
        for group in ("账户与用量", "任务与模板", "文件与产物", "能力扩展", "🧬 自我进化", "系统"):
            self.assertIn(group, labels, group)

    def test_tool_menu_top_entries(self):
        """中心入口是工具菜单最前两个导航按钮（高频直达）。"""
        tm = self._top_menu("工具")
        first = tm.entrycget(0, "label")
        second = tm.entrycget(1, "label")
        self.assertIn("工具中心", first)
        self.assertIn("插件中心", second)

    def test_settings_menu_grouped(self):
        """设置菜单：AI 行为 / 应用行为 分组。"""
        sm = self._top_menu("设置")
        self.assertIsNotNone(sm)
        labels = self._menu_labels(sm)
        joined = " ".join(labels)
        # AI 行为组（角色与提示词统一入口）
        self.assertIn("角色与提示词", joined)
        self.assertIn("strict 工具模式", joined)
        # 应用行为组
        for kw in ("完成通知", "项目上下文", "隐私模式", "开机自启", "最小化到托盘"):
            self.assertIn(kw, joined, kw)
        # 系统提示词独立入口已合并进「角色与提示词」（消除重复入口）
        self.assertNotIn("系统提示词…", joined)
        # 分隔线 ≥2（分组）
        sep = 0
        try:
            end = sm.index("end")
        except tk.TclError:
            end = -1
        for i in range(end + 1):
            try:
                if sm.type(i) == "separator":
                    sep += 1
            except tk.TclError:
                continue
        self.assertGreaterEqual(sep, 2)

    def test_view_menu_fullscreen(self):
        """视图菜单含全屏入口（F11）。"""
        vm = self._top_menu("视图")
        labels = self._menu_labels(vm)
        self.assertTrue(any("全屏" in l for l in labels))

    def test_alt_bindings_seven(self):
        """Alt 快捷键覆盖全部 7 个顶级菜单。"""
        self.assertEqual(
            self.app._alt_menu_bindings,
            {"f": 0, "e": 1, "v": 2, "t": 3, "a": 4, "s": 5, "h": 6},
        )

    def test_hub_overview_nav_buttons(self):
        """工具中心概览：快捷操作 + 导航双行按钮（聚合入口）。"""
        self.app.show_tool_hub()
        hubs = [w for w in self.app.root.winfo_children()
                if isinstance(w, tk.Toplevel) and "工具中心" in w.title()]
        self.assertTrue(hubs)
        labels = []
        stack = list(hubs)
        while stack:
            w = stack.pop()
            if isinstance(w, tk.Button):
                labels.append(w.cget("text"))
            stack.extend(w.winfo_children())
        joined = " ".join(labels)
        for kw in ("查余额", "用量统计", "定时任务", "流程管理", "知识库", "最近产物", "插件中心"):
            self.assertIn(kw, joined, kw)
        for d in hubs:
            d.destroy()


class TestRoleSystem(unittest.TestCase):
    """角色与提示词统一：当前角色识别 + 双向切换（消除重复入口）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir, cls.root, cls.app = _make_app()

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

    def test_current_role_matches_preset(self):
        """系统提示词与预设一致 → 识别为对应角色。"""
        self.assertEqual(m.AssistantApp._current_role_name(m.ROLES["翻译官"]["prompt"]), "翻译官")
        self.assertEqual(m.AssistantApp._current_role_name(m.ROLES["周报助手"]["prompt"]), "周报助手")

    def test_current_role_custom(self):
        """不匹配任何预设 → 自定义。"""
        self.assertEqual(m.AssistantApp._current_role_name("完全自定义的提示词"), "自定义")

    def test_role_dialog_has_custom_and_presets(self):
        """统一对话框：列表含全部预设 + 自定义项。"""
        self.app.show_roles()
        dialogs = [w for w in self.app.root.winfo_children()
                   if isinstance(w, tk.Toplevel) and "角色与提示词" in w.title()]
        self.assertTrue(dialogs, "角色与提示词对话框未创建")
        listbox = None
        stack = list(dialogs)
        while stack:
            w = stack.pop()
            if isinstance(w, tk.Listbox):
                listbox = w
                break
            stack.extend(w.winfo_children())
        self.assertIsNotNone(listbox)
        items = list(listbox.get(0, "end"))
        for name in m.ROLES:
            self.assertTrue(any(name in it for it in items), name)
        self.assertTrue(any("自定义" in it for it in items), "应包含自定义项")
        for d in dialogs:
            d.destroy()

    def test_apply_role_updates_state(self):
        """应用角色：cfg + messages[0] + 角色显示联动。"""
        self.app.cfg["system_prompt"] = "旧提示词"
        self.app.messages[0]["content"] = "旧提示词"
        with mock.patch.object(m.messagebox, "askyesno", return_value=True):
            self.app.apply_role("翻译官")
        self.assertEqual(self.app.cfg["system_prompt"], m.ROLES["翻译官"]["prompt"])
        self.assertEqual(self.app.messages[0]["content"], m.ROLES["翻译官"]["prompt"])
        self.assertEqual(m.AssistantApp._current_role_name(self.app.cfg["system_prompt"]), "翻译官")
        self.assertEqual(self.app.cfg["thinking"], "high")

    def test_user_role_crud(self):
        """用户角色：新增 → 识别 → 应用 → 编辑 → 删除（持久化）。"""
        m.USER_ROLES_PATH = os.path.join(self.tmpdir, "user_roles.json")
        try:
            # 新增
            ok = m.AssistantApp.save_user_roles([
                {"name": "小红书专家", "prompt": "你是小红书爆款专家", "thinking": "high",
                 "desc": "生成爆款文案", "category": "写作"},
            ])
            self.assertTrue(ok)
            all_roles = m.AssistantApp.load_all_roles()
            self.assertTrue(any(r["name"] == "小红书专家" for r in all_roles))
            self.assertEqual(
                m.AssistantApp._current_role_name("你是小红书爆款专家"), "小红书专家"
            )
            # 应用用户角色
            self.app.cfg["system_prompt"] = "旧"
            with mock.patch.object(m.messagebox, "askyesno", return_value=True):
                self.app.apply_role("小红书专家")
            self.assertEqual(self.app.cfg["system_prompt"], "你是小红书爆款专家")
            # 编辑
            self.assertTrue(m.AssistantApp.save_user_roles([
                {"name": "小红书专家", "prompt": "你是小红书爆款专家v2", "thinking": "max",
                 "desc": "x", "category": "写作"},
            ]))
            self.assertEqual(
                m.AssistantApp._current_role_name("你是小红书爆款专家v2"), "小红书专家"
            )
            # 删除
            self.assertTrue(m.AssistantApp.save_user_roles([]))
            self.assertEqual(m.AssistantApp.load_all_roles(), m.AssistantApp.load_all_roles())
            self.assertNotIn("小红书专家",
                             [r["name"] for r in m.AssistantApp.load_all_roles()])
        finally:
            m.USER_ROLES_PATH = os.path.join(m.DATA_DIR, "user_roles.json")

    def test_mode_change_keeps_persona(self):
        """自主模式切换不触碰人格（人格独立，任务能力由模式表达）。"""
        self.app.cfg["system_prompt"] = "我的自定义人格"
        self.app.messages[0]["content"] = "我的自定义人格"
        with mock.patch.object(m.messagebox, "askyesno", return_value=True):
            self.app.mode_var.set("full_auto")
            self.app._on_mode_change()
        # 模式只表达任务能力（权限/工具），不改写 system_prompt
        self.assertEqual(self.app.cfg["system_prompt"], "我的自定义人格")
        self.assertEqual(self.app.messages[0]["content"], "我的自定义人格")
        self.assertTrue(self.app.cfg["full_auto"])


if __name__ == "__main__":
    unittest.main()
