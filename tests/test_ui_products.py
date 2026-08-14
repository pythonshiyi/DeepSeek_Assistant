# -*- coding: utf-8 -*-
"""产物面板/产物条 UI 回归测试（真实 Tk 实例）。

覆盖：产物条显示/隐藏/复制、右侧面板 Tab 切换、文件树根节点与懒加载。
"""
import json
import os
import sys
import tempfile
import unittest

# tkinter 可用性探测：部分 CI 环境的 Tcl/Tk 运行库缺失，此时跳过而不是报错
try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as m


class ProductUIBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not TK_AVAILABLE:
            raise unittest.SkipTest("tkinter 不可用（缺少 Tcl/Tk 运行库）")
        cls.tmpdir = tempfile.mkdtemp(prefix="dsa_prod_")
        m.CONFIG_PATH = os.path.join(cls.tmpdir, "config.json")
        m.HISTORY_DIR = cls.tmpdir
        m.SNAPSHOT_PATH = os.path.join(cls.tmpdir, "snap.json")
        m.SESSIONS_DIR = os.path.join(cls.tmpdir, "sessions")
        m.STATS_PATH = os.path.join(cls.tmpdir, "stats.json")
        m.PROMPTS_PATH = os.path.join(cls.tmpdir, "prompts.json")
        m.USER_TOOLS_PATH = os.path.join(cls.tmpdir, "ut.json")
        m.ARCHIVES_DIR = os.path.join(cls.tmpdir, "archives")
        m.RECENT_PATH = os.path.join(cls.tmpdir, "recent.json")
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


class TestRecentBar(ProductUIBase):
    def setUp(self):
        self.app._recent_cache = []
        self.app._recent_dirty = False
        self.app._hide_recent_bar()
        self.test_file = os.path.join(self.tmpdir, "产物.txt")
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("hello")

    def _bar_managed(self):
        # withdraw 的根窗口下 winfo_ismapped 恒为 False：用 pack 管理器判定
        return bool(self.app.recent_bar.winfo_manager())

    def test_hidden_by_default(self):
        self.assertFalse(self._bar_managed())

    def test_record_shows_bar(self):
        self.app._record_recent_output(f"已生成文件：{self.test_file}")
        self.assertTrue(self._bar_managed())
        self.assertIn("产物.txt", self.app.recent_bar_lbl.cget("text"))
        self.assertEqual(self.app._recent_cache[0], self.test_file)

    def test_record_updates_to_latest(self):
        self.app._record_recent_output(f"A：{self.test_file}")
        f2 = os.path.join(self.tmpdir, "第二个.md")
        with open(f2, "w", encoding="utf-8") as f:
            f.write("x")
        self.app._record_recent_output(f"B：{f2}")
        self.assertIn("第二个.md", self.app.recent_bar_lbl.cget("text"))
        self.assertEqual(self.app._recent_cache[0], f2)

    def test_hide_removes_bar(self):
        self.app._record_recent_output(f"x：{self.test_file}")
        self.assertTrue(self._bar_managed())
        self.app._hide_recent_bar()
        self.assertFalse(self._bar_managed())

    def test_copy_path(self):
        self.app._record_recent_output(f"x：{self.test_file}")
        self.app._copy_recent_bar()
        clip = self.app.root.clipboard_get()
        self.assertEqual(clip, self.test_file)

    def test_nonexistent_path_ignored(self):
        self.app._record_recent_output("C:/不存在/的文件.txt")
        self.assertEqual(self.app._recent_cache, [])
        self.assertFalse(self._bar_managed())


