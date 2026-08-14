# -*- coding: utf-8 -*-
"""进程终端面板：后台进程（服务器/长驻任务）的实时输出终端。

- 独立置顶小窗，按需打开（工具菜单或 AI 启动进程时自动弹出）。
- 进程下拉切换、停止、清空、自动跟随滚动。
- 所有更新必须在主线程（队列驱动），面板内部不做线程操作。
"""
import logging
import tkinter as tk

logger = logging.getLogger("whaletalk")

FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"

PANEL_W = 620
PANEL_H = 400
MAX_TEXT_LINES = 2000  # 与 _buf 上限对齐：当前进程可见文本也要裁剪，防无界增长


class ProcessPanel:
    def __init__(self, root, on_stop=None, theme=None):
        self.root = root
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

    def _ensure_built(self):
        if self._built:
            return
        win = tk.Toplevel(self.root)
        win.title("进程终端")
        win.geometry(f"{PANEL_W}x{PANEL_H}")
        win.configure(bg=self._t("panel", "#ffffff"))
        win.protocol("WM_DELETE_WINDOW", self.hide)
        self.win = win

        head = tk.Frame(win, bg=self._t("panel", "#ffffff"))
        head.pack(fill="x", padx=10, pady=(8, 4))
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

        body = tk.Frame(win, bg=self._t("surface", "#eef0f3"))
        body.pack(fill="both", expand=True, padx=10, pady=(4, 6))
        self.text = tk.Text(
            body, wrap="none", state="disabled",
            bg=self._t("code_bg", "#f2f4f7"), fg=self._t("code_fg", "#24292f"),
            relief="flat", highlightthickness=1,
            highlightbackground=self._t("border", "#e3e5e9"),
            font=(MONO_FAMILY, 9), padx=8, pady=6,
        )
        sb = tk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        bar = tk.Frame(win, bg=self._t("panel", "#ffffff"))
        bar.pack(fill="x", padx=10, pady=(0, 10))
        self.btn_stop = tk.Button(
            bar, text="■ 停止选中", command=self._stop_current,
            bg=self._t("page", "#f5f6f8"), fg=self._t("error", "#ff3b30"),
            activebackground=self._t("surface", "#eef0f3"),
            activeforeground=self._t("error", "#ff3b30"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=12, pady=3,
        )
        self.btn_stop.pack(side="left")
        tk.Button(
            bar, text="清空", command=self._clear_current,
            bg=self._t("page", "#f5f6f8"), fg=self._t("text", "#1d1f24"),
            activebackground=self._t("surface", "#eef0f3"),
            activeforeground=self._t("text", "#1d1f24"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=12, pady=3,
        ).pack(side="left", padx=(6, 0))
        tk.Button(
            bar, text="刷新列表", command=self.reload_processes,
            bg=self._t("page", "#f5f6f8"), fg=self._t("text", "#1d1f24"),
            activebackground=self._t("surface", "#eef0f3"),
            activeforeground=self._t("text", "#1d1f24"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=12, pady=3,
        ).pack(side="left", padx=(6, 0))
        self._follow_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            bar, text="自动跟随", variable=self._follow_var,
            bg=self._t("panel", "#ffffff"), fg=self._t("text", "#1d1f24"),
            activebackground=self._t("panel", "#ffffff"),
            font=(FONT_FAMILY, 9),
        ).pack(side="right")

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
            self.text.configure(
                bg=t.get("code_bg", "#f2f4f7"), fg=t.get("code_fg", "#24292f"),
                highlightbackground=t.get("border", "#e3e5e9"),
            )
            self.btn_stop.configure(
                bg=t.get("page", "#f5f6f8"), fg=t.get("error", "#ff3b30"),
                activebackground=t.get("surface", "#eef0f3"),
                activeforeground=t.get("error", "#ff3b30"),
            )
        except tk.TclError:
            pass

    # ---------- 对外接口（主线程） ----------
    def open(self):
        if self._destroyed:
            return
        self._ensure_built()
        self.win.deiconify()
        self.win.lift()
        self._hidden = False
        self.reload_processes()

    def hide(self):
        if self._built and not self._destroyed:
            try:
                self.win.withdraw()
            except tk.TclError:
                pass
        self._hidden = True

    def close(self):
        self._destroyed = True
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

    def process_exited(self, name, code):
        if not self._built:
            return
        if name == self._current:
            self._append_text(f"── 进程已退出（code={code}）──")
            self.status_lbl.configure(text=f"已退出 code={code}")
        else:
            self._dirty.add(name)

    # ---------- 内部 ----------
    def _select(self, name):
        self._current = name
        self._dirty.discard(name)
        if not name:
            self._set_text("")
            self.status_lbl.configure(text="无进程")
            return
        lines = self._buf.get(name, [])
        self._set_text("\n".join(lines))
        import deepseek_client as dc

        entry = dc.get_process(name)
        if entry:
            status = "运行中" if not entry["exited"] else f"已退出 code={entry['code']}"
            self.status_lbl.configure(text=f"pid={entry['pid']} · {status}")
        self.text.see("end")

    def _append_text(self, line):
        try:
            self.text.configure(state="normal")
            self.text.insert("end", line + "\n")
            n = int(self.text.index("end-1c").split(".")[0])
            if n > MAX_TEXT_LINES:
                # 从头裁剪：长驻进程刷屏时 Text 行数有界（否则内存只涨不降 + 逐行变慢）
                self.text.delete("1.0", f"{n - MAX_TEXT_LINES}.0")
            if self._follow_var.get():
                self.text.see("end")
            self.text.configure(state="disabled")
        except tk.TclError:
            pass

    def _set_text(self, content):
        try:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            if content:
                self.text.insert("1.0", content)
            self.text.see("end")
            self.text.configure(state="disabled")
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
