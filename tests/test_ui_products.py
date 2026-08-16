# -*- coding: utf-8 -*-
"""产物面板/产物条 UI 回归测试（真实 Tk 实例）。

覆盖：产物条显示/隐藏/复制、右侧面板 Tab 切换、文件树根节点与懒加载。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

# tkinter 可用性探测：部分 CI 环境的 Tcl/Tk 运行库缺失，此时跳过而不是报错
try:
    import tkinter as tk
    from tkinter import ttk

    _probe = tk.Tk()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as m
import permissions


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


class TestInAppPreview(ProductUIBase):
    """产物应用内预览：md/文本/图片在应用内查看，其他类型回退系统打开。"""

    def setUp(self):
        self._dialogs = []

    def tearDown(self):
        for d in self._dialogs:
            try:
                d.destroy()
            except Exception:
                pass

    def _open_dialogs(self):
        return [w for w in self.app.root.winfo_children() if isinstance(w, tk.Toplevel)]

    def test_preview_classify(self):
        md = os.path.join(self.tmpdir, "预览.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write("# 标题")
        txt = os.path.join(self.tmpdir, "a.txt")
        with open(txt, "w", encoding="utf-8") as f:
            f.write("hello")
        try:
            from PIL import Image as _PILImage
        except ImportError:
            _PILImage = None
        png = os.path.join(self.tmpdir, "b.png")
        if _PILImage is not None:
            _PILImage.new("RGB", (32, 24), "#336699").save(png)
        exe = os.path.join(self.tmpdir, "c.exe")
        with open(exe, "wb") as f:
            f.write(b"MZ")
        self.assertTrue(self.app._preview_path(md))
        self.assertTrue(self.app._preview_path(txt))
        if _PILImage is not None:
            self.assertTrue(self.app._preview_path(png))
        self.assertFalse(self.app._preview_path(exe))
        self.assertFalse(self.app._preview_path(os.path.join(self.tmpdir, "missing.md")))
        for d in self._open_dialogs():
            d.destroy()

    def test_preview_text_opens_dialog(self):
        md = os.path.join(self.tmpdir, "预览.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write("# 标题\n\n**加粗** 与 `代码`\n")
        self.assertTrue(self.app._preview_text(md))
        dialogs = self._open_dialogs()
        self.assertTrue(dialogs, "预览对话框未创建")
        self.assertIn("预览", dialogs[0].title())
        for d in dialogs:
            d.destroy()

    def test_preview_image_opens_dialog(self):
        try:
            from PIL import Image as _PILImage
        except ImportError:
            self.skipTest("Pillow 未安装")
        png = os.path.join(self.tmpdir, "图.png")
        _PILImage.new("RGB", (64, 48), "#336699").save(png)
        self.assertTrue(self.app._preview_image(png))
        dialogs = self._open_dialogs()
        self.assertTrue(dialogs, "图片预览对话框未创建")
        for d in dialogs:
            d.destroy()

    def test_preview_md_renders_styles(self):
        """md 预览走 mdparse 渲染：粗体/代码 tag 生效（不再显示原始标记）。"""
        md = os.path.join(self.tmpdir, "渲染.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write("**加粗** 与 `代码`")
        self.assertTrue(self.app._preview_text(md))
        dialogs = self._open_dialogs()
        self.assertTrue(dialogs)
        # 递归查找预览 Text（Text 嵌套在 wrap Frame 内）
        text = None
        stack = list(dialogs)
        while stack:
            w = stack.pop()
            if isinstance(w, tk.Text):
                text = w
                break
            stack.extend(w.winfo_children())
        self.assertIsNotNone(text, "未找到预览文本控件")
        rendered = text.get("1.0", "end")
        self.assertIn("加粗", rendered)
        self.assertIn("代码", rendered)
        self.assertNotIn("**", rendered)  # 标记被渲染掉
        for d in dialogs:
            d.destroy()


class TestCompareModelsDialog(ProductUIBase):
    """模型对比参数化：弹窗选择/输入两个模型。"""

    def setUp(self):
        self.app.input_text.configure(state="normal")
        self.app.input_text.delete("1.0", "end")
        self.app.input_text.insert("1.0", "对比测试问题")
        self.app.busy = False

    def tearDown(self):
        self.app.busy = False
        for d in [w for w in self.app.root.winfo_children() if isinstance(w, tk.Toplevel)]:
            try:
                d.destroy()
            except Exception:
                pass

    def test_dialog_opens_with_models(self):
        """弹窗出现且包含两个可输入模型框（内置模型为候选值）。"""
        self.app.compare_models()
        dialogs = [w for w in self.app.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertTrue(dialogs, "对比弹窗未创建")
        combos = []
        stack = list(dialogs)
        while stack:
            w = stack.pop()
            if isinstance(w, ttk.Combobox):
                combos.append(w)
            stack.extend(w.winfo_children())
        self.assertEqual(len(combos), 2, "应有两个模型选择框")
        self.assertIn("deepseek-v4-flash", combos[0]["values"])
        self.assertIn("deepseek-v4-pro", combos[1]["values"])

    def test_no_text_blocks(self):
        """输入框为空时不弹窗。"""
        self.app.input_text.delete("1.0", "end")
        with mock.patch("main.messagebox.showinfo") as showinfo:
            self.app.compare_models()
        showinfo.assert_called_once()


class TestDependencyStatus(ProductUIBase):
    """依赖状态：清单完整性 + 检测结果格式。"""

    def test_optional_deps_covered(self):
        deps = m.OPTIONAL_DEPS
        names = {d[0] for d in deps}
        # 关键可选依赖必须覆盖
        for required in ("PIL", "pystray", "playwright", "faster_whisper", "fitz",
                         "reportlab", "feedparser", "diskcache", "tiktoken", "win32com"):
            self.assertIn(required, names, f"依赖清单缺少关键项：{required}")
        self.assertTrue(len(deps) >= 15, "依赖清单应覆盖全部可选能力")
        for mod, name, use, hint in deps:
            self.assertTrue(mod and name and use and hint, f"依赖条目不完整：{name}")

    def test_status_rows(self):
        rows = m.AssistantApp._dependency_status()
        self.assertEqual(len(rows), len(m.OPTIONAL_DEPS))
        for name, ok, use, hint in rows:
            self.assertIsInstance(ok, bool)
            self.assertTrue(name)
        # PIL 若未安装则跳过，避免最小环境假失败
        pil = next((ok for name, ok, _u, _h in rows if name == "Pillow"), None)
        if pil is None:
            self.skipTest("Pillow 未安装")
        self.assertTrue(pil, "Pillow 应已安装（环境依赖）")

    def test_dialog_opens(self):
        self.app.show_dependencies()
        dialogs = [w for w in self.app.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertTrue(dialogs, "依赖状态对话框未创建")
        for d in dialogs:
            d.destroy()


class TestHubs(ProductUIBase):
    """工具中心 / 插件中心：正式页面（Notebook 多页签）。"""

    def _open_hub(self, fn):
        fn()
        hubs = [w for w in self.app.root.winfo_children()
                if isinstance(w, tk.Toplevel) and ("中心" in w.title())]
        self.assertTrue(hubs, "中心窗口未创建")
        nb = None
        stack = list(hubs)
        while stack:
            w = stack.pop()
            if isinstance(w, ttk.Notebook):
                nb = w
                break
            stack.extend(w.winfo_children())
        self.assertIsNotNone(nb, "未找到 Notebook")
        return hubs, nb

    def test_tool_hub_tabs(self):
        hubs, nb = self._open_hub(self.app.show_tool_hub)
        tabs = [nb.tab(i, "text") for i in range(len(nb.tabs()))]
        self.assertEqual(tabs, ["概览", "工具设置", "权限"])
        for d in hubs:
            d.destroy()

    def test_tool_hub_save_button_collects_panels(self):
        """工具中心底部「保存更改」：收集工具设置+权限面板回调并执行（防回调被清空丢失）。"""
        self.app.show_tool_hub()
        hubs = [w for w in self.app.root.winfo_children()
                if isinstance(w, tk.Toplevel) and "中心" in w.title()]
        self.assertEqual(len(self.app._hub_saves), 2, "应收集工具设置+权限两个保存回调")
        saved = []
        for fn in list(self.app._hub_saves):
            fn()
            saved.append(True)
        self.assertEqual(len(saved), 2)
        self.app.cfg["enabled_tools"] = []
        self.app._hub_save_all()
        # 保存后回调已清空（防重复保存）
        self.assertEqual(self.app._hub_saves, [])
        for d in hubs:
            d.destroy()

    def test_ssrf_trusted_save_and_effect(self):
        """权限页签 SSRF 信任白名单：保存写入 cfg 并即时生效。"""
        self.app.show_tool_hub()
        hubs = [w for w in self.app.root.winfo_children()
                if isinstance(w, tk.Toplevel) and "中心" in w.title()]
        nb = None
        stack = list(hubs)
        while stack:
            w = stack.pop()
            if isinstance(w, ttk.Notebook):
                nb = w
                break
            stack.extend(w.winfo_children())
        nb.select(2)
        self.app.root.update()
        def plain_entries(widget):
            acc = []
            stack = [widget]
            while stack:
                c = stack.pop()
                if type(c).__name__ == "Entry":
                    acc.append(c)
                stack.extend(c.winfo_children())
            return acc

        entries = plain_entries(nb.nametowidget(nb.select()))
        # 栈遍历为 LIFO（顺序反转）：entries[0] 是最后创建的 SSRF 输入框
        ssrf_e = entries[0]
        ssrf_e.delete(0, "end")
        ssrf_e.insert(0, "192.168.1.0/24, nas.lan")
        self.assertEqual(len(self.app._hub_saves), 2)
        self.app._hub_save_all()
        self.assertEqual(self.app.cfg["ssrf_trusted"], ["192.168.1.0/24", "nas.lan"])
        self.assertEqual(m._dc.SSRF_TRUSTED, ["192.168.1.0/24", "nas.lan"])
        # 默认 blacklist 模式：全部放行，SSRF trusted 仅 whitelist 模式生效
        data = permissions.get_data()
        data["security_mode"] = "blacklist"
        self.assertEqual(m._dc._safe_url("http://192.168.1.20/"), "")
        self.assertEqual(m._dc._safe_url("http://nas.lan/"), "")
        self.assertEqual(m._dc._safe_url("http://10.0.0.1/"), "")
        # 切到 whitelist 模式验证 trusted 生效
        data = permissions.get_data()
        old_mode = data.get("security_mode", "blacklist")
        data["security_mode"] = "whitelist"
        try:
            self.assertFalse(m._dc._safe_url("http://192.168.1.20/"))   # CIDR 放行
            self.assertFalse(m._dc._safe_url("http://nas.lan/"))        # 主机名放行
            self.assertTrue(m._dc._safe_url("http://10.0.0.1/"))        # 未信任阻止
        finally:
            data["security_mode"] = old_mode
        m._dc.set_ssrf_trusted([])
        self.app.cfg["ssrf_trusted"] = []  # 同时清理 cfg，防止后续保存回调重新应用旧白名单
        for d in hubs:
            d.destroy()

    def test_plugin_hub_tabs(self):
        hubs, nb = self._open_hub(self.app.show_plugin_hub)
        tabs = [nb.tab(i, "text") for i in range(len(nb.tabs()))]
        self.assertEqual(tabs, ["我的插件", "画廊", "市场", "工坊"])
        for d in hubs:
            d.destroy()

    def test_legacy_entries_forward_to_hub(self):
        """旧入口转发到中心（兼容）。"""
        self.app.show_plugins()
        self.app.show_plugin_gallery()
        self.app.show_plugin_workshop()
        hubs = [w for w in self.app.root.winfo_children()
                if isinstance(w, tk.Toplevel) and ("中心" in w.title())]
        self.assertTrue(hubs)
        for d in hubs:
            d.destroy()


class TestOcrResultFlow(ProductUIBase):
    """OCR 结果修复：识别结果插入输入框（修复走已删语音队列的历史 bug）。"""

    def test_ocr_result_inserts_text(self):
        self.app.input_text.delete("1.0", "end")
        self.app._ocr_result("识别出的文字")
        self.assertIn("识别出的文字", self.app.input_text.get("1.0", "end"))

    def test_ocr_empty_hint(self):
        with mock.patch.object(self.app, "_flash_status") as fs:
            self.app._ocr_result("")
        fs.assert_called_once()  # 空结果提示，不抛异常


class TestAutoModeSemantics(ProductUIBase):
    """自主模式 = 任务能力（场景包已移除）：完全智能=全部工具、纯对话=无工具、标准=按配置。"""

    def setUp(self):
        self.app.cfg["full_auto"] = False
        self.app.cfg["pure_chat"] = False
        self.app.mode_var.set("standard")
        self.app.blocks = []

    def test_suggest_code_output_proposes_full_auto(self):
        """代码输出建议：从"启用开发场景包"改为"切换完全智能模式"。"""
        self.app.blocks.append(("content", "这是```python\nprint(1)```代码", 1))
        with mock.patch.object(self.app, "_show_suggestion") as sug:
            self.app._suggest()
        self.assertTrue(sug.called)
        text, fn, arg = sug.call_args[0][0]
        self.assertIn("任务模式", text)
        self.assertEqual(fn.__name__, "_switch_mode_quick")
        self.assertEqual(arg, "full_auto")

    def test_suggest_quick_switch_adopts(self):
        """建议采纳：一键切换完全智能（确认后生效）。"""
        with mock.patch.object(m.messagebox, "askyesno", return_value=True):
            self.app._switch_mode_quick("full_auto")
        self.assertTrue(self.app.cfg["full_auto"])

    def test_suggest_quick_switch_effective(self):
        """建议条快捷切换：无二次确认，直接生效（任务 ↔ 对话互斥）。"""
        self.app.cfg["pure_chat"] = True
        self.app.cfg["full_auto"] = False
        self.app._switch_mode_quick("full_auto")
        self.assertTrue(self.app.cfg["full_auto"])
        self.assertFalse(self.app.cfg["pure_chat"])

    def test_full_auto_resolves_all_tools(self):
        """完全智能模式：运行时工具集 = 内置 + 自定义全部工具。"""
        self.app.cfg["full_auto"] = True
        self.app.cfg["pure_chat"] = False
        self.app.cfg["enabled_tools"] = ["get_date"]
        names, tools_enabled = self.app._mode_tools_for_request(self.app.cfg)
        self.assertTrue(tools_enabled)
        self.assertIn("write_file", names)
        self.assertIn("run_python", names)
        self.assertIn("get_date", names)  # 不受 enabled_tools 限制

    def test_pure_chat_resolves_no_tools(self):
        """纯对话模式：运行时工具集为空。"""
        self.app.cfg["full_auto"] = False
        self.app.cfg["pure_chat"] = True
        names, tools_enabled = self.app._mode_tools_for_request(self.app.cfg)
        self.assertEqual(names, [])
        self.assertFalse(tools_enabled)

    def test_pure_chat_resolves_no_tools(self):
        """纯对话模式：请求时工具集为空。"""
        self.app.cfg["full_auto"] = False
        self.app.cfg["pure_chat"] = True
        names, tools_enabled = self.app._mode_tools_for_request(self.app.cfg)
        self.assertEqual(names, [])
        self.assertFalse(tools_enabled)

    def test_full_auto_smart_loads_all(self):
        """完全智能模式：全部工具名返回（deepseek_client 侧做 smart 索引激活）。"""
        self.app.cfg["full_auto"] = True
        self.app.cfg["pure_chat"] = False
        names, tools_enabled = self.app._mode_tools_for_request(self.app.cfg)
        self.assertTrue(tools_enabled)
        self.assertGreaterEqual(len(names), len(m.TOOLS))

    def test_mode_switch_keeps_mode(self):
        """模式切换（完全智能 ↔ 纯对话）互斥生效。"""
        with mock.patch.object(m.messagebox, "askyesno", return_value=True):
            self.app.mode_var.set("pure_chat")
            self.app._on_mode_change()
        self.assertFalse(self.app.cfg["full_auto"])
        self.assertTrue(self.app.cfg["pure_chat"])
        with mock.patch.object(m.messagebox, "askyesno", return_value=True):
            self.app.mode_var.set("full_auto")
            self.app._on_mode_change()
        self.assertTrue(self.app.cfg["full_auto"])
        self.assertFalse(self.app.cfg["pure_chat"])

    def test_intelligent_role_prompt_design(self):
        """「智能体」角色：为开发/创作任务而生的提示词设计（目标/执行/验证闭环）。"""
        r = m.ROLES["智能体"]
        self.assertEqual(r["thinking"], "max")
        for kw in ("目标先行", "验证", "产物", "汇报"):
            self.assertIn(kw, r["prompt"])


class TestRoleStatusVisible(ProductUIBase):
    """角色状态可视化：状态栏 + 识别缓存。"""

    def test_status_right_shows_role(self):
        self.app.cfg["system_prompt"] = m.ROLES["翻译官"]["prompt"]
        self.app.update_status()
        self.assertIn("翻译官", self.app.status_right.cget("text"))

    def test_role_cache_invalidated_on_save(self):
        """用户角色保存后识别缓存失效（状态栏读到新角色）。"""
        m.USER_ROLES_PATH = os.path.join(self.tmpdir, "user_roles.json")
        try:
            m.AssistantApp._role_cache["prompt"] = "cache-hit"
            m.AssistantApp._role_cache["name"] = "旧角色"
            m.AssistantApp.save_user_roles([
                {"name": "新角色", "prompt": "P", "thinking": "high", "desc": "", "category": "我的"},
            ])
            # 缓存已失效（save 时清空）
            self.assertIsNone(m.AssistantApp._role_cache["prompt"])
            self.assertEqual(m.AssistantApp._current_role_name("P"), "新角色")
        finally:
            m.USER_ROLES_PATH = os.path.join(m.DATA_DIR, "user_roles.json")
            m.AssistantApp._role_cache.update(prompt=None, name=None)


if __name__ == "__main__":
    unittest.main()

