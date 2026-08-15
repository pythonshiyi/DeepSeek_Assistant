# -*- coding: utf-8 -*-
"""Dialogs: data_stats."""
import tkinter as tk
from tkinter import ttk

from .common import FONT_FAMILY, MONO_FAMILY

def show_stats(app):
    import tkinter as tk
    from datetime import date

    import stats

    t = app._theme()
    data = stats.load_stats(app.STATS_PATH)
    today = date.today().isoformat()
    today_usage = stats.day_total(data, today)
    month_key = date.today().strftime("%Y-%m")
    month_usage = stats.empty_day()
    for day, models in data.items():
        if day.startswith(month_key):
            for usage in models.values():
                for key in month_usage:
                    month_usage[key] += usage.get(key, 0)
    total_usage = stats.all_total(data)
    today_cost = sum(
        stats.estimate_cost(usage, model)
        for model, usage in (data.get(today) or {}).items()
    )
    month_cost = sum(
        stats.estimate_cost(usage, model)
        for day, models in data.items()
        if day.startswith(month_key)
        for model, usage in models.items()
    )
    total_cost = sum(
        stats.estimate_cost(usage, model)
        for models in data.values()
        for model, usage in models.items()
    )
    savings = 0.0
    for models in data.values():
        for model, usage in models.items():
            price = stats.pricing().get(model, stats.DEFAULT_PRICE)
            savings += (
                usage.get("cache_hit", 0) * (price["prompt"] - price["cache_hit"])
                / 1_000_000
            )

    def fmt(u, cost):
        hit = u["cache_hit"]
        miss = u["cache_miss"]
        ratio = f"缓存占比 {hit / (hit + miss):.0%}" if (hit + miss) else "缓存占比 -"
        return (
            f"输入 {u['prompt']:,} (命中 {hit:,} / 未命中 {miss:,} · {ratio})\n"
            f"输出 {u['completion']:,}  |  预估费用 ¥{stats.format_cost(cost)}"
        )

    dialog, body, footer = app._dialog_shell("用量统计", 520, 460, subtitle="按天/月/累计汇总 · 含缓存节省估算")
    scroll_wrap = tk.Frame(body, bg=t["panel"])
    scroll_wrap.pack(fill="both", expand=True)
    app._restyle.append((scroll_wrap, "panel"))

    def section(title, usage, cost):
        app._lbl(scroll_wrap, title, role="label_accent", bg="panel",
                 font=(FONT_FAMILY, 10, "bold")).pack(anchor="w", pady=(8, 2))
        app._lbl(scroll_wrap, fmt(usage, cost), bg="panel",
                 font=(FONT_FAMILY, 9)).pack(anchor="w", padx=(12, 0))

    section(f"今日 ({today})", today_usage, today_cost)
    section(f"本月 ({month_key})", month_usage, month_cost)
    section("累计", total_usage, total_cost)
    app._lbl(
        scroll_wrap, f"💰 缓存节省 ≈ ¥{stats.format_cost(savings)}（未命中价差估算）",
        role="label_accent", bg="panel", font=(FONT_FAMILY, 9, "bold"),
    ).pack(anchor="w", pady=(10, 2))
    if data:
        app._lbl(scroll_wrap, "各模型累计:", role="label_sec", bg="panel",
                 font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(6, 2))
        for model in sorted({mdl for models in data.values() for mdl in models}):
            mu = stats.model_total(data, model)
            mc = stats.estimate_cost(mu, model)
            app._lbl(
                scroll_wrap,
                f"· {model}: 输入 {mu['prompt']:,} / 输出 {mu['completion']:,} ≈ ¥{stats.format_cost(mc)}",
                bg="panel", font=(FONT_FAMILY, 9),
            ).pack(anchor="w", padx=(12, 0))
        app._lbl(scroll_wrap, f"统计文件: {app.STATS_PATH}", role="label_sec", bg="panel",
                 font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(8, 0))
    app._footer_btn(footer, "关闭", dialog.destroy)

