# -*- coding: utf-8 -*-
"""Dialogs: productivity."""
import tkinter as tk
from tkinter import ttk

from .common import FONT_FAMILY, MONO_FAMILY

def show_batch_task(app):
    """批量任务：多选文件 + 指令模板（{file} 占位），一次发送让 AI 逐个处理。"""
    from tkinter import filedialog, simpledialog

    files = filedialog.askopenfilenames(
        title="选择要批量处理的文件",
        parent=app.root,
        filetypes=[("所有文件", "*.*")],
    )
    if not files:
        return
    template = simpledialog.askstring(
        "批量任务",
        f"已选 {len(files)} 个文件。\n输入任务指令（用 {{{{file}}}} 表示每个文件路径）：\n"
        "示例：读取 {file} 并总结要点",
        parent=app.root,
    )
    if template is None or not template.strip():
        return
    file_list = "\n".join(f"- {p}" for p in files)
    prompt = (
        f"【批量任务】请对以下 {len(files)} 个文件逐个执行同一指令，每个文件都处理并单独汇报结果，不要遗漏。\n"
        f"指令模板：{template.strip()}\n"
        f"（模板中的 {{{{file}}}} 请替换为对应的文件完整路径）\n\n"
        f"文件列表：\n{file_list}\n\n"
        "请依次处理，逐个说明处理结果。"
    )
    app._clear_placeholder()
    app.send(text=prompt)

