# -*- coding: utf-8 -*-
"""Dialogs: session."""
import tkinter as tk
from tkinter import ttk

from .common import FONT_FAMILY, MONO_FAMILY

def show_history_sessions(app):
    """历史会话库：查看/载入/批量删除历史会话。"""
    import logging
    import threading
    import tkinter as tk
    from tkinter import messagebox

    t = app._theme()
    state = {"items": []}
    dialog, body, footer = app._dialog_shell(
        "历史会话库", 580, 460,
        subtitle="所有会话按需落盘，点击载入完整消息；Ctrl/Shift 多选可批量删除",
    )
    listbox = tk.Listbox(
        body,
        width=66,
        selectmode="extended",
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
    listbox.insert("end", "正在加载历史会话…")
    hint = app._lbl(
        body,
        "提示：Ctrl/Shift 或鼠标框选可多选，支持批量删除",
        role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
    )
    hint.pack(anchor="w", pady=(4, 0))

    def reload():
        listbox.delete(0, "end")
        listbox.insert("end", "正在加载历史会话…")

        def worker():
            try:
                state["items"] = app.list_saved_sessions()
            except Exception:
                state["items"] = []
                logging.exception("扫描历史会话失败")
            app._ui_queue.put(("history_loaded", (dialog, listbox, state)))

        threading.Thread(target=worker, daemon=True).start()

    def load_selected():
        items = state["items"]
        sel = listbox.curselection()
        if not sel or not items:
            return
        if len(sel) > 1:
            messagebox.showinfo("提示", "载入仅支持单选，请只选择一个会话。")
            return
        it = items[sel[0]]
        if app._current.get("id") == it["id"]:
            messagebox.showinfo("提示", "该会话已在当前列表中。")
            return
        if app.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        if app.load_session_from_file(it["id"]):
            dialog.destroy()

    def toggle_select_all():
        items = state["items"]
        if not items:
            return
        if listbox.curselection() and len(listbox.curselection()) == len(items):
            listbox.selection_clear(0, "end")
        else:
            listbox.selection_set(0, "end")

    def delete_selected():
        items = state["items"]
        sel = listbox.curselection()
        if not sel or not items:
            return
        if len(sel) == 1:
            it = items[sel[0]]
            if not messagebox.askyesno(
                "删除历史会话", f"确认删除会话「{it['name']}」？"
            ):
                return
        else:
            if not messagebox.askyesno(
                "批量删除",
                f"确认删除选中的 {len(sel)} 个历史会话？此操作不可恢复。",
            ):
                return
        deleted = 0
        for idx in reversed(sel):
            it = items[idx]
            if app.delete_session_file(it["id"]):
                deleted += 1
        app._flash_status(f"已删除 {deleted} 个历史会话")
        reload()

    listbox.bind("<Double-1>", lambda e: load_selected())
    app._footer_btn(footer, "关闭", dialog.destroy)
    app._footer_btn(footer, "批量删除", delete_selected)
    app._footer_btn(footer, "全选/取消", toggle_select_all)
    app._footer_btn(footer, "载入选中", load_selected, primary=True)

    reload()

def show_command_palette(app):
    """命令面板（Ctrl+K）：输入过滤全部常用操作，Enter 执行。"""
    import logging
    import tkinter as tk

    t = app._theme()
    if getattr(app, "_palette", None) is not None:
        try:
            if app._palette.winfo_exists():
                app._palette.destroy()
        except tk.TclError:
            pass
        app._palette = None
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    app._palette = dialog
    dialog.title("命令面板")
    dialog.overrideredirect(True)
    dialog.transient(app.root)
    dialog.geometry("420x360")
    dialog.bind("<Escape>", lambda e: dialog.destroy())
    entry = tk.Entry(
        dialog, bg=t["input_bg"], fg=t["input_fg"], insertbackground=t["input_fg"],
        relief="flat", highlightthickness=1, highlightbackground=t["border"],
        highlightcolor=t["accent"], font=(FONT_FAMILY, 10),
    )
    entry.pack(fill="x", padx=10, pady=(10, 6))
    hint = tk.Label(
        dialog, text="Esc 关闭 · ↑/↓ 选择 · Enter 执行", bg=t["panel"],
        fg=t["text_sec"], font=(FONT_FAMILY, 9),
    )
    hint.pack(anchor="e", padx=12, pady=(0, 2))
    listbox = tk.Listbox(
        dialog, bg=t["panel"], fg=t["text"],
        selectbackground=t["selection"], selectforeground=t["accent_text"],
        activestyle="none", relief="flat", borderwidth=0,
        highlightthickness=0, font=(FONT_FAMILY, 9), exportselection=False,
    )
    listbox.pack(fill="both", expand=True, padx=6, pady=(0, 8))

    actions = [
        ("新会话", app.new_conversation),
        ("新建临时会话", lambda: app.add_tab(ephemeral=True)),
        ("生成会话纪要", app.generate_session_summary),
        ("会话结构导航", app.show_session_timeline),
        ("角色与提示词", app.show_roles),
        ("切换主题", app.toggle_theme),
        ("查余额", app.check_balance),
        ("用量统计", app.show_stats),
        ("预算设置", app.edit_budget),
        ("上下文详情", app.show_context_details),
        ("模型对比", app.compare_models),
        ("工具设置", app.edit_tools),
        ("权限设置", app.edit_permissions),
        ("工作目录", app.choose_working_dir),
        ("工作区文件树", app.show_workspace_tree),
        ("最近产物", app.show_recent_outputs),
        ("提示词库管理", app.manage_prompts),
        ("定时任务", app.manage_schedules),
        ("推送与数据库配置", app.show_external_config),
        ("进程终端", app.show_process_terminal),
        ("导出历史", app.export_history),
        ("导入会话", app.import_session_file),
        ("数据清理", app.show_cleanup),
        ("关于", app.show_about),
    ]

    def refresh(_e=None):
        q = entry.get().strip().lower()
        listbox.delete(0, "end")
        for label, _fn in actions:
            if not q or q in label.lower():
                listbox.insert("end", label)
        if listbox.size():
            listbox.selection_set(0)
            listbox.activate(0)

    def run(_e=None):
        sel = listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        label = listbox.get(idx)
        fn = dict((l, f) for l, f in actions)[label]
        dialog.destroy()
        app._palette = None
        try:
            fn()
        except Exception:
            logging.exception("命令面板执行失败: %s", label)

    entry.bind("<KeyRelease>", refresh)
    entry.bind("<Return>", run)
    # 返回 "break"：阻止 Entry 默认光标移动与列表激活状态混杂
    entry.bind("<Down>", lambda e: (
        listbox.selection_clear(0, "end"),
        listbox.selection_set(min(listbox.size() - 1, (listbox.curselection() or [0])[0] + 1)),
        listbox.activate((listbox.curselection() or [0])[0]),
        "break")[3])
    entry.bind("<Up>", lambda e: (
        listbox.selection_clear(0, "end"),
        listbox.selection_set(max(0, (listbox.curselection() or [0])[0] - 1)),
        listbox.activate((listbox.curselection() or [0])[0]),
        "break")[3])
    listbox.bind("<Button-1>", lambda e: None)
    listbox.bind("<Double-1>", run)
    dialog.bind("<Escape>", lambda e: dialog.destroy())
    refresh()
    try:
        cx = app.root.winfo_rootx() + (app.root.winfo_width() - 420) // 2
        cy = app.root.winfo_rooty() + (app.root.winfo_height() - 360) // 3
        dialog.geometry(f"+{cx}+{cy}")
    except Exception:
        pass
    # 注意：不用 grab_set——无边框窗口 + grab 会拦截主窗口全部事件，
    # 且没有可见关闭按钮，极易表现为"程序卡死"（Windows 上还会与
    # 输入法/焦点组合锁死事件循环）。非模态 + Esc 关闭更安全，
    # 主窗口始终可交互，面板残留无害（再次 Ctrl+K 会先销毁旧面板）。
    entry.focus_force()
    entry.focus_set()

def show_context_details(app):
    """上下文占用详情：当前上下文的构成占比。"""
    import tkinter as tk

    import tokens
    from config_defaults import MAX_CONTEXT_TOKENS
    from deepseek_client import MODELS

    t = app._theme()
    # 复用 worker 已算好的计数（若有），避免主线程全量 tiktoken（1M 上下文可卡 1-2s）
    with app._messages_lock:
        msgs = list(app.messages)
    if getattr(app, "_ctx_counts", None) is None or len(app._ctx_counts) != len(msgs):
        # 长度不一致 = worker 压缩/裁剪过，计数已失效，按当前消息重算
        app._ctx_counts = tokens.message_token_counts(msgs)
    counts = app._ctx_counts
    total = tokens.BASE_OVERHEAD + sum(counts)

    def cat(msg):
        if msg.get("role") == "system":
            if (msg.get("content") or "").startswith("[历史对话摘要]"):
                return "历史摘要"
            return "系统提示词"
        if msg.get("role") == "user":
            return "用户消息"
        if msg.get("role") == "tool":
            return "工具调用记录"
        if msg.get("role") == "assistant":
            if msg.get("reasoning_content"):
                return "思考过程（含回复）"
            return "助手回复"
        return "其他"

    groups = {}
    for i, msg in enumerate(msgs):
        key = cat(msg)
        g = groups.setdefault(key, {"count": 0, "tokens": 0, "msgs": 0})
        g["count"] += 1
        g["tokens"] += counts[i] if i < len(counts) else 0
        g["msgs"] += 1

    dialog, body, footer = app._dialog_shell(
        "上下文占用详情", 480, 440,
        subtitle="当前上下文的构成占比，帮助了解 1M 窗口的使用情况",
    )
    app._lbl(
        body,
        f"当前上下文估算 {total:,} token / "
        f"{MODELS.get(app.model_combo.get(), {}).get('max_context_tokens', MAX_CONTEXT_TOKENS):,}",
        role="label_sec",
        bg="panel",
        font=(FONT_FAMILY, 10, "bold"),
    ).pack(anchor="w", pady=(0, 8))
    if app.last_usage:
        lu = app.last_usage
        hit = lu.get("cache_hit", 0)
        miss = lu.get("cache_miss", 0)
        ratio = f"{hit / (hit + miss):.0%}" if (hit + miss) else "-"
        app._lbl(
            body,
            f"最近一轮：输入 {lu.get('prompt', 0):,} token（缓存命中 {hit:,} / 未命中 {miss:,}，占比 {ratio}）"
            f" · 输出 {lu.get('completion', 0):,} token",
            role="label_sec",
            bg="panel",
            font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(0, 10))
    order = ["系统提示词", "历史摘要", "用户消息", "助手回复", "思考过程（含回复）", "工具调用记录", "其他"]
    max_tokens = max((g["tokens"] for g in groups.values()), default=1)
    for key in order:
        g = groups.get(key)
        if not g:
            continue
        row = tk.Frame(body, bg=t["panel"])
        row.pack(fill="x", pady=3)
        app._restyle.append((row, "panel"))
        pct = g["tokens"] / total if total else 0
        bar = ttk.Progressbar(
            row,
            style="Context.Horizontal.TProgressbar",
            maximum=max_tokens,
            value=g["tokens"],
            length=200,
        )
        bar.pack(side="left")
        app._lbl(
            row,
            f"{key}: {g['tokens']:,} ({pct:.1%}) · {g['msgs']} 条",
            bg="panel",
            font=(FONT_FAMILY, 9),
        ).pack(side="left", padx=(10, 0))
    app._footer_btn(footer, "关闭", dialog.destroy)

def show_session_timeline(app):
    """会话轨迹：按时间顺序列出消息/思考/工具调用/系统事件，双击定位原文。"""
    import tkinter as tk

    t = app._theme()
    dialog, body, footer = app._dialog_shell(
        "会话轨迹", 620, 480,
        subtitle="完整轨迹：消息 + 思考 + 工具调用 + 系统事件；双击助手/用户行定位原文",
    )
    frame = tk.Frame(body, bg=t["panel"])
    frame.pack(fill="both", expand=True)
    app._restyle.append((frame, "panel"))
    listbox = tk.Listbox(
        frame, bg=t["input_bg"], fg=t["input_fg"],
        selectbackground=t["selection"], selectforeground=t["accent_text"],
        activestyle="none", relief="flat", borderwidth=0,
        highlightthickness=0, font=(FONT_FAMILY, 9), exportselection=False,
    )
    sb = tk.Scrollbar(
        frame, orient="vertical", command=listbox.yview,
        relief="flat", bd=0, highlightthickness=0, width=10,
    )
    sb.pack(side="right", fill="y")
    listbox.pack(side="left", fill="both", expand=True)
    listbox.configure(yscrollcommand=sb.set)
    sb.configure(
        bg=t["disabled"], activebackground=t["text_sec"],
        troughcolor=t["input_bg"],
        highlightbackground=t["input_bg"], highlightcolor=t["input_bg"],
    )
    labels = app._timeline_items(app.blocks)
    if len(labels) > 300:
        labels = labels[-300:]
    for _idx, text_, _j in labels:
        listbox.insert("end", text_)
    if not labels:
        listbox.insert("end", "（会话暂无轨迹）")

    def jump():
        sel = listbox.curselection()
        if not sel or sel[0] >= len(labels):
            return
        idx, _text, jumpl = labels[sel[0]]
        if not jumpl or idx is None:
            app._flash_status("该条目无原文可跳转（工具/事件/思考）")
            return
        dialog.destroy()
        app._scroll_to_message(idx)

    listbox.bind("<Double-1>", lambda e: jump())
    listbox.bind("<Return>", lambda e: jump())
    app._footer_hint(footer, f"{len(labels)} 个轨迹条目 · 消息/思考/工具/事件")
    app._footer_btn(footer, "关闭", dialog.destroy)
    app._footer_btn(footer, "跳转", jump, primary=True)
