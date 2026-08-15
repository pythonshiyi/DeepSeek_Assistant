# -*- coding: utf-8 -*-
"""Dialogs: workspace."""
import tkinter as tk
from tkinter import ttk

from .common import FONT_FAMILY, MONO_FAMILY

def choose_working_dir(app, workspace_dir):
    """工作目录选择：指定 AI 执行任务的"家"（自动加入权限允许目录）。"""
    import os
    import re
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog

    import permissions

    t = app._theme()
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title("工作目录（AI 执行任务的默认位置）")
    dialog.geometry("560x420")
    dialog.transient(app.root)
    body = tk.Frame(dialog, bg=t["panel"])
    body.pack(fill="both", expand=True, padx=16, pady=(12, 0))
    app._restyle.append((body, "panel"))
    app._lbl(
        body, f"当前：{app._get_active_dir()}",
        role="label_accent", bg="panel", font=(FONT_FAMILY, 9, "bold"),
    ).pack(anchor="w", pady=(0, 8))
    app._lbl(
        body, "AI 的所有文件操作/项目创建默认在此目录下进行，新任务会自动创建独立子目录。",
        role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
    ).pack(anchor="w", pady=(0, 10))

    row = tk.Frame(body, bg=t["panel"])
    row.pack(fill="x", pady=(0, 6))
    app._restyle.append((row, "panel"))
    dir_var = tk.StringVar(value=app._get_active_dir())
    ttk.Entry(row, textvariable=dir_var).pack(side="left", fill="x", expand=True)
    app._mk_button(
        row, "浏览…",
        lambda: dir_var.set(filedialog.askdirectory(initialdir=dir_var.get() or workspace_dir) or dir_var.get()),
        fsz=9,
    ).pack(side="left", padx=(6, 0))

    app._lbl(
        body, "常用目录：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
    ).pack(anchor="w", pady=(4, 2))
    common = [workspace_dir]
    d = permissions.get_data()
    for p in d["filesystem"].get("allowed_dirs", []):
        if os.path.isdir(p) and p not in common:
            common.append(p)
    listbox = tk.Listbox(
        body, height=6, bg=t["input_bg"], fg=t["input_fg"],
        selectbackground=t["selection"], selectforeground=t["accent_text"],
        relief="flat", highlightthickness=1, highlightbackground=t["border"],
        highlightcolor=t["accent"], exportselection=False,
    )
    listbox.pack(fill="both", expand=True)
    for p in common:
        listbox.insert("end", p)

    def pick_common(_e=None):
        sel = listbox.curselection()
        if sel:
            dir_var.set(common[sel[0]])

    listbox.bind("<<ListboxSelect>>", pick_common)

    def mk_subdir():
        name = simpledialog.askstring(
            "新建子目录", "输入子目录名（在当前目录下创建）:", parent=dialog
        )
        if not name:
            return
        name = re.sub(r'[\\/:*?"<>|]', "_", name.strip())[:60]
        if not name:
            return
        target = os.path.join(dir_var.get() or workspace_dir, name)
        try:
            os.makedirs(target, exist_ok=True)
            dir_var.set(target)
            app._flash_status(f"已创建子目录：{target}")
        except Exception as e:
            messagebox.showerror("创建失败", str(e))

    def apply():
        ok, info = app._set_active_dir(dir_var.get())
        if not ok:
            messagebox.showerror("设置失败", info)
            return
        app._flash_status(f"工作目录已切换：{info}")
        dialog.destroy()

    bar = tk.Frame(dialog, bg=t["panel"])
    bar.pack(fill="x", padx=16, pady=(12, 14))
    app._restyle.append((bar, "panel"))
    app._mk_button(bar, "新建子目录", mk_subdir, fsz=9).pack(side="left")
    app._mk_button(bar, "设为工作目录", apply, kind="primary", fsz=9).pack(side="right")
    app._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right", padx=(0, 8))

def show_cleanup(app, targets, delete_target):
    """数据清理对话框。targets: [(名称, 路径, kind)]，delete_target 为删除函数。"""
    import tkinter as tk
    from tkinter import messagebox, ttk

    t = app._theme()
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title("数据清理")
    dialog.geometry("420x460")
    dialog.transient(app.root)
    body = tk.Frame(dialog, bg=t["panel"])
    body.pack(fill="both", expand=True, padx=16, pady=(12, 0))
    app._restyle.append((body, "panel"))
    app._lbl(
        body, "勾选要清理的数据（操作不可恢复）：",
        role="label_sec", bg="panel", font=(FONT_FAMILY, 9, "bold"),
    ).pack(anchor="w", pady=(0, 6))
    vars_ = {}
    for name, _p, _k in targets:
        v = tk.BooleanVar(value=False)
        vars_[name] = v
        ttk.Checkbutton(body, text=name, variable=v).pack(anchor="w", pady=1)

    def select_all():
        all_on = all(v.get() for v in vars_.values())
        for v in vars_.values():
            v.set(not all_on)

    def run_cleanup():
        chosen = [t for t in targets if vars_[t[0]].get()]
        if not chosen:
            messagebox.showinfo("提示", "请至少选择一项。")
            return
        if not messagebox.askyesno(
            "确认清理", f"将删除 {len(chosen)} 类数据，此操作不可恢复。确定？"
        ):
            return
        done = []
        for name, path, kind in chosen:
            n = delete_target(path, kind)
            done.append(f"· {name}：删除 {n} 个文件")
        app._flash_status("数据清理完成")
        messagebox.showinfo("清理完成", "\n".join(done))

    bar = tk.Frame(dialog, bg=t["panel"])
    bar.pack(fill="x", padx=16, pady=(12, 14))
    app._restyle.append((bar, "panel"))
    app._mk_button(bar, "全选/取消", select_all, fsz=9).pack(side="left")
    app._mk_button(bar, "清理", run_cleanup, kind="danger", fsz=9).pack(side="left", padx=(8, 0))
    app._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

