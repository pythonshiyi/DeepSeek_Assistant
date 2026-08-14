# -*- coding: utf-8 -*-
"""DeepSeek 对话助手启动界面（Splash Screen）。

深海蓝渐变背景 + 蓝鲸徽标 + 波浪纹理 + 加载进度，契合 DeepSeek 品牌
设计理念（深海 / 蓝鲸 / 极简 / 未来感）。主窗口就绪后淡出销毁。

用法：
    splash = SplashScreen(root, version=VERSION)
    splash.show()          # 渲染并显示（主窗口构建期间保持静态画面）
    splash.fade_out()      # 主窗口就绪后调用：进度拉满并淡出
"""
import time
import tkinter as tk

FONT_FAMILY = "Microsoft YaHei UI"

# 调色板（DeepSeek 品牌蓝）
BG_TOP = "#061628"
BG_BOTTOM = "#0F3A72"
ACCENT = "#4D6BFE"
ACCENT_DEEP = "#6E8FFF"
ACCENT_LIGHT = "#A9BEFF"
WHALE_BODY = "#6E8FFF"
WHALE_BELLY = "#B7C9FF"
WAVE_1 = "#123B6E"
WAVE_2 = "#1B4F8F"
RING = "#2C5094"
TEXT_MAIN = "#FFFFFF"
TEXT_SUB = "#9DB4D9"
TEXT_LOAD = "#B8CBF2"
TEXT_VERSION = "#6E8FC8"
DOT_DARK = "#3E5C9E"

SPLASH_SIZE = (540, 340)
FADE_STEPS = 10
FADE_DELAY = 0.028


def _hex_to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def _vertical_gradient(w, h, c1, c2):
    """生成竖向渐变 PhotoImage（逐行插值）。"""
    img = tk.PhotoImage(width=w, height=h)
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        img.put(f"#{r:02x}{g:02x}{b:02x}", to=(0, y, w, y + 1))
    return img


