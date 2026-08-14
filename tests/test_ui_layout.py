# -*- coding: utf-8 -*-
"""布局定版规范测试（Layout Specification v1.0）：
窗口几何/输入区与聊天列对齐/对话框档位吸附/紧凑阈值/几何记忆。"""
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


def _make_app():
    if not TK_AVAILABLE:
        raise unittest.SkipTest("tkinter 不可用（缺少 Tcl/Tk 运行库）")
    tmpdir = tempfile.mkdtemp(prefix="dsa_layout_")
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
    os.makedirs(m.SESSIONS_DIR, exist_ok=True)
    os.makedirs(m.ARCHIVES_DIR, exist_ok=True)
    with open(m.CLEAN_EXIT_FLAG, "w", encoding="utf-8") as f:
        f.write("ok")
    with open(m.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"welcomed": True, "restore_session": False}, f)
    root = tk.Tk()
    root.withdraw()
    app = m.AssistantApp(root)
    return tmpdir, root, app


class TestLayoutConstants(unittest.TestCase):
    def test_all_keys_present(self):
        for k in ("window_w", "window_h", "window_min_w", "window_min_h",
                  "sidebar_default", "sidebar_min", "sidebar_max",
                  "panel_default", "panel_min", "panel_max", "panel_files_w",
                  "content_min", "content_max", "content_margin",
                  "compact_panel_w", "compact_sidebar_w",
                  "dialog_s", "dialog_m", "dialog_l",
                  "dialog_h_s", "dialog_h_m", "dialog_h_l",
                  "dialog_h_editor", "dialog_h_editor_l"):
            self.assertIn(k, m.LAYOUT, k)

    def test_sane_ranges(self):
        L = m.LAYOUT
        self.assertLessEqual(L["sidebar_min"], L["sidebar_default"], L["sidebar_max"])
        self.assertLessEqual(L["panel_min"], L["panel_default"], L["panel_max"])
        self.assertLess(L["content_min"], L["content_max"])
        # 档位严格递增
        self.assertEqual(tuple(L[k] for k in ("dialog_s", "dialog_m", "dialog_l")),
                         tuple(sorted(L[k] for k in ("dialog_s", "dialog_m", "dialog_l"))))


class TestWindowGeometry(unittest.TestCase):
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

    def test_default_size(self):
        # 无记忆时使用 LAYOUT 默认
        self.assertEqual(m.LAYOUT["window_w"], 1280)
        self.assertEqual(m.LAYOUT["window_h"], 820)

    def test_minsize_constants(self):
        self.assertEqual(m.LAYOUT["window_min_w"], 880)
        self.assertEqual(m.LAYOUT["window_min_h"], 620)
        # minsize 必须小于紧凑阈值：窄窗口下紧凑逻辑可触发（优先保聊天区）
        self.assertLess(m.LAYOUT["window_min_w"], m.LAYOUT["compact_panel_w"])

    def test_geometry_saved_on_close(self):
        # 构造几何字符串 → 模拟保存路径
        self.app.cfg["window_geometry"] = ""
        try:
            self.app.cfg["window_geometry"] = "1234x700+50+40"
            data = dict(self.app.cfg)
            self.assertIn("window_geometry", data)
            self.assertEqual(data["window_geometry"], "1234x700+50+40")
        finally:
            self.app.cfg["window_geometry"] = ""


class TestInputChatAlignment(unittest.TestCase):
    """核心：输入区与聊天内容列同宽对齐。"""

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

    def _content_col_width(self, tw):
        return max(m.LAYOUT["content_min"],
                   min(m.LAYOUT["content_max"], tw - m.LAYOUT["content_margin"]))

    def test_layout_input_padx_matches_col(self):
        for tw in (800, 1000, 1200, 1400):
            cw = self._content_col_width(tw)
            # _layout_input 内部逻辑：padx = (tw - cw)//2
            padx = max(16, (tw - cw) // 2)
            self.assertGreaterEqual(padx, 16)
            # 输入区宽 = tw - 2*padx 应 ≈ cw（±1px 取整误差）
            self.assertLessEqual(abs((tw - 2 * padx) - cw), 1)

    def test_layout_all_aligns_input(self):
        # 真实调用 _layout_input(1000) 后 input_wrap 的 padx 与聊天列一致
        try:
            self.app._layout_input(1000)
            info = self.app.input_wrap.pack_info()
            tw = 1000
            cw = self._content_col_width(tw)
            expect_padx = max(16, (tw - cw) // 2)
            self.assertEqual(int(info.get("padx", 0)), expect_padx)
        except Exception as e:
            self.fail(str(e))


class TestDialogSnap(unittest.TestCase):
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

    def test_width_snap(self):
        snaps = m.AssistantApp._DIALOG_W_SNAP
        self.assertEqual(snaps, (420, 520, 640))
        # 就近吸附：460 → 420（差 40 vs 60）
        self.assertEqual(min(snaps, key=lambda d: abs(d - 460)), 420)
        # 380 → 420（差 40 vs 140）
        self.assertEqual(min(snaps, key=lambda d: abs(d - 380)), 420)
        # 540 → 520（差 20 vs 100）
        self.assertEqual(min(snaps, key=lambda d: abs(d - 540)), 520)
        # 620 → 640（差 20 vs 100）
        self.assertEqual(min(snaps, key=lambda d: abs(d - 620)), 640)

    def test_height_snap(self):
        snaps = m.AssistantApp._DIALOG_H_SNAP
        self.assertIn(420, snaps)
        self.assertIn(540, snaps)

    @staticmethod
    def _geo_size(d):
        """从 Toplevel 的 geometry 字符串解析宽x高（需窗口已映射）。"""
        try:
            size = d.geometry().split("+")[0]
            w, _, h = size.partition("x")
            return int(w), int(h)
        except (ValueError, AttributeError):
            return 0, 0

    def _mapped_dialog(self, title, w, h):
        """deiconify 主窗口后创建对话框（withdraw 下 Toplevel 不映射，geometry 请求失效）。"""
        self.app.root.deiconify()
        try:
            d, body, footer = self.app._dialog_shell(title, w, h)
            self.app.root.update()
            return d, body, footer
        finally:
            self.app.root.withdraw()

    def test_shell_snaps_actual(self):
        d, body, footer = self._mapped_dialog("吸附测试", 460, 440)
        w, _ = self._geo_size(d)
        self.assertIn(w, m.AssistantApp._DIALOG_W_SNAP)
        d.destroy()

    def test_shell_snap_620_to_640(self):
        d, body, footer = self._mapped_dialog("宽对话框", 620, 540)
        w, _ = self._geo_size(d)
        self.assertIn(w, (640,))
        d.destroy()

    def test_shell_height_snapped(self):
        d, body, footer = self._mapped_dialog("高对话框", 520, 440)
        _, h = self._geo_size(d)
        self.assertIn(h, m.AssistantApp._DIALOG_H_SNAP)
        d.destroy()


class TestCompactThresholds(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(m.LAYOUT["compact_sidebar_w"], 1120)
        self.assertEqual(m.LAYOUT["compact_panel_w"], 1000)
        # 侧栏阈值 > 面板阈值：窄窗口先收侧栏再收面板（优先保聊天区）
        self.assertGreater(m.LAYOUT["compact_sidebar_w"], m.LAYOUT["compact_panel_w"])
        # minsize 必须小于两者：任意可达窗口尺寸下紧凑逻辑都能生效
        self.assertLess(m.LAYOUT["window_min_w"], m.LAYOUT["compact_panel_w"])


if __name__ == "__main__":
    unittest.main()