def show_workspace_tree(app, workspace_dir):
    """工作区文件树：浏览 AI 产物，双击文件注入输入框，右键复制路径。"""
    import os
    import tkinter as tk

    t = app._theme()
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title("工作区文件树")
    dialog.geometry("560x480")
    dialog.transient(app.root)
    tree = ttk.Treeview(dialog, columns=("size",), show="tree headings")
    tree.heading("size", text="大小")
    tree.column("size", width=90, anchor="e")
    tree.pack(fill="both", expand=True, padx=12, pady=(12, 0))
    paths = {}

    def add_item(parent, text, full, values=()):
        iid = tree.insert(parent, "end", text=text, values=values)
        paths[iid] = full
        return iid

    populated = set()

    def populate(parent, path):
        if path in populated:
            return
        populated.add(path)
        try:
            entries = sorted(os.listdir(path))
        except Exception:
            return
        # 清除占位符后重建该目录子项
        for cid in tree.get_children(parent):
            tree.delete(cid)
        for e in entries:
            full = os.path.join(path, e)
            if os.path.isdir(full):
                iid = add_item(parent, e + os.sep, full)
                try:
                    # 懒加载占位：展开该节点时才遍历子目录（大工作区不冻结 UI）
                    tree.insert(iid, "end", text="…")
                except Exception:
                    pass
            else:
                try:
                    size = os.path.getsize(full)
                    size_txt = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
                except OSError:
                    size_txt = ""
                add_item(parent, e, full, (size_txt,))

    def on_tree_open(_event):
        # 用 focus() 而非 selection()：点击展开箭头时节点往往尚未被选中，
        # 用选中项判断会导致子目录懒加载失效
        iid = tree.focus()
        if not iid:
            return
        full = paths.get(iid)
        if full and os.path.isdir(full):
            populate(iid, full)

    tree.bind("<<TreeviewOpen>>", on_tree_open)

    root_iid = add_item("", "", workspace_dir)
    tree.item(root_iid, text=f"工作区：{workspace_dir}", open=True)
    populate(root_iid, workspace_dir)  # 只遍历根目录一层，子目录按需懒加载

    def inject_file():
        sel = tree.selection()
        if not sel:
            return
        full = paths.get(sel[0])
        if not full or not os.path.isfile(full):
            return
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(8000)
            if len(content) >= 8000:
                content += "\n[文件较大，已截断前 8000 字符]"
        except Exception as e:
            content = f"读取失败: {e}"
        app._clear_placeholder()
        app.input_text.delete("1.0", "end")
        app.input_text.insert(
            "1.0", f"[文件] {os.path.basename(full)}:\n{content}\n\n"
        )
        app.input_text.focus_set()
        dialog.destroy()
        app._flash_status(f"已注入 {os.path.basename(full)}")

    def copy_path():
        sel = tree.selection()
        if not sel:
            return
        full = paths.get(sel[0])
        if full:
            app.root.clipboard_clear()
            app.root.clipboard_append(full)
            app._flash_status("已复制路径")

    def open_selected():
        sel = tree.selection()
        if not sel:
            return
        full = paths.get(sel[0])
        if full:
            app._open_path(full)

    def open_folder():
        sel = tree.selection()
        if not sel:
            return
        full = paths.get(sel[0])
        if not full:
            return
        target = os.path.dirname(full) if os.path.isfile(full) else full
        try:
            os.startfile(target)
        except Exception:
            app._flash_status(f"无法打开目录：{target}")

    tree.bind("<Double-1>", lambda e: inject_file())
    tree.bind("<Button-3>", lambda e: copy_path())
    bar = tk.Frame(dialog, bg=t["panel"])
    bar.pack(fill="x", padx=12, pady=(8, 12))
    app._restyle.append((bar, "panel"))
    app._mk_button(bar, "注入选中文件", inject_file, kind="primary", fsz=9).pack(side="left")
    app._mk_button(bar, "打开", open_selected, fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "打开所在文件夹", open_folder, fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "复制路径", copy_path, fsz=9).pack(side="left", padx=(6, 0))
    hint = app._lbl(
        bar, "双击注入对话 · 右键复制路径", role="label_sec", bg="panel",
        font=(FONT_FAMILY, 9),
    )
    hint.pack(side="right")
