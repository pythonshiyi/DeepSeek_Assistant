# -*- coding: utf-8 -*-
"""任务执行面板：Agent 工具链执行过程的实时可视化悬浮窗。

- 右下角置顶小窗，任务开始时出现，结束后自动收起。
- 所有更新必须发生在主线程（由 _drain_ui_queue 调用），面板内部不做线程操作。
- 显示：当前工具 + 计时、已执行数、最近完成结果（5 条）、产物计数、停止/收起按钮。
"""
import time
import tkinter as tk

from shared import PATH_RE

FONT_FAMILY = "Microsoft YaHei UI"

PANEL_W = 400
PANEL_H = 250
MAX_RECENT = 5
AUTO_HIDE_MS = 3000


class TaskPanel:
    def __init__(self, root, on_stop=None, theme=None):
        self.root = root
        self.on_stop = on_stop
        self.theme = theme or {}
        self._count = 0
        self._ok = 0
        self._fail = 0
        self._outputs = 0
        self._started = False
        self._started_at = 0.0
        self._tick_after = None
        self._hidden = True
        self._built = False
        self._hide_after = None
        self._destroyed = False

    # ---------- 构建 ----------
    def _ensure_built(self):
        if self._built:
            return
        t = self.theme
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=t.get("panel", "#ffffff"))
        self.win = win

        head = tk.Frame(win, bg=t.get("panel", "#ffffff"))
        head.pack(fill="x", padx=10, pady=(8, 2))
        self.title_lbl = tk.Label(
            head, text="🤖 任务执行中", bg=t.get("panel", "#ffffff"),
            fg=t.get("accent", "#3478f6"), font=(FONT_FAMILY, 10, "bold"),
        )
        self.title_lbl.pack(side="left")
        self.elapsed_lbl = tk.Label(
            head, text="0.0s", bg=t.get("panel", "#ffffff"),
            fg=t.get("text_sec", "#8a9099"), font=(FONT_FAMILY, 9),
        )
        self.elapsed_lbl.pack(side="right")

        self.current_lbl = tk.Label(
            win, text="准备中…", anchor="w",
            bg=t.get("surface", "#eef0f3"), fg=t.get("text", "#1d1f24"),
            font=(FONT_FAMILY, 9), padx=8, pady=6,
        )
        self.current_lbl.pack(fill="x", padx=10, pady=(4, 2))

        self.count_lbl = tk.Label(
            win, text="已执行 0 个工具", anchor="w",
            bg=t.get("panel", "#ffffff"), fg=t.get("text_sec", "#8a9099"),
            font=(FONT_FAMILY, 9),
        )
        self.count_lbl.pack(fill="x", padx=10, pady=(2, 0))

        self.recent_lbl = tk.Label(
            win, text="", anchor="nw", justify="left",
            bg=t.get("panel", "#ffffff"), fg=t.get("text_sec", "#8a9099"),
            font=(FONT_FAMILY, 9), wraplength=PANEL_W - 30,
        )
        self.recent_lbl.pack(fill="both", expand=True, padx=10, pady=(4, 2))

        bar = tk.Frame(win, bg=t.get("panel", "#ffffff"))
        bar.pack(fill="x", padx=10, pady=(2, 8))
        self.btn_stop = tk.Button(
            bar, text="■ 停止", command=self._on_stop,
            bg=t.get("page", "#f5f6f8"), fg=t.get("error", "#ff3b30"),
            activebackground=t.get("surface", "#eef0f3"),
            activeforeground=t.get("error", "#ff3b30"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=12, pady=3,
        )
        self.btn_stop.pack(side="left")
        self.btn_hide = tk.Button(
            bar, text="收起", command=self.hide,
            bg=t.get("page", "#f5f6f8"), fg=t.get("text", "#1d1f24"),
            activebackground=t.get("surface", "#eef0f3"),
            activeforeground=t.get("text", "#1d1f24"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=12, pady=3,
        )
        self.btn_hide.pack(side="right")

        # 支持拖动
        for w in (head, self.title_lbl):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        self._drag_x = None
        self._drag_y = None

        self._built = True

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.win.winfo_x()
        self._drag_y = event.y_root - self.win.winfo_y()

    def _drag_move(self, event):
        try:
            self.win.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")
        except Exception:
            pass

    def _on_stop(self):
        if self.on_stop:
            self.on_stop()

    # ---------- 位置 ----------
    def _place(self):
        try:
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
            if rw > 100 and rh > 100:
                x = rx + rw - PANEL_W - 16
                y = ry + rh - PANEL_H - 60
            else:
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                x = sw - PANEL_W - 16
                y = sh - PANEL_H - 60
            self.win.geometry(f"{PANEL_W}x{PANEL_H}+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    # ---------- 主题 ----------
    def apply_theme(self, theme):
        """主题切换后重刷面板颜色（控件已构建时调用）。"""
        self.theme = theme or {}
        if not self._built:
            return
        t = self.theme
        try:
            self.win.configure(bg=t.get("panel", "#ffffff"))
            self.title_lbl.configure(
                bg=t.get("panel", "#ffffff"), fg=t.get("accent", "#3478f6")
            )
            self.elapsed_lbl.configure(
                bg=t.get("panel", "#ffffff"), fg=t.get("text_sec", "#8a9099")
            )
            self.current_lbl.configure(
                bg=t.get("surface", "#eef0f3"), fg=t.get("text", "#1d1f24")
            )
            self.count_lbl.configure(
                bg=t.get("panel", "#ffffff"), fg=t.get("text_sec", "#8a9099")
            )
            self.recent_lbl.configure(
                bg=t.get("panel", "#ffffff"), fg=t.get("text_sec", "#8a9099")
            )
            self.btn_stop.configure(
                bg=t.get("page", "#f5f6f8"), fg=t.get("error", "#ff3b30"),
                activebackground=t.get("surface", "#eef0f3"),
                activeforeground=t.get("error", "#ff3b30"),
            )
            self.btn_hide.configure(
                bg=t.get("page", "#f5f6f8"), fg=t.get("text", "#1d1f24"),
                activebackground=t.get("surface", "#eef0f3"),
                activeforeground=t.get("text", "#1d1f24"),
            )
        except tk.TclError:
            pass

    # ---------- 对外接口（均须主线程调用） ----------
    def prepare(self):
        """任务开始前的准备：重置计数与计时，但不显示。

        面板在第一个工具调用（add_tool）时才真正弹出——纯对话任务全程无感。
        """
        self._count = 0
        self._ok = 0
        self._fail = 0
        self._outputs = 0
        self._started = False
        self._started_at = 0.0
        self._cancel_hide()
        self._stop_tick()
        if self._built:
            self.recent_lbl.configure(text="")

    def begin(self):
        if self._destroyed:
            return  # 主程序已关闭：队列中残留的工具事件不能再碰已销毁的窗口
        self._ensure_built()
        self._count = 0
        self._ok = 0
        self._fail = 0
        self._outputs = 0
        self._started = True
        self._started_at = time.monotonic()
        self._cancel_hide()
        self._place()
        self.win.deiconify()
        self._hidden = False
        self._refresh()
        self._start_tick()

    def add_tool(self, name, result, duration=None):
        if self._destroyed:
            return
        if not self._started:
            self.begin()  # 首个工具调用时懒启动（此前处于隐藏准备态）
        if self._destroyed:
            return
        self._ensure_built()
        self._count += 1
        try:
            import deepseek_client as dc

            fail_prefixes = dc.TOOL_RESULT_FAIL_PREFIXES
        except Exception:
            fail_prefixes = ("错误", "权限拒绝", "超时")
        failed = str(result or "").startswith(fail_prefixes)
        if failed:
            self._fail += 1
        else:
            self._ok += 1
        first = str(result or "").splitlines()[0] if result else ""
        mark = "❌" if failed else "✅"
        dur = f" · {duration:.1f}s" if duration is not None else ""
        self._outputs += _count_paths(str(result))
        self._refresh(f"{mark} {name}{dur} {first[:50]}")

    def finish(self, summary=None):
        self._stop_tick()
        if not self._started:
            # 从未显示（纯对话无工具）：直接复位，不弹窗不调度
            self._count = 0
            self._ok = 0
            self._fail = 0
            self._outputs = 0
            return
        if self._built:
            self._cancel_hide()
            self._hide_after = self.root.after(AUTO_HIDE_MS, self.hide)
            if summary:
                try:
                    self.title_lbl.configure(text=summary[:24])
                except Exception:
                    pass
            self._refresh()
        else:
            self._hidden = True

    def hide(self):
        if self._built and not self._destroyed:
            try:
                self.win.withdraw()
            except tk.TclError:
                pass
        self._hidden = True
        self._stop_tick()

    def destroy(self):
        self._destroyed = True
        self._stop_tick()
        self._cancel_hide()
        if self._built:
            try:
                self.win.destroy()
            except tk.TclError:
                pass

    def is_visible(self):
        return self._built and not self._hidden

    # ---------- 内部 ----------
    def _refresh(self, latest=None):
        if not self._built or self._hidden:
            return
        try:
            self.elapsed_lbl.configure(text=f"{self._elapsed():.1f}s")
            self.count_lbl.configure(
                text=f"已执行 {self._count} 个工具（✅ {self._ok} / ❌ {self._fail}）"
                f" · 产物 {self._outputs} 个文件"
            )
            if latest:
                old = self.recent_lbl.cget("text")
                lines = ([latest] + (old.splitlines() if old else []))[:MAX_RECENT]
                self.recent_lbl.configure(text="\n".join(lines))
        except tk.TclError:
            pass

    def _elapsed(self):
        if not self._started_at:
            return 0.0
        return time.monotonic() - self._started_at

    def _start_tick(self):
        self._stop_tick()
        self._tick_after = self.root.after(300, self._tick)

    def _tick(self):
        self._tick_after = None
        if self._hidden or self._destroyed:
            return
        try:
            self.elapsed_lbl.configure(text=f"{self._elapsed():.1f}s")
        except tk.TclError:
            return
        self._tick_after = self.root.after(300, self._tick)

    def _stop_tick(self):
        if self._tick_after is not None:
            try:
                self.root.after_cancel(self._tick_after)
            except Exception:
                pass
            self._tick_after = None

    def _cancel_hide(self):
        if self._hide_after is not None:
            try:
                self.root.after_cancel(self._hide_after)
            except Exception:
                pass
            self._hide_after = None


class InlineTaskPanel:
    """聊天区右上角内嵌任务进度条（替代左下角悬浮窗）。

    单行紧凑设计：任务开始时出现在会话信息条下方，工具执行时更新，
    完成后 4 秒淡出。高度仅 ~28px，不遮挡会话信息条。
    """

    def __init__(self, master, theme=None, on_detail=None):
        self.master = master
        self.on_detail = on_detail
        self.theme = theme or {}
        t = self.theme
        self.frame = tk.Frame(
            master, bg=t.get("panel", "#ffffff"),
            highlightthickness=1, bd=0,
            highlightbackground=t.get("border", "#e3e5e9"),
            highlightcolor=t.get("accent", "#3478f6"),
        )
        # 定位在会话信息条（header，约 42px）下方：不遮挡右上角信息
        self.frame.place(relx=1.0, x=-12, y=48, anchor="ne")
        self.title_lbl = tk.Label(
            self.frame, text="🤖 任务执行中", bg=t.get("panel", "#ffffff"),
            fg=t.get("accent", "#3478f6"), font=(FONT_FAMILY, 9, "bold"),
        )
        self.title_lbl.pack(side="left", padx=(10, 8), pady=5)
        self.status_lbl = tk.Label(
            self.frame, text="", bg=t.get("panel", "#ffffff"),
            fg=t.get("text_sec", "#8a9099"), font=(FONT_FAMILY, 9),
            anchor="w", justify="left",
        )
        self.status_lbl.pack(side="left", padx=(0, 12), pady=5)
        self.detail_btn = tk.Label(
            self.frame, text="详情", bg=t.get("panel", "#ffffff"),
            fg=t.get("accent", "#3478f6"), font=(FONT_FAMILY, 8, "bold"),
            cursor="hand2", padx=4, pady=2,
        )
        self.detail_btn.pack(side="left", padx=(0, 6), pady=5)
        self.detail_btn.bind("<Button-1>", lambda e: self._on_detail())
        self._count = 0
        self._started_at = 0.0
        self._hide_after = None
        self.frame.place_forget()

    def prepare(self):
        self._count = 0
        self._started_at = time.monotonic()
        try:
            self.title_lbl.configure(text="🤖 任务执行中")
            self.status_lbl.configure(text="准备工具…")
            self.frame.place(relx=1.0, x=-12, y=48, anchor="ne")
            self.frame.lift()
        except tk.TclError:
            pass

    def add_tool(self, name, result):
        self._count += 1
        ok = not str(result or "").startswith(("错误", "权限拒绝", "超时"))
        mark = "✅" if ok else "⚠"
        try:
            self.status_lbl.configure(
                text=f"{mark} {name} · 第 {self._count} 个 · {self._elapsed():.0f}s"
            )
            self.frame.place(relx=1.0, x=-12, y=48, anchor="ne")
            self.frame.lift()
        except tk.TclError:
            pass

    def finish(self, summary=""):
        try:
            self.title_lbl.configure(text="🤖 任务完成")
            self.status_lbl.configure(text=str(summary or "")[:120])
            self.frame.place(relx=1.0, x=-12, y=48, anchor="ne")
            self.frame.lift()
        except tk.TclError:
            pass
        self._cancel_hide()
        self._hide_after = self.master.after(4000, self._hide)

    def _on_detail(self):
        try:
            if self.on_detail:
                self.on_detail()
        except Exception:
            pass

    def _elapsed(self):
        return max(0.0, time.monotonic() - self._started_at)

    def _hide(self):
        self._hide_after = None
        try:
            self.frame.place_forget()
        except tk.TclError:
            pass

    def _cancel_hide(self):
        if self._hide_after is not None:
            try:
                self.master.after_cancel(self._hide_after)
            except Exception:
                pass
            self._hide_after = None

    def apply_theme(self, t):
        self.theme = t or {}
        try:
            self.frame.configure(
                bg=t.get("panel", "#ffffff"),
                highlightbackground=t.get("border", "#e3e5e9"),
                highlightcolor=t.get("accent", "#3478f6"),
            )
            self.title_lbl.configure(bg=t.get("panel", "#ffffff"), fg=t.get("accent", "#3478f6"))
            self.status_lbl.configure(bg=t.get("panel", "#ffffff"), fg=t.get("text_sec", "#8a9099"))
            self.detail_btn.configure(bg=t.get("panel", "#ffffff"), fg=t.get("accent", "#3478f6"))
        except tk.TclError:
            pass

    def destroy(self):
        self._cancel_hide()
        try:
            self.frame.destroy()
        except tk.TclError:
            pass


def _count_paths(text):
    try:
        return sum(1 for _ in PATH_RE.finditer(text or ""))
    except Exception:
        return 0