def show_dependencies(app):
    """依赖状态：可选能力清单与安装指引。"""
    import tkinter as tk

    t = app._theme()
    rows = app._dependency_status()
    n_ok = sum(1 for _, ok, _, _ in rows if ok)
    dialog, body, footer = app._dialog_shell(
        "依赖状态", 580, 500,
        subtitle=f"可选能力：{n_ok}/{len(rows)} 已安装（缺失项给出安装命令；所有缺失功能均自动降级提示）",
    )
    canvas, inner = app._scroll_panel(body)
    for name, ok, use, hint in rows:
        row = tk.Frame(inner, bg=t["panel"])
        row.pack(fill="x", pady=2)
        app._restyle.append((row, "panel"))
        mark = "✅" if ok else "⚠ "
        fg = t["success"] if ok else t["warning"]
        app._lbl(row, f"{mark} {name}", bg="panel", font=(FONT_FAMILY, 9, "bold"),
                 fg=fg, width=17, anchor="w").pack(side="left")
        app._lbl(row, use, role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
                 anchor="w").pack(side="left", padx=(6, 0))
        if not ok:
            app._lbl(row, hint, role="label_sec", bg="panel", font=(MONO_FAMILY, 8),
                     anchor="e").pack(side="right")
    app._footer_btn(footer, "关闭", dialog.destroy)

def show_failures(app):
    """失败模式库：查看 AI 工具曾失败的原因（供分析/规避）。"""
    import tkinter as tk
    from tkinter import messagebox

    t = app._theme()
    items = app._load_failures()
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title(f"失败模式库（{len(items)} 条 · 自动积累工具失败原因）")
    dialog.geometry("560x400")
    dialog.transient(app.root)
    text = tk.Text(
        dialog, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
        relief="flat", highlightthickness=1, highlightbackground=t["border"],
        highlightcolor=t["accent"], padx=12, pady=10,
    )
    text.pack(fill="both", expand=True, padx=12, pady=(12, 0))
    for r in reversed(items[-200:]):
        text.insert(
            "end",
            f"🔧 {r.get('tool', '?')} · [{r.get('ts', '')}]\n"
            f"参数: {r.get('args', '')}\n错误: {r.get('error', '')}\n\n",
        )
    if not items:
        text.insert("1.0", "（暂无失败记录，AI 工具执行失败时自动积累）")
    text.configure(state="disabled")
    bar = tk.Frame(dialog, bg=t["panel"])
    bar.pack(fill="x", padx=12, pady=(8, 12))
    app._restyle.append((bar, "panel"))

    def clear():
        if messagebox.askyesno("清空失败模式", "确认清空全部失败记录？"):
            app._save_failures([])
            dialog.destroy()
            show_failures(app)

    app._mk_button(bar, "清空", clear, fsz=9).pack(side="left")
    app._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

def show_tasklog(app):
    """项目任务记录：查看当前工作目录的任务历史。"""
    import os
    import tkinter as tk
    from tkinter import messagebox

    t = app._theme()
    data = app._load_tasklog()
    tasks = data.get("tasks") or []
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title(f"项目任务记录（{os.path.basename(app._get_active_dir())}，{len(tasks)} 条）")
    dialog.geometry("600x420")
    dialog.transient(app.root)
    text = tk.Text(
        dialog, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
        relief="flat", highlightthickness=1, highlightbackground=t["border"],
        highlightcolor=t["accent"], padx=12, pady=10,
    )
    text.pack(fill="both", expand=True, padx=12, pady=(12, 0))
    for task in reversed(tasks):
        chain = " → ".join(task.get("chain", []))
        arts = "、".join(task.get("artifacts", []))
        text.insert(
            "end",
            f"【{task.get('ts', '')}】{task.get('title', '任务')}\n"
            f"工具链：{chain}\n"
            + (f"产物：{arts}\n" if arts else "")
            + "\n",
        )
    if not tasks:
        text.insert("1.0", "（暂无任务记录，AI 在本目录执行过任务后自动记录）")
    text.configure(state="disabled")
    bar = tk.Frame(dialog, bg=t["panel"])
    bar.pack(fill="x", padx=12, pady=(8, 12))
    app._restyle.append((bar, "panel"))

    def clear():
        if messagebox.askyesno("清空任务记录", "确认清空当前项目的任务记录？"):
            app._save_tasklog({"tasks": []})
            dialog.destroy()
            show_tasklog(app)

    app._mk_button(bar, "清空", clear, fsz=9).pack(side="left")
    app._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