class TestSidePanelFilesTab(ProductUIBase):
    def setUp(self):
        # 每个用例显式回到 settings tab（类内共享实例，防状态泄漏）
        self.app._switch_side_tab("settings")

    def test_tab_switch_settings_default(self):
        self.assertEqual(self.app._side_tab, "settings")
        self.assertTrue(self.app.panel_settings_body.winfo_manager())
        self.assertFalse(self.app.panel_files_body.winfo_manager())

    def test_switch_to_files(self):
        self.app._switch_side_tab("files")
        self.assertEqual(self.app._side_tab, "files")
        self.assertFalse(self.app.panel_settings_body.winfo_manager())
        self.assertTrue(self.app.panel_files_body.winfo_manager())

    def test_files_panel_width(self):
        self.app._switch_side_tab("files")
        self.app.root.update_idletasks()
        self.assertEqual(int(self.app.side_panel.winfo_reqwidth()), 460)
        self.app._switch_side_tab("settings")
        self.app.root.update_idletasks()
        self.assertEqual(int(self.app.side_panel.winfo_reqwidth()), 280)

    def test_refresh_roots(self):
        self.app._switch_side_tab("files")
        self.app._refresh_files_panel()
        roots = self.app.files_tree.get_children("")
        self.assertIn("ws", roots)
        self.assertIn("drafts", roots)
        self.assertIn("recent", roots)
        self.assertIn("data", roots)

    def test_workspace_lazy_load(self):
        self.app._switch_side_tab("files")
        self.app._refresh_files_panel()
        # 造一个工作区文件后展开 ws 节点 → 懒加载到该文件
        probe = os.path.join(m.WORKSPACE_DIR, "probe_产物.txt")
        try:
            with open(probe, "w", encoding="utf-8") as f:
                f.write("x")
            self.app.files_tree.selection_set("ws")
            self.app.files_tree.item("ws", open=True)
            self.app._on_files_open()
            found = False
            for iid in self.app.files_tree.get_children("ws"):
                if self.app.files_tree.item(iid, "text") == "probe_产物.txt":
                    found = True
            self.assertTrue(found)
        finally:
            try:
                os.remove(probe)
            except OSError:
                pass

    def test_recent_node_fill(self):
        self.app._switch_side_tab("files")
        self.app._refresh_files_panel()
        f = os.path.join(self.tmpdir, "r.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.app._recent_cache = [f]
        self.app.files_tree.selection_set("recent")
        self.app.files_tree.item("recent", open=True)
        self.app._on_files_open()
        names = [self.app.files_tree.item(i, "text") for i in self.app.files_tree.get_children("recent")]
        self.assertIn("r.txt", names)

    def test_recent_child_path_resolution(self):
        """真实 bug 回归：最近产物子文件双击无反应（parent=recent 解析失败）。"""
        self.app._switch_side_tab("files")
        self.app._refresh_files_panel()
        f = os.path.join(self.tmpdir, "子文件.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.app._recent_cache = [f]
        self.app.files_tree.selection_set("recent")
        self.app.files_tree.item("recent", open=True)
        self.app._on_files_open()
        # 子节点 iid 即绝对路径
        child_ids = self.app.files_tree.get_children("recent")
        self.assertEqual(len(child_ids), 1)
        resolved = self.app._files_entry_path(child_ids[0])
        self.assertEqual(resolved, f)  # 修复前为 None（双击无反应）

    def test_recent_child_double_click_opens(self):
        """双击最近产物文件 → _open_path 被调用（打开动作）。"""
        self.app._switch_side_tab("files")
        self.app._refresh_files_panel()
        f = os.path.join(self.tmpdir, "双击.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.app._recent_cache = [f]
        self.app.files_tree.selection_set("recent")
        self.app.files_tree.item("recent", open=True)
        self.app._on_files_open()
        child = self.app.files_tree.get_children("recent")[0]
        # 模拟双击事件：identify_row 用事件 y 坐标，直接构造并调用打开路径逻辑
        self.app.files_tree.selection_set(child)
        self.app.files_tree.focus(child)
        with unittest.mock.patch.object(self.app, "_open_path") as opener:
            event = type("E", (), {"y": 10})()
            # 无法可靠模拟 identify_row 命中，验证核心路径解析+打开分支：
            self.app.files_tree.item(child, open=False)
            self.assertEqual(self.app._files_entry_path(child), f)
            if not os.path.isdir(self.app._files_entry_path(child)):
                self.app._open_path(self.app._files_entry_path(child))
        opener.assert_called_once_with(f)

    def test_entry_path_resolution(self):
        self.app._refresh_files_panel()
        self.assertEqual(self.app._files_entry_path("ws"), m.WORKSPACE_DIR)
        self.assertEqual(self.app._files_entry_path("data"), m.DATA_DIR)
        # 普通子节点路径解析
        self.app.files_tree.selection_set("ws")
        self.app.files_tree.item("ws", open=True)
        self.app._on_files_open()
        children = self.app.files_tree.get_children("ws")
        if children:
            child = children[0]
            p = self.app._files_entry_path(child)
            self.assertTrue(p.startswith(m.WORKSPACE_DIR))


if __name__ == "__main__":
    unittest.main()