def show_fim_dialog(app):
    """FIM 代码补全（Beta）对话框。"""
    import threading
    import tkinter as tk
    from tkinter import messagebox

    cfg = app.save_widgets_to_config()
    if not cfg["api_key"]:
        messagebox.showwarning("未配置 API Key", "请先填写 DeepSeek API Key。")
        return
    t = app._theme()
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title("FIM 代码补全（Beta）")
    dialog.geometry("700x540")
    dialog.transient(app.root)
    body = tk.Frame(dialog, bg=t["panel"])
    body.pack(fill="both", expand=True, padx=14, pady=(12, 0))
    app._restyle.append((body, "panel"))
    app._lbl(
        body, "前缀（已有代码，模型从此继续补全中间内容）",
        role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
    ).pack(anchor="w")
    prefix_text = tk.Text(
        body, height=8, wrap="none", bg=t["input_bg"], fg=t["input_fg"],
        insertbackground=t["input_fg"], relief="flat", highlightthickness=1,
        highlightbackground=t["border"], highlightcolor=t["accent"],
        font=(MONO_FAMILY, 9), padx=8, pady=6,
    )
    prefix_text.pack(fill="x", pady=(2, 6))
    app._lbl(
        body, "后缀（可选，补全内容位于前缀与后缀之间）",
        role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
    ).pack(anchor="w")
    suffix_text = tk.Text(
        body, height=4, wrap="none", bg=t["input_bg"], fg=t["input_fg"],
        insertbackground=t["input_fg"], relief="flat", highlightthickness=1,
        highlightbackground=t["border"], highlightcolor=t["accent"],
        font=(MONO_FAMILY, 9), padx=8, pady=6,
    )
    suffix_text.pack(fill="x", pady=(2, 6))
    app._lbl(
        body, "补全结果", role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
    ).pack(anchor="w")
    result_text = tk.Text(
        body, height=9, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
        insertbackground=t["input_fg"], relief="flat", highlightthickness=1,
        highlightbackground=t["border"], highlightcolor=t["accent"],
        font=(MONO_FAMILY, 9), padx=8, pady=6,
    )
    result_text.pack(fill="both", expand=True, pady=(2, 6))
    status_lbl = app._lbl(
        body, "FIM 需要 Beta 端点，未开启时自动使用 /beta", role="label_sec",
        bg="panel", font=(FONT_FAMILY, 9),
    )
    status_lbl.pack(anchor="w")
    bar = tk.Frame(dialog, bg=t["panel"])
    bar.pack(fill="x", padx=14, pady=(8, 12))
    app._restyle.append((bar, "panel"))

    def run_fim():
        prefix = prefix_text.get("1.0", "end").strip()
        if not prefix:
            messagebox.showinfo("提示", "请输入前缀。")
            return
        suffix = suffix_text.get("1.0", "end").strip()
        status_lbl.configure(text="正在调用 FIM 补全…")
        result_text.delete("1.0", "end")

        # worker 线程禁止直接碰 Tk（after/configure 都不是线程安全的），
        # 结果经 UI 队列回主线程 _drain_ui_queue 处理
        app._fim_widgets = (result_text, status_lbl, t)
        app._capture_client_params()

        def worker():
            try:
                client = app.ensure_client()
                result = client.fim_complete(prefix, suffix)
                app._ui_queue.put(("fim_done", (True, result)))
            except Exception as e:
                app._ui_queue.put(("fim_done", (False, str(e))))

        threading.Thread(target=worker, daemon=True).start()

    dialog.bind(
        "<Destroy>",
        lambda e: setattr(app, "_fim_widgets", None)
        if e.widget is dialog
        else None,
    )

    def insert_result():
        result = result_text.get("1.0", "end").strip()
        if not result:
            return
        app._clear_placeholder()
        app.input_text.delete("1.0", "end")
        app.input_text.insert("1.0", result)
        app.input_text.focus_set()
        dialog.destroy()
        app._flash_status("已插入补全结果")

    app._mk_button(bar, "补全", run_fim, kind="primary", fsz=9).pack(side="left")
    app._mk_button(bar, "插入输入框", insert_result, fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

def show_variants(app):
    """回复变体：查看/恢复/复制已保存的回复版本。"""
    import tkinter as tk

    t = app._theme()
    variants = app._current.get("variants") or []
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title(f"回复变体（{app._session_display_name(app._current)}）")
    dialog.geometry("620x420")
    dialog.transient(app.root)

    left = tk.Frame(dialog, bg=t["panel"])
    left.pack(side="left", fill="y", padx=(12, 6), pady=12)
    app._restyle.append((left, "panel"))
    listbox = tk.Listbox(
        left,
        width=16,
        bg=t["input_bg"],
        fg=t["input_fg"],
        selectbackground=t["selection"],
        selectforeground=t["accent_text"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=t["border"],
        highlightcolor=t["accent"],
        exportselection=False,
    )
    listbox.pack(fill="both", expand=True)
    for i in range(len(variants)):
        listbox.insert("end", f"第 {i + 1} 版")

    right = tk.Frame(dialog, bg=t["panel"])
    right.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)
    app._restyle.append((right, "panel"))
    viewer = tk.Text(
        right,
        wrap="word",
        bg=t["input_bg"],
        fg=t["input_fg"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=t["border"],
        highlightcolor=t["accent"],
        padx=10,
        pady=8,
    )
    viewer.pack(fill="both", expand=True)
    if not variants:
        viewer.insert("1.0", "暂无变体。\n\n使用「编辑 → 生成变体」保存回复版本。")

    def select_item(_e=None):
        sel = listbox.curselection()
        if not sel:
            return
        viewer.delete("1.0", "end")
        viewer.insert("1.0", variants[sel[0]])

    def restore_item():
        sel = listbox.curselection()
        if not sel or not variants:
            return
        app._replace_last_reply(variants[sel[0]])
        dialog.destroy()

    def copy_item():
        sel = listbox.curselection()
        if not sel or not variants:
            return
        app.root.clipboard_clear()
        app.root.clipboard_append(variants[sel[0]])
        app._flash_status("已复制该版本")

    listbox.bind("<<ListboxSelect>>", select_item)
    bar = tk.Frame(right, bg=t["panel"])
    bar.pack(fill="x", pady=(8, 0))
    app._restyle.append((bar, "panel"))
    app._mk_button(bar, "恢复此版本", restore_item, kind="primary", fsz=9).pack(side="left")
    app._mk_button(bar, "复制", copy_item, fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")
    if variants:
        listbox.selection_set(0)
        select_item()

def show_evolutions(app):
    """自我进化提案：查看/差异/自检/采纳/忽略。"""
    import os
    import shutil
    import tkinter as tk
    from tkinter import messagebox

    t = app._theme()
    items = app.list_evolutions()
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title(f"自我进化提案（{len(items)} 个待处理）")
    dialog.geometry("640x460")
    dialog.transient(app.root)
    left = tk.Frame(dialog, bg=t["panel"])
    left.pack(side="left", fill="y", padx=(12, 6), pady=12)
    app._restyle.append((left, "panel"))
    listbox = tk.Listbox(
        left, width=26, bg=t["input_bg"], fg=t["input_fg"],
        selectbackground=t["selection"], selectforeground=t["accent_text"],
        relief="flat", highlightthickness=1, highlightbackground=t["border"],
        highlightcolor=t["accent"], exportselection=False,
    )
    listbox.pack(fill="both", expand=True)
    for it in items:
        listbox.insert("end", it["name"])
    if not items:
        listbox.insert("end", "（暂无提案）")

    right = tk.Frame(dialog, bg=t["panel"])
    right.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)
    app._restyle.append((right, "panel"))
    viewer = tk.Text(
        right, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
        relief="flat", highlightthickness=1, highlightbackground=t["border"],
        highlightcolor=t["accent"], padx=10, pady=8,
    )
    viewer.pack(fill="both", expand=True)

    def show_item(idx):
        it = items[idx]
        md = os.path.join(it["dir"], "EVOLUTION.md")
        text = ""
        if os.path.exists(md):
            try:
                with open(md, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(4000)
            except Exception:
                pass
        text += "\n\n── 修改文件 ──\n" + "\n".join("· " + f for f in it["files"])
        viewer.delete("1.0", "end")
        viewer.insert("1.0", text)

    def select_item(_e=None):
        sel = listbox.curselection()
        if sel and sel[0] < len(items):
            show_item(sel[0])

    listbox.bind("<<ListboxSelect>>", select_item)

    def view_diff():
        sel = listbox.curselection()
        if not sel or sel[0] >= len(items):
            return
        app._show_evolution_diff(items[sel[0]])

    def apply_item():
        sel = listbox.curselection()
        if not sel or sel[0] >= len(items):
            return
        app._apply_evolution(items[sel[0]]["name"])
        dialog.destroy()
        show_evolutions(app)

    def ignore_item():
        sel = listbox.curselection()
        if not sel or sel[0] >= len(items):
            return
        name = items[sel[0]]["name"]
        if messagebox.askyesno("忽略提案", f"确认忽略并删除提案「{name}」？"):
            try:
                shutil.rmtree(items[sel[0]]["dir"], ignore_errors=True)
            except Exception:
                pass
            dialog.destroy()
            show_evolutions(app)

    def verify_item():
        """提案自检：对提案目录内的 .py 文件做语法编译检查（不应用，只验证）。"""
        sel = listbox.curselection()
        if not sel or sel[0] >= len(items):
            return
        it = items[sel[0]]
        py_files = [
            os.path.join(it["dir"], rel)
            for rel in it["files"]
            if rel.endswith(".py")
        ]
        if not py_files:
            messagebox.showinfo("提案自检", "该提案没有 Python 文件可检查")
            return
        errors = []
        ok_n = 0
        for f in py_files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    source = fh.read()
                compile(source, f, "exec")
                ok_n += 1
            except SyntaxError as e:
                errors.append(f"语法错误 {os.path.basename(f)}：{e}")
            except Exception as e:
                errors.append(f"读取失败 {os.path.basename(f)}：{e}")
        if errors:
            messagebox.showwarning(
                "提案自检未通过",
                f"{len(py_files)} 个文件中 {len(errors)} 个存在问题：\n\n" + "\n".join(errors),
            )
        else:
            messagebox.showinfo(
                "提案自检通过",
                f"✅ {ok_n}/{len(py_files)} 个 Python 文件语法检查全部通过，可安全采纳。",
            )

    bar = tk.Frame(dialog, bg=t["panel"])
    bar.pack(fill="x", padx=12, pady=(0, 12))
    app._restyle.append((bar, "panel"))
    app._mk_button(bar, "查看差异", view_diff, fsz=9).pack(side="left")
    app._mk_button(bar, "提案自检", verify_item, fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "采纳（应用到项目）", apply_item, kind="primary", fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "忽略", ignore_item, fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")
    if items:
        listbox.selection_set(0)
        show_item(0)