def show_checkpoint(app):
    """任务检查点：查看未完成任务进度 / 清除。"""
    import os
    import tkinter as tk
    from tkinter import messagebox

    import deepseek_client as _dc

    t = app._theme()
    dialog, body, footer = app._dialog_shell(
        "任务检查点（断点续跑）", 560, 360,
        subtitle="长任务进度持久化：崩溃/重启后可从断点继续",
        minsize=(440, 260),
    )
    viewer = tk.Text(
        body, height=10, bg=t["input_bg"], fg=t["input_fg"], relief="flat",
        highlightthickness=1, highlightbackground=t["border"],
        highlightcolor=t["accent"], font=(MONO_FAMILY, 9), padx=8, pady=6,
    )
    viewer.pack(fill="both", expand=True)
    try:
        out = _dc.task_checkpoint_load()
    except Exception:
        out = "错误：读取检查点失败"
    viewer.insert("1.0", out)

    def clear_cp():
        if not messagebox.askyesno("清除检查点", "确认清除任务检查点？"):
            return
        try:
            if _dc.CHECKPOINT_FILE and os.path.exists(_dc.CHECKPOINT_FILE):
                os.remove(_dc.CHECKPOINT_FILE)
            viewer.delete("1.0", "end")
            viewer.insert("1.0", "检查点已清除")
            app._flash_status("任务检查点已清除")
        except Exception as e:
            messagebox.showerror("清除失败", str(e))

    app._footer_btn(footer, "关闭", dialog.destroy)
    app._footer_btn(footer, "清除检查点", clear_cp)

def show_recent_outputs(app):
    """最近产物：查看 AI 创建/修改的文件。"""
    import os
    import tkinter as tk

    t = app._theme()
    recent = list(app._recent_cache)
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title(f"最近产物（{len(recent)} 条，AI 创建/修改的文件）")
    dialog.geometry("640x420")
    dialog.transient(app.root)
    listbox = tk.Listbox(
        dialog, width=80, bg=t["input_bg"], fg=t["input_fg"],
        selectbackground=t["selection"], selectforeground=t["accent_text"],
        relief="flat", highlightthickness=1, highlightbackground=t["border"],
        highlightcolor=t["accent"], exportselection=False,
    )
    listbox.pack(fill="both", expand=True, padx=12, pady=(12, 0))
    for p in recent:
        listbox.insert("end", p)
    if not recent:
        listbox.insert("end", "（暂无产物，AI 执行写文件类工具后自动记录）")

    def open_selected():
        sel = listbox.curselection()
        if not sel or not recent:
            return
        app._open_path(recent[sel[0]])

    def open_folder():
        sel = listbox.curselection()
        if not sel or not recent:
            return
        p = recent[sel[0]]
        target = os.path.dirname(p) if os.path.isfile(p) else p
        try:
            os.startfile(target)
        except Exception:
            app._flash_status(f"无法打开目录：{target}")

    def copy_path():
        sel = listbox.curselection()
        if not sel or not recent:
            return
        app.root.clipboard_clear()
        app.root.clipboard_append(recent[sel[0]])
        app._flash_status("已复制路径")

    def remove_selected():
        sel = listbox.curselection()
        if not sel or not recent:
            return
        recent.pop(sel[0])
        app._recent_cache = recent
        app._save_recent(recent)
        app._recent_dirty = False
        listbox.delete(sel[0])

    def restore_selected():
        sel = listbox.curselection()
        if not sel or not recent:
            return
        app._restore_bak(recent[sel[0]])

    listbox.bind("<Double-1>", lambda e: open_selected())
    bar = tk.Frame(dialog, bg=t["panel"])
    bar.pack(fill="x", padx=12, pady=(8, 12))
    app._restyle.append((bar, "panel"))
    app._mk_button(bar, "打开", open_selected, kind="primary", fsz=9).pack(side="left")
    app._mk_button(bar, "打开所在文件夹", open_folder, fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "还原 .bak", restore_selected, fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "复制路径", copy_path, fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "从列表移除", remove_selected, fsz=9).pack(side="left", padx=(6, 0))
    app._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

