# -*- coding: utf-8 -*-
"""进程终端面板：后台进程（服务器/长驻任务）的实时输出终端。

- 独立置顶小窗，按需打开（工具菜单或 AI 启动进程时自动弹出）。
- 进程下拉切换、停止、清空、自动跟随滚动。
- 所有更新必须在主线程（队列驱动），面板内部不做线程操作。
"""
import logging
import tkinter as tk
import tkinter.font as tkfont

logger = logging.getLogger("whaletalk")

FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"

PANEL_W = 620
PANEL_H = 400
MAX_TEXT_LINES = 2000  # 与 _buf 上限对齐：当前进程可见文本也要裁剪，防无界增长
FOLD_IDLE_MS = 8000  # 无新输出 8s 后自动收起为小条


class ProcessPanel:
    def __init__(self, root, on_stop=None, theme=None, embed_in=None, width_cb=None):
        self.root = root
        self.embed_in = embed_in or root  # 内嵌容器（默认 root；传 chat_frame 只占聊天区右上角）
        self.width_cb = width_cb  # 返回内容列宽度（用于计算右侧空白，避免遮挡）
        self.on_stop = on_stop
        self.theme = theme or {}
        self._built = False
        self._hidden = True
        self._current = None
        self._buf = {}  # name -> [lines]
        self._dirty = set()  # 有输出但未选中的进程
        self._destroyed = False

    def _t(self, key, default=""):
        return self.theme.get(key, default)

    def _mk_status(self, parent):
        return tk.Label(
            parent, text="", bg=self._t("panel", "#ffffff"),
            fg=self._t("text_sec", "#8a9099"), font=(MONO_FAMILY, 8),
        )

    def _ensure_built(self):
        if self._built:
            return
        t = self.theme
        # 内嵌 Frame（非弹窗）：place 在聊天区右上角、任务进度条下方
        win = tk.Frame(self.embed_in, bg=t.get("panel", "#ffffff"),
                       highlightthickness=1, bd=0,
                       highlightbackground=t.get("border", "#e3e5e9"),
                       highlightcolor=t.get("accent", "#3478f6"))
        win.pack_propagate(False)
        self.win = win

        head = tk.Frame(win, bg=self._t("panel", "#ffffff"))
        head.pack(fill="x", padx=10, pady=(8, 4))
        self.head = head
        tk.Label(
            head, text="🤖 后台进程", bg=self._t("panel", "#ffffff"),
            fg=self._t("accent", "#3478f6"), font=(FONT_FAMILY, 10, "bold"),
        ).pack(side="left")
        self.combo = ttk_combobox(head)
        self.combo.pack(side="left", padx=(10, 6))
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._select(self.combo.get()))
        self.status_lbl = tk.Label(
            head, text="无进程", bg=self._t("panel", "#ffffff"),
            fg=self._t("text_sec", "#8a9099"), font=(FONT_FAMILY, 9),
        )
        self.status_lbl.pack(side="right")
        self.btn_close = tk.Button(
            head, text="✕", command=self.hide,
            bg=self._t("panel", "#ffffff"), fg=self._t("text_sec", "#8a9099"),
            activebackground=self._t("surface", "#eef0f3"),
            activeforeground=self._t("text", "#1d1f24"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=6, pady=0,
        )
        self.btn_close.pack(side="right", padx=(4, 0))
        self.btn_fold = tk.Button(
            head, text="—", command=self.fold,
            bg=self._t("panel", "#ffffff"), fg=self._t("text_sec", "#8a9099"),
            activebackground=self._t("surface", "#eef0f3"),
            activeforeground=self._t("text", "#1d1f24"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=6, pady=0,
        )
        self.btn_fold.pack(side="right", padx=(2, 0))

        # 进程信息行：pid · 启动时间 · 运行时长 · 状态徽章
        info = tk.Frame(win, bg=self._t("panel", "#ffffff"))
        info.pack(fill="x", padx=10, pady=(0, 2))
        self.info_bar = info
        self.pid_lbl = tk.Label(
            info, text="", bg=self._t("panel", "#ffffff"),
            fg=self._t("text_sec", "#8a9099"), font=(MONO_FAMILY, 8),
        )
        self.pid_lbl.pack(side="left")
        self.badge_lbl = tk.Label(
            info, text="", bg=self._t("surface", "#eef0f3"),
            fg=self._t("success", "#12b878"), font=(FONT_FAMILY, 8, "bold"),
            padx=6, pady=1,
        )
        self.badge_lbl.pack(side="right")

        body = tk.Frame(win, bg=self._t("surface", "#eef0f3"))
        body.pack(fill="both", expand=True, padx=10, pady=(4, 6))
        self.body = body
        self.text = tk.Text(
            body, wrap="char", state="disabled",
            bg=self._t("code_bg", "#f2f4f7"), fg=self._t("code_fg", "#24292f"),
            relief="flat", highlightthickness=1,
            highlightbackground=self._t("border", "#e3e5e9"),
            font=(MONO_FAMILY, 9), padx=8, pady=6,
            undo=False,
        )
        # 终端着色 tag：时间戳 / 状态 / 错误
        self.text.tag_configure("ts", foreground=self._t("text_sec", "#8a9099"))
        self.text.tag_configure("meta", foreground=self._t("accent", "#3478f6"))
        self.text.tag_configure("err", foreground=self._t("error", "#ff3b30"))
        self.text.tag_configure("ok", foreground=self._t("success", "#12b878"))
        sb = tk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        bar = tk.Frame(win, bg=self._t("panel", "#ffffff"))
        bar.pack(fill="x", padx=10, pady=(0, 10))
        self.bar = bar
        self.btn_stop = tk.Button(
            bar, text="■ 停止", command=self._stop_current,
            bg=self._t("page", "#f5f6f8"), fg=self._t("error", "#ff3b30"),
            activebackground=self._t("surface", "#eef0f3"),
            activeforeground=self._t("error", "#ff3b30"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=10, pady=3,
        )
        self.btn_stop.pack(side="left")
        tk.Button(
            bar, text="清空", command=self._clear_current,
            bg=self._t("page", "#f5f6f8"), fg=self._t("text", "#1d1f24"),
            activebackground=self._t("surface", "#eef0f3"),
            activeforeground=self._t("text", "#1d1f24"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=10, pady=3,
        ).pack(side="left", padx=(6, 0))
        self.line_count_lbl = self._mk_status(bar)
        self.line_count_lbl.pack(side="left", padx=(10, 0))
        self._follow_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            bar, text="自动跟随", variable=self._follow_var,
            bg=self._t("panel", "#ffffff"), fg=self._t("text", "#1d1f24"),
            activebackground=self._t("panel", "#ffffff"),
            font=(FONT_FAMILY, 9),
        ).pack(side="right")

        # 折叠小条：标题 + 状态 + 展开/关闭（替代完整面板，节省空间）
        bar_fold = tk.Frame(win, bg=self._t("panel", "#ffffff"))
        self.fold_bar = bar_fold
        tk.Label(
            bar_fold, text="🤖 后台进程", bg=self._t("panel", "#ffffff"),
            fg=self._t("accent", "#3478f6"), font=(FONT_FAMILY, 9, "bold"),
        ).pack(side="left", padx=(10, 6), pady=6)
        self.fold_status_lbl = tk.Label(
            bar_fold, text="无进程", bg=self._t("panel", "#ffffff"),
            fg=self._t("text_sec", "#8a9099"), font=(FONT_FAMILY, 9),
        )
        self.fold_status_lbl.pack(side="left", pady=6)
        self.btn_fold_open = tk.Button(
            bar_fold, text="展开", command=self.unfold,
            bg=self._t("page", "#f5f6f8"), fg=self._t("accent", "#3478f6"),
            activebackground=self._t("surface", "#eef0f3"),
            activeforeground=self._t("accent", "#3478f6"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=8, pady=2,
        )
        self.btn_fold_open.pack(side="right", padx=(4, 8), pady=3)
        self.btn_fold_close = tk.Button(
            bar_fold, text="✕", command=self.hide,
            bg=self._t("panel", "#ffffff"), fg=self._t("text_sec", "#8a9099"),
            activebackground=self._t("surface", "#eef0f3"),
            activeforeground=self._t("text", "#1d1f24"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=6, pady=2,
        )
        self.btn_fold_close.pack(side="right", pady=3)
        bar_fold.pack_forget()  # 默认显示完整面板

        self._folded = False
        self._fold_timer = None
        self._built = True

    # ---------- 主题 ----------
    def apply_theme(self, theme):
        """主题切换后重刷面板颜色（控件已构建时调用）。"""
        self.theme = theme or {}
        if not self._built:
            return
        t = self.theme
        try:
            self.win.configure(bg=t.get("panel", "#ffffff"))
            self.status_lbl.configure(
                bg=t.get("panel", "#ffffff"), fg=t.get("text_sec", "#8a9099")
            )
            self.pid_lbl.configure(
                bg=t.get("panel", "#ffffff"), fg=t.get("text_sec", "#8a9099")
            )
            self.badge_lbl.configure(
                bg=t.get("surface", "#eef0f3"),
                fg=t.get("success" if "运行" in self.badge_lbl.cget("text") else "error", "#12b878"),
            )
            self.line_count_lbl.configure(
                bg=t.get("panel", "#ffffff"), fg=t.get("text_sec", "#8a9099")
            )
            self.fold_status_lbl.configure(
                bg=t.get("panel", "#ffffff"), fg=t.get("text_sec", "#8a9099")
            )
            self.text.configure(
                bg=t.get("code_bg", "#f2f4f7"), fg=t.get("code_fg", "#24292f"),
                highlightbackground=t.get("border", "#e3e5e9"),
            )
            self.text.tag_configure("ts", foreground=t.get("text_sec", "#8a9099"))
            self.text.tag_configure("meta", foreground=t.get("accent", "#3478f6"))
            self.text.tag_configure("err", foreground=t.get("error", "#ff3b30"))
            self.text.tag_configure("ok", foreground=t.get("success", "#12b878"))
            self.btn_stop.configure(
                bg=t.get("page", "#f5f6f8"), fg=t.get("error", "#ff3b30"),
                activebackground=t.get("surface", "#eef0f3"),
                activeforeground=t.get("error", "#ff3b30"),
            )
        except tk.TclError:
            pass

    def _place_fixed(self):
        """内嵌定位：聊天区右上角，任务进度条（InlineTaskPanel y≈48,高≈38）下方。

        宽度动态适配：容器宽 - 内容列宽 - 边距，夹在 [300, 560]，只占右侧空白。
        """
        try:
            cw = self.embed_in.winfo_width()
            col_w = 0
            if self.width_cb is not None:
                try:
                    col_w = int(self.width_cb())
                except Exception:
                    col_w = 0
            blank = max(0, cw - col_w - 24)
            width = max(300, min(480, blank))
            # 任务条下方：y = 48 + 38 + 6
            self.win.place(relx=1.0, x=-12, y=92, anchor="ne", width=width, height=320)
            self.win.lift()
            self._text_max_w = max(150, width - 40)
        except tk.TclError:
            pass

    # ---------- 折叠 ----------
    def fold(self):
        """收起为单行小条（标题 + 状态 + 展开/✕）。"""
        if not self._built or self._destroyed or self._folded:
            return
        self._folded = True
        self._cancel_fold_timer()
        try:
            for w in (self.head, getattr(self, "info_bar", None), self.body, self.bar):
                if w is not None:
                    w.pack_forget()
        except tk.TclError:
            pass
        try:
            self.fold_status_lbl.configure(text=self.status_lbl.cget("text"))
        except tk.TclError:
            pass
        self.fold_bar.pack(fill="x", side="top")
        # 高度取折叠条实际请求高度（硬编码 32 会裁掉按钮导致无法点击展开）
        try:
            self.win.update_idletasks()
            h = self.fold_bar.winfo_reqheight() + 2
        except tk.TclError:
            h = 36
        self.win.place_configure(height=h)

    def unfold(self):
        """展开完整面板。"""
        if not self._built or self._destroyed or not self._folded:
            return
        self._folded = False
        try:
            self.fold_bar.pack_forget()
        except tk.TclError:
            pass
        try:
            self.head.pack(fill="x", padx=10, pady=(8, 4))
            if getattr(self, "info_bar", None) is not None:
                self.info_bar.pack(fill="x", padx=10, pady=(0, 2))
        except tk.TclError:
            pass
        self.body.pack(fill="both", expand=True, padx=10, pady=(4, 6))
        self.bar.pack(fill="x", padx=10, pady=(0, 10))
        self.win.place_configure(height=320)
        self._arm_fold_timer()

    def _arm_fold_timer(self):
        """无新输出 FOLD_IDLE_MS 后自动收起为小条。"""
        self._cancel_fold_timer()
        try:
            self._fold_timer = self.root.after(FOLD_IDLE_MS, self.fold)
        except tk.TclError:
            pass

    def _cancel_fold_timer(self):
        if getattr(self, "_fold_timer", None) is not None:
            try:
                self.root.after_cancel(self._fold_timer)
            except Exception:
                pass
            self._fold_timer = None

    # ---------- 对外接口（主线程） ----------
    def open(self):
        if self._destroyed:
            return
        self._ensure_built()
        self._place_fixed()
        self._hidden = False
        self.reload_processes()
        if not self._folded:
            self._arm_fold_timer()

    def fold_if_idle(self):
        """外部调用：有新输出时重置计时，无输出自动折叠。"""
        if self._built and not self._destroyed and not self._folded:
            self._arm_fold_timer()

    def hide(self):
        if self._built and not self._destroyed:
            try:
                self.win.place_forget()
            except tk.TclError:
                pass
        self._hidden = True
        self._cancel_fold_timer()

    def close(self):
        self._destroyed = True
        self._cancel_fold_timer()
        if self._built:
            try:
                self.win.destroy()
            except tk.TclError:
                pass

    def reload_processes(self):
        """从 deepseek_client.PROCESSES 重建进程下拉列表。"""
        if not self._built or self._hidden:
            return
        try:
            import deepseek_client as dc

            names = [n for n, _ in dc.snapshot_processes()]
            cur = self.combo.get()
            self.combo.configure(values=names)
            if cur in names:
                self.combo.set(cur)
            elif names:
                self.combo.set(names[0])
                self._select(names[0])
            else:
                self.combo.set("")
                self._select(None)
        except Exception:
            logger.debug("进程列表刷新失败", exc_info=True)

    def process_started(self, name, pid=None):
        if self._destroyed:
            return
        self._ensure_built()
        self._buf.setdefault(name, [])
        self.open()
        self.reload_processes()
        self._select(name)
        self.status_lbl.configure(text=f"运行中 pid={pid}")
        self.fold_status_lbl.configure(text=f"● {name} · pid={pid}")

    def append_line(self, name, line):
        if not self._built or self._destroyed:
            return
        # 单行限长：minified JSON/二进制日志单行可无限长，绕过行数裁剪撑爆内存
        if len(line) > 4096:
            line = line[:4096] + "…[超长行已截断]"
        self._buf.setdefault(name, []).append(line)
        if len(self._buf[name]) > 2000:
            del self._buf[name][: len(self._buf[name]) - 2000]
        if name == self._current:
            self._append_text(line)
        else:
            self._dirty.add(name)
            self.status_lbl.configure(text="其他进程有新输出")
            self.fold_status_lbl.configure(text=f"● {name} · 新输出")
        # 有新输出 → 重置空闲折叠计时（展开状态下继续计时）
        if self._built and not self._folded:
            self._arm_fold_timer()

    def process_exited(self, name, code):
        if not self._built:
            return
        if name == self._current:
            self._append_text(f"── 进程已退出（code={code}）──")
            self.status_lbl.configure(text=f"已退出 code={code}")
            self.fold_status_lbl.configure(text=f"■ {name} · 已退出 {code}")
            self.badge_lbl.configure(
                text=f"■ 已退出 {code}",
                fg=self._t("error", "#ff3b30"),
                bg=self._t("surface", "#eef0f3"),
            )
        else:
            self._dirty.add(name)
            self.fold_status_lbl.configure(text=f"■ {name} · 已退出 {code}")

    # ---------- 内部 ----------
    def _select(self, name):
        self._current = name
        self._dirty.discard(name)
        if not name:
            self._set_text("")
            self.status_lbl.configure(text="无进程")
            self.pid_lbl.configure(text="")
            self.badge_lbl.configure(text="")
            self.line_count_lbl.configure(text="")
            self.fold_status_lbl.configure(text="无进程")
            return
        lines = self._buf.get(name, [])
        self._set_text("\n".join(lines))
        import deepseek_client as dc

        entry = dc.get_process(name)
        if entry:
            status = "运行中" if not entry["exited"] else f"已退出 code={entry['code']}"
            self.status_lbl.configure(text=f"pid={entry['pid']} · {status}")
            self.pid_lbl.configure(
                text=f"pid {entry['pid']} · 启动 {entry.get('started', '?')}"
            )
            self.badge_lbl.configure(
                text="● 运行中" if not entry["exited"] else f"■ 已退出 {entry.get('code', '')}"
            )
            self.badge_lbl.configure(
                fg=self._t("success", "#12b878") if not entry["exited"] else self._t("error", "#ff3b30"),
                bg=self._t("surface", "#eef0f3"),
            )
        self._update_line_count()
        self.text.see("end")

    def _wrap_line(self, line):
        """按可视宽度手动折行（规避 Tk wrap 在 DPI 缩放下对超长行的异常表现）。

        用字体度量精确计算每行可容纳宽度；返回折行后的多行列表。
        """
        try:
            font = tkfont.Font(font=self.text.cget("font"))
        except Exception:
            return [line]
        max_w = getattr(self, "_text_max_w", 0)
        if max_w <= 60:
            max_w = max(150, self.win.winfo_width() - 40)
            self._text_max_w = max_w
        if font.measure(line) <= max_w:
            return [line]
        out = []
        cur = ""
        for ch in line:
            if ch == "\t":
                ch = "    "
            if font.measure(cur + ch) > max_w and cur:
                out.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            out.append(cur)
        return out

    def _append_text(self, line):
        try:
            from datetime import datetime as _dt
            ts = _dt.now().strftime("%H:%M:%S")
            self.text.configure(state="normal")
            # 终端着色：时间戳 + 内容（错误行红色 / 状态行 accent）
            err = any(k in line for k in ("Traceback", "Error", "ERROR", "Exception", "失败", "错误", " refused", "NotImplemented"))
            meta = line.startswith("──")
            tag = "err" if err else ("meta" if meta else None)
            parts = self._wrap_line(line)
            for i, seg in enumerate(parts):
                if i == 0:
                    self.text.insert("end", f"{ts} ", "ts")
                else:
                    self.text.insert("end", "        ", "ts")
                if tag:
                    self.text.insert("end", seg + "\n", tag)
                else:
                    self.text.insert("end", seg + "\n")
            n = int(self.text.index("end-1c").split(".")[0])
            if n > MAX_TEXT_LINES:
                self.text.delete("1.0", f"{n - MAX_TEXT_LINES}.0")
            if self._follow_var.get():
                self.text.see("end")
            self.text.configure(state="disabled")
            self._update_line_count()
        except tk.TclError:
            pass

    def _update_line_count(self):
        try:
            n = int(self.text.index("end-1c").split(".")[0]) - 1
            self.line_count_lbl.configure(text=f"{n} 行")
            if self._current:
                self.fold_status_lbl.configure(
                    text=f"● {self._current} · {n} 行"
                )
        except tk.TclError:
            pass

    def _set_text(self, content):
        try:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            if content:
                from datetime import datetime as _dt
                ts = _dt.now().strftime("%H:%M:%S")
                for line in content.splitlines():
                    parts = self._wrap_line(line)
                    for i, seg in enumerate(parts):
                        if i == 0:
                            self.text.insert("end", f"{ts} ", "ts")
                        else:
                            self.text.insert("end", "        ", "ts")
                        self.text.insert("end", seg + "\n")
            self.text.see("end")
            self.text.configure(state="disabled")
            self._update_line_count()
        except tk.TclError:
            pass

    def _stop_current(self):
        if not self._current:
            return
        if self.on_stop:
            self.on_stop(self._current)

    def _clear_current(self):
        if not self._current:
            return
        self._buf[self._current] = []
        self._set_text("")


def ttk_combobox(parent):
    from tkinter import ttk

    return ttk.Combobox(parent, state="readonly", width=26)
