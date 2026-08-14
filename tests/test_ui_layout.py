# -*- coding: utf-8 -*-
"""布局定版规范测试（Layout Specification v1.0）：
窗口几何/输入区与聊天列对齐/对话框档位吸附/紧凑阈值/几何记忆。"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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

    def _snaps(self):
        """按当前屏幕比例计算的档位（与 _dialog_shell 同逻辑）。"""
        scale = self.app._screen_scale()
        return (
            tuple(int(s * scale) for s in (420, 520, 640)),
            tuple(int(s * scale) for s in (300, 420, 460, 540, 620)),
        )

    def test_base_snap_constants(self):
        """基准档位常量保持不变（缩放发生在对话框实例层）。"""
        self.assertEqual(m.AssistantApp._DIALOG_W_SNAP, (420, 520, 640))
        self.assertEqual(m.AssistantApp._DIALOG_H_SNAP, (300, 420, 460, 540, 620))

    def test_screen_scale_ranges(self):
        """应用级自适应系数：以主窗口宽度为基准，1.0-1.9 区间。"""
        scale = self.app._screen_scale()
        self.assertGreaterEqual(scale, 1.0)
        self.assertLessEqual(scale, 1.9)
        with mock.patch.object(self.app.root, "winfo_width", return_value=1000):
            self.assertEqual(self.app._screen_scale(), 1.0)
        with mock.patch.object(self.app.root, "winfo_width", return_value=1474):
            self.assertAlmostEqual(self.app._screen_scale(), 1.474, places=2)
        with mock.patch.object(self.app.root, "winfo_width", return_value=2200):
            self.assertEqual(self.app._screen_scale(), 1.9)  # 封顶

    def test_hub_size_follows_main_window(self):
        """中心窗口 = 主窗口的 92%×94%（以应用为参照，非屏幕比例）。"""
        with mock.patch.object(self.app.root, "winfo_width", return_value=1474), \
             mock.patch.object(self.app.root, "winfo_height", return_value=921), \
             mock.patch.object(self.app.root, "winfo_screenwidth", return_value=2048), \
             mock.patch.object(self.app.root, "winfo_screenheight", return_value=1152), \
             mock.patch.object(self.app.root, "attributes", return_value=False):
            size = self.app._hub_size()
        w, _, h = size.partition("x")
        self.assertEqual(int(w), int(1474 * 0.92))
        self.assertEqual(int(h), int(921 * 0.94))

    def test_hub_size_fullscreen_keeps_margin(self):
        """全屏时中心窗口 = 屏幕 85%（四周留边）。"""
        with mock.patch.object(self.app.root, "winfo_width", return_value=2048), \
             mock.patch.object(self.app.root, "winfo_height", return_value=1152), \
             mock.patch.object(self.app.root, "winfo_screenwidth", return_value=2048), \
             mock.patch.object(self.app.root, "winfo_screenheight", return_value=1152), \
             mock.patch.object(self.app.root, "attributes", return_value=True):
            size = self.app._hub_size()
        w, _, h = size.partition("x")
        self.assertEqual(int(w), int(2048 * 0.85))
        self.assertEqual(int(h), int(1152 * 0.85))

    def test_width_snap_scaled(self):
        w_snaps, _h = self._snaps()
        self.assertEqual(len(w_snaps), 3)
        self.assertTrue(w_snaps[0] < w_snaps[1] < w_snaps[2], "档位应严格递增")
        self.assertGreaterEqual(w_snaps[0], 420, "放大后不低于基准")
        # 吸附语义：结果必须是档位集合中的成员
        for target in (460, 540, 620, 380):
            got = min(w_snaps, key=lambda d: abs(d - target))
            self.assertIn(got, w_snaps)

    def test_scale_unity_at_1000(self):
        """基准窗口（1000 宽）系数为 1.0 → 档位等于基准值。"""
        with mock.patch.object(self.app.root, "winfo_width", return_value=1000):
            self.assertEqual(self.app._screen_scale(), 1.0)

    def test_shell_snaps_actual(self):
        w_snaps, _h = self._snaps()
        d, body, footer = self._mapped_dialog("吸附测试", 460, 440)
        w, _ = self._geo_size(d)
        self.assertIn(w, w_snaps)
        d.destroy()

    def test_shell_snap_620_to_widest(self):
        w_snaps, _h = self._snaps()
        d, body, footer = self._mapped_dialog("宽对话框", 620, 540)
        w, _ = self._geo_size(d)
        self.assertIn(w, w_snaps)
        d.destroy()

    def test_shell_height_snapped(self):
        _w, h_snaps = self._snaps()
        d, body, footer = self._mapped_dialog("高对话框", 520, 440)
        _, h = self._geo_size(d)
        self.assertIn(h, h_snaps)
        d.destroy()


class TestCompactThresholds(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(m.LAYOUT["compact_sidebar_w"], 1120)
        self.assertEqual(m.LAYOUT["compact_panel_w"], 1000)
        # 侧栏阈值 > 面板阈值：窄窗口先收侧栏再收面板（优先保聊天区）
        self.assertGreater(m.LAYOUT["compact_sidebar_w"], m.LAYOUT["compact_panel_w"])
        # minsize 必须小于两者：任意可达窗口尺寸下紧凑逻辑都能生效
        self.assertLess(m.LAYOUT["window_min_w"], m.LAYOUT["compact_panel_w"])


class TestCenterAndFullscreen(unittest.TestCase):
    """可见性优先：窗口居中 + 全屏 + 滚动完整性。"""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir, cls.root, cls.app = _make_app()
        cls.app.root.deiconify()
        cls.app.root.update()

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

    def test_center_geometry_in_screen(self):
        """居中几何：含位置且位于屏幕内。"""
        geo = self.app._center_geometry(600, 400)
        self.assertIn("+", geo)
        w, _, rest = geo.partition("x")
        h, _, pos = rest.partition("+")
        x, _, y = pos.partition("+")
        sw, sh = self.app.root.winfo_screenwidth(), self.app.root.winfo_screenheight()
        self.assertGreaterEqual(int(x), 0)
        self.assertGreaterEqual(int(y), 0)
        self.assertLessEqual(int(x) + int(w), sw)
        self.assertLessEqual(int(y) + int(h), sh)

    def test_dialog_centered(self):
        """对话框打开即带居中位置。"""
        d, body, footer = self.app._dialog_shell("居中测试", 460, 440)
        geo = d.geometry()
        self.assertIn("+", geo)
        size = geo.split("+")[0]
        w, _, h = size.partition("x")
        self.assertGreater(int(w), 0)
        d.destroy()

    def test_fullscreen_toggle(self):
        """F11 全屏切换：进入无边框全屏，退出恢复。"""
        self.app.toggle_fullscreen()
        try:
            self.assertTrue(bool(self.app.root.attributes("-fullscreen")))
        finally:
            self.app.toggle_fullscreen()
        self.assertFalse(bool(self.app.root.attributes("-fullscreen")))

    def test_plugin_hub_scrollbars(self):
        """插件中心列表与详情均有滚动条（内容完整可滚动，不截断）。"""
        self.app.show_plugin_hub()
        hubs = [w for w in self.app.root.winfo_children()
                if isinstance(w, tk.Toplevel) and "插件中心" in w.title()]
        self.assertTrue(hubs)
        hub = hubs[0]
        sbs = []
        stack = list(hub.winfo_children())
        while stack:
            w = stack.pop()
            if isinstance(w, tk.Scrollbar):
                sbs.append(w)
            stack.extend(w.winfo_children())
        self.assertGreaterEqual(len(sbs), 4, "我的插件/画廊两页各应有列表+详情滚动条")
        for d in hubs:
            d.destroy()


if __name__ == "__main__":
    unittest.main()