def show_stars(app):
    """收藏消息：查看/复制/跳转。"""
    import tkinter as tk

    stars = app._current.get("stars") or []
    t = app._theme()
    dialog, body, footer = app._dialog_shell(
        f"收藏消息（{app._session_display_name(app._current)}）", 620, 420,
        subtitle="聊天区右键消息选择「☆ 收藏此消息」；左侧列表点击查看，双击跳转",
    )
    left = tk.Frame(body, bg=t["panel"])
    left.pack(side="left", fill="y", padx=(0, 8))
    app._restyle.append((left, "panel"))
    listbox = tk.Listbox(
        left,
        width=24,
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
    for star in stars:
        content = star.get("content") or ""
        preview = " ".join(content.split())[:20] or "（空消息）"
        tag = "用户" if star.get("role") == "user" else "助手"
        listbox.insert("end", f"[{tag}] {preview}")
    right = tk.Frame(body, bg=t["panel"])
    right.pack(side="left", fill="both", expand=True, padx=(8, 0))
    app._restyle.append((right, "panel"))
    text = tk.Text(
        right,
        wrap="word",
        bg=t["input_bg"],
        fg=t["input_fg"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=t["border"],
        highlightcolor=t["accent"],
        padx=12,
        pady=10,
    )
    text.pack(fill="both", expand=True)
    if not stars:
        text.insert("1.0", "暂无收藏消息。\n\n在聊天区右键任意消息，选择「☆ 收藏此消息」。")
    text.configure(state="disabled")

    def show_detail(_e=None):
        sel = listbox.curselection()
        text.configure(state="normal")
        text.delete("1.0", "end")
        if not sel or not stars:
            text.insert("1.0", "（选择左侧列表查看详情）" if stars else "暂无收藏消息。")
        else:
            star = stars[sel[0]]
            tag = "用户" if star.get("role") == "user" else "助手"
            text.insert("end", f"【{tag}】{star.get('time', '')}\n\n")
            text.insert("end", star.get("content") or "")
        text.configure(state="disabled")

    def copy_stars():
        lines = []
        for star in stars:
            tag = "用户" if star.get("role") == "user" else "助手"
            lines.append(f"【{tag}】{star.get('time', '')}\n{star.get('content')}")
        app.root.clipboard_clear()
        app.root.clipboard_append("\n\n".join(lines))
        app._flash_status("已复制收藏内容")

    def jump_to_star():
        sel = listbox.curselection()
        if not sel or not stars:
            return
        star = stars[sel[0]]
        content = star.get("content")
        role = star.get("role")
        idx = next(
            (
                i
                for i, m in enumerate(app.messages)
                if m.get("role") == role and m.get("content") == content
            ),
            None,
        )
        if idx is None:
            app._flash_status("该消息已被删除，无法跳转")
            return
        dialog.destroy()
        app.after(80, lambda: app._scroll_to_message(idx))

    listbox.bind("<<ListboxSelect>>", show_detail)
    listbox.bind("<Double-1>", lambda e: jump_to_star())
    app._footer_btn(footer, "关闭", dialog.destroy)
    app._footer_btn(footer, "复制全部", copy_stars)
    app._footer_btn(footer, "跳转", jump_to_star, primary=True)
    show_detail()

def show_feature_suggestions(app):
    """功能建议：鲸语基于对自身的完整理解，提出新功能与升级方向（产出 MD 文档）。"""
    import tkinter as tk

    t = app._theme()
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title("功能建议（鲸语基于自我认知提出升级方向）")
    dialog.geometry("460x280")
    dialog.transient(app.root)
    body = tk.Frame(dialog, bg=t["panel"])
    body.pack(fill="both", expand=True, padx=18, pady=(14, 0))
    app._restyle.append((body, "panel"))
    app._lbl(
        body, "🧬 让鲸语基于对自身代码与能力的理解，提出新功能建议",
        role="label_accent", bg="panel", font=(FONT_FAMILY, 10, "bold"),
    ).pack(anchor="w", pady=(0, 8))
    app._lbl(
        body,
        "鲸语将：分析自身架构与现有能力 → 结合用户场景与 DeepSeek 能力特性 "
        "→ 提出 6-10 个功能建议/升级方向（名称/价值/实现思路/复杂度/优先级）"
        "→ 写入工作区 code-review/ 建议文档。",
        role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        wraplength=400, justify="left",
    ).pack(anchor="w", pady=(0, 12))

    def run():
        prompt = (
            "请基于对鲸语自身的完整理解，提出新的功能添加与升级建议（不是审查 bug）。\n"
            "1. 用 project_info 了解项目全貌。\n"
            "2. 用 read_project_file 阅读关键模块（main.py 可分页、deepseek_client.py、"
            "permissions.py、tokens.py、processpanel.py、taskpanel.py 等），理解现有能力。\n"
            "3. 结合：用户实际使用场景（对话/智能体/办公/创作/自我进化）、"
            "DeepSeek V4 的能力特性（1M 上下文、思考模式、工具调用、缓存计费、峰谷定价）、"
            "以及业界 AI 助手产品趋势。\n"
            "4. 提出 6-10 个功能建议或升级方向，每个必须包含：\n"
            "   - 建议名称与一句话描述\n"
            "   - 价值（解决什么痛点 / 带来什么收益）\n"
            "   - 实现思路（涉及哪些模块、大致怎么做）\n"
            "   - 复杂度（低/中/高）与优先级（高/中/低）\n"
            "5. 用 create_doc 在工作区 code-review/ 生成《鲸语功能建议_日期时间.md》。\n"
            "6. 回复中给出文档路径与 Top 3 建议摘要。"
        )
        app.send(text=prompt)
        dialog.destroy()

    bar = tk.Frame(dialog, bg=t["panel"])
    bar.pack(fill="x", padx=18, pady=(12, 14))
    app._restyle.append((bar, "panel"))
    app._mk_button(bar, "开始分析", run, kind="primary").pack(side="right")
    app._mk_button(bar, "取消", dialog.destroy).pack(side="right", padx=(0, 8))

def show_evolution_audit(app):
    """管理员主动发起：让鲸语自我审查并提交进化提案。"""
    import tkinter as tk
    from tkinter import ttk

    t = app._theme()
    dialog = tk.Toplevel(app.root, bg=t["panel"])
    dialog.title("发起自我审查（鲸语分析自身代码并提交改进提案）")
    dialog.geometry("440x320")
    dialog.transient(app.root)
    body = tk.Frame(dialog, bg=t["panel"])
    body.pack(fill="both", expand=True, padx=18, pady=(14, 0))
    app._restyle.append((body, "panel"))
    app._lbl(
        body, "选择审查重点：", role="label_sec", bg="panel",
        font=(FONT_FAMILY, 10, "bold"),
    ).pack(anchor="w", pady=(0, 8))
    focus_var = tk.StringVar(value="全面审查")
    for key in app.EVOLUTION_AUDIT_TASKS:
        ttk.Radiobutton(
            body, text=f"🔍 {key}", value=key, variable=focus_var,
        ).pack(anchor="w", pady=2)
    app._lbl(
        body,
        "鲸语将：分析项目结构 → 阅读关键模块 → 定位具体问题 → "
        "在工作区 code-review/ 生成完整审查报告 MD（现状代码 / 替换代码 / 验证方式），"
        "供开发 AI 实施。",
        role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        wraplength=380, justify="left",
    ).pack(anchor="w", pady=(10, 0))

    def run():
        focus = focus_var.get()
        label = app.EVOLUTION_AUDIT_TASKS.get(focus, "全面代码审查")
        prompt = (
            f"请对鲸语自身进行一次{label}，产出审查报告文档（不是修改代码）。\n"
            "重要：鲸语项目位于程序安装目录（不是工作区 Documents\\WhaleTalk\\workspace，"
            "工作区是空的）。你必须使用专用工具：\n"
            "1. 用 project_info 了解项目结构与规模。\n"
            "2. 用 read_project_file 阅读关键模块（main.py、deepseek_client.py、"
            "permissions.py、tokens.py、stats.py、crypto.py、processpanel.py、"
            "taskpanel.py、exporters.py 等）。\n"
            "3. 禁止使用 list_dir / read_file / search_local 等工作区工具分析自身代码。\n"
            f"4. 找出{label}方面的具体问题（至少 3 个，按严重度排序，引用具体代码位置与原因）。\n"
            "5. 用 create_doc 在工作区 code-review 子目录生成《鲸语代码审查报告_日期时间.md》，"
            "报告必须包含：\n"
            "   - 问题总览表（严重度 / 文件 / 位置 / 问题 / 是否建议修复）\n"
            "   - 每个核心问题的【现状代码 / 替换代码 / 验证方式】（替换代码必须是完整可直接使用的补丁）\n"
            "   - 低危观察项清单\n"
            "   - 给实施 AI 的步骤清单与风险回滚说明\n"
            "6. 完成后在回复中给出报告完整路径与核心问题摘要。"
        )
        app.send(text=prompt)
        dialog.destroy()

    bar = tk.Frame(dialog, bg=t["panel"])
    bar.pack(fill="x", padx=18, pady=(12, 14))
    app._restyle.append((bar, "panel"))
    app._mk_button(bar, "开始审查", run, kind="primary").pack(side="right")
    app._mk_button(bar, "取消", dialog.destroy).pack(side="right", padx=(0, 8))