class SplashScreen:
    def __init__(self, root, version="1.1.0",
                 title="鲸语 WhaleTalk",
                 subtitle="深海蓝鲸 · 专业桌面 AI 工作台",
                 size=SPLASH_SIZE):
        self.root = root
        self.version = version
        self.w, self.h = size
        self._dot = 0
        self._progress = 0.0
        self._fading = False
        self._destroyed = False
        self._bar_item = None  # _draw_loading 前可能被 _set_progress(1.0) 调用

        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{self.w}x{self.h}+{self._center_x()}+{self._center_y()}")
        win.configure(bg=BG_TOP)
        self.win = win

        self._canvas = tk.Canvas(
            win, width=self.w, height=self.h, highlightthickness=0, bd=0,
            bg=BG_TOP,
        )
        self._canvas.pack(fill="both", expand=True)

    def _center_x(self):
        try:
            sw = self.root.winfo_screenwidth()
            return max(0, (sw - self.w) // 2)
        except Exception:
            return 100

    def _center_y(self):
        try:
            sh = self.root.winfo_screenheight()
            return max(0, (sh - self.h) // 2 - 20)
        except Exception:
            return 80

    # ---------- 绘制 ----------
    def _draw_background(self):
        self._bg_img = _vertical_gradient(self.w, self.h, BG_TOP, BG_BOTTOM)
        self._canvas.create_image(0, 0, anchor="nw", image=self._bg_img)

    def _draw_decorations(self):
        c = self._canvas
        # 深海光点
        for x, y, r in (
            (72, 58, 3), (452, 84, 2), (486, 168, 3), (52, 212, 2),
            (470, 268, 2), (88, 292, 2), (512, 40, 2),
        ):
            c.create_oval(x - r, y - r, x + r, y + r, fill=DOT_DARK, outline="")
        # 底部波浪纹理
        c.create_line(
            2, 316, 90, 304, 180, 316, 270, 304, 360, 316, 450, 304, 538, 316,
            smooth=True, fill=WAVE_1, width=2,
        )
        c.create_line(
            2, 328, 120, 316, 240, 328, 360, 316, 480, 328, 538, 320,
            smooth=True, fill=WAVE_2, width=2,
        )

    def _draw_whale(self, cx=270, cy=100):
        c = self._canvas
        body = WHALE_BODY
        # 环绕光环
        c.create_oval(cx - 96, cy - 72, cx + 96, cy + 76, outline=RING, width=2)
        # 尾鳍（上/下两瓣）
        c.create_polygon(
            (212, 96), (148, 58), (144, 96), (208, 102),
            fill=body, outline="",
        )
        c.create_polygon(
            (212, 116), (148, 150), (144, 112), (208, 106),
            fill=body, outline="",
        )
        # 身体
        c.create_oval(cx - 70, cy - 30, cx + 60, cy + 30, fill=body, outline="")
        # 肚皮亮弧
        c.create_arc(
            cx - 64, cy - 26, cx + 56, cy + 34, start=18, extent=144,
            style="arc", outline=WHALE_BELLY, width=3,
        )
        # 背鳍
        c.create_polygon(
            (252, 78), (270, 50), (284, 78), fill=body, outline="",
        )
        # 水柱
        c.create_arc(292, 26, 336, 72, start=180, extent=90,
                     style="arc", outline=WHALE_BELLY, width=3)
        c.create_arc(314, 12, 352, 56, start=180, extent=80,
                     style="arc", outline=WHALE_BELLY, width=3)
        # 眼睛
        c.create_oval(302, 90, 310, 98, fill=BG_TOP, outline="")

    def _draw_texts(self):
        c = self._canvas
        c.create_text(
            self.w // 2, 178, text="鲸语", fill=TEXT_MAIN,
            font=(FONT_FAMILY, 17, "bold"),
        )
        c.create_text(
            self.w // 2, 204, text="WhaleTalk", fill=ACCENT_LIGHT,
            font=(FONT_FAMILY, 11, "bold"),
        )
        c.create_text(
            self.w // 2, 228, text="深海蓝鲸 · 专业桌面 AI 工作台",
            fill=TEXT_SUB, font=(FONT_FAMILY, 9),
        )
        c.create_text(
            self.w - 14, self.h - 12, text=f"v{self.version}",
            fill=TEXT_VERSION, font=(FONT_FAMILY, 8), anchor="e",
        )

    def _draw_loading(self):
        c = self._canvas
        self._load_text_item = c.create_text(
            self.w // 2, 268, text="正在启动鲸语引擎", fill=TEXT_LOAD,
            font=(FONT_FAMILY, 9),
        )
        self._dots_item = c.create_text(
            self.w // 2 + 110, 268, text="", fill=TEXT_LOAD,
            font=(FONT_FAMILY, 9), anchor="w",
        )
        # 进度条轨道
        bx0, by0, bx1, by1 = 170, 284, 370, 287
        c.create_rectangle(bx0, by0, bx1, by1, fill=WAVE_1, outline="")
        self._bar_item = c.create_rectangle(
            bx0, by0, bx0, by1, fill=ACCENT, outline="",
        )

    def _set_progress(self, frac):
        try:
            if self._bar_item is None:
                return
            bx0, by0, bx1, by1 = 170, 284, 370, 287
            w = bx0 + int((bx1 - bx0) * max(0.0, min(1.0, frac)))
            self._canvas.coords(self._bar_item, bx0, by0, w, by1)
        except tk.TclError:
            pass

    # ---------- 对外接口 ----------
    def show(self):
        """渲染全部内容并显示（同步刷一帧）。"""
        self._draw_background()
        self._draw_decorations()
        self._draw_whale()
        self._draw_texts()
        self._draw_loading()
        self._set_progress(0.0)
        try:
            self.win.update()
        except tk.TclError:
            pass
        self.win.after(90, self._tick)

    def _tick(self):
        if self._destroyed or self._fading:
            return
        self._dot = (self._dot + 1) % 4
        try:
            self._canvas.itemconfig(
                self._dots_item, text="." * self._dot
            )
            if self._progress < 0.9:
                self._progress = min(0.9, self._progress + 0.012)
                self._set_progress(self._progress)
        except tk.TclError:
            return
        self.win.after(90, self._tick)

    def fade_out(self):
        """进度拉满后逐帧降低透明度，最后销毁窗口。"""
        if self._destroyed or self._fading:
            return
        self._fading = True
        try:
            self._set_progress(1.0)
            self.win.update()
            for i in range(FADE_STEPS, -1, -1):
                try:
                    self.win.attributes("-alpha", i / FADE_STEPS)
                except tk.TclError:
                    break
                self.win.update()
                time.sleep(FADE_DELAY)
        finally:
            self._destroyed = True
            try:
                self.win.destroy()
            except tk.TclError:
                pass
