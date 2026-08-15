# -*- coding: utf-8 -*-
"""Dialogs: about_help."""
import tkinter as tk
from tkinter import ttk

from .common import FONT_FAMILY, MONO_FAMILY

def show_about(app):
    """关于对话框：品牌信息 + 能力一览（正式版品牌视觉）。"""
    dialog, body, footer = app._dialog_shell(
        f"关于 {app.APP_NAME} {app.APP_NAME_EN}", 460, 420,
        subtitle="深海蓝鲸 · 专业桌面 AI 工作台",
    )
    app._lbl(body, f"🐋 {app.APP_NAME} {app.APP_NAME_EN}", role="label_accent", bg="panel",
             font=(FONT_FAMILY, 18, "bold")).pack(anchor="w", pady=(0, 2))
    app._lbl(body, f"版本 {app.VERSION} · 基于 DeepSeek V4 API", role="label_sec", bg="panel",
             font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(0, 14))
    for line in (
        "· 流式思考与回答、1M 长上下文、缓存命中优化",
        "· 100+ Agent 工具：文档/代码/浏览器/数据/媒体/云盘/公众号写作",
        "· 产物面板与产物条：生成的文件一键直达",
        "· 会话快照、用量统计、预算控制、隐私模式",
        "· 自我进化：AI 可感知自身代码并提出改进提案",
    ):
        app._lbl(body, "✓ " + line, bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w", pady=1)
    app._lbl(
        body, f"\n{app.APP_NAME} 是独立产品，与 DeepSeek 官方无任何关联。",
        role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
    ).pack(anchor="w")
    app._footer_btn(footer, "关闭", dialog.destroy)

def show_balance(app, data):
    """余额查询结果：品牌对话框展示（替代系统 messagebox）。"""
    import tkinter as tk

    t = app._theme()
    dialog, body, footer = app._dialog_shell("余额查询", 460, 320, subtitle="DeepSeek 账户余额")
    app._lbl(
        body, "账户状态: " + ("✅ 可用" if data.get("is_available") else "❌ 不可用"),
        role="label_accent" if data.get("is_available") else "label_error",
        bg="panel", font=(FONT_FAMILY, 11, "bold"),
    ).pack(anchor="w", pady=(0, 10))
    for info in data.get("balance_infos", []):
        card = tk.Frame(body, bg=t["surface"], padx=12, pady=10)
        card.pack(fill="x", pady=4)
        app._restyle.append((card, "surface"))
        app._lbl(
            card, f"总余额 ¥{info.get('total_balance')}",
            bg="surface", font=(FONT_FAMILY, 12, "bold"),
        ).pack(anchor="w")
        app._lbl(
            card,
            f"赠送 ¥{info.get('granted_balance')} · 充值 ¥{info.get('topped_up_balance')}",
            role="label_sec", bg="surface", font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(2, 0))
    app._footer_btn(footer, "关闭", dialog.destroy)

def show_help(app):
    """帮助对话框：常用操作速查（正式版排版）。"""
    t = app._theme()
    dialog, body, footer = app._dialog_shell(
        "使用说明", 520, 460, subtitle="常用操作速查 · F1 随时打开"
    )
    rows = [
        ("发送消息", "Enter 发送 · Shift+Enter 换行 · Ctrl+Enter 快速发送"),
        ("新会话 / 关闭", "Ctrl+N 新建 · Ctrl+W 关闭 · 双击会话名重命名"),
        ("对话搜索", "Ctrl+F 会话内搜索 · Ctrl+Shift+F 全局搜索"),
        ("导出历史", "Ctrl+E 导出 MD/TXT/HTML/JSONL"),
        ("命令面板", "Ctrl+K 唤起全部常用操作"),
        ("重新生成", "F5 重新生成最后回复"),
        ("编辑重发", "聊天区右键消息 → 编辑此消息"),
        ("消息操作", "右键消息：复制/收藏/固定/分叉/引用/朗读/快速动作"),
        ("产物查看", "右侧面板「📂 文件」Tab + 输入框上方产物条"),
        ("生成中打断", "直接发送新消息即可打断并继续"),
        ("设置面板", "左栏「⚙ 设置」按钮收起/展开右侧面板"),
        ("主题与字号", "菜单 视图 → 切换主题 / 增大/减小字号"),
    ]
    for k, v in rows:
        row = tk.Frame(body, bg=t["panel"])
        row.pack(fill="x", pady=2)
        app._restyle.append((row, "panel"))
        app._lbl(row, k, role="label_accent", bg="panel", font=(FONT_FAMILY, 9, "bold"),
                 width=12, anchor="w").pack(side="left")
        app._lbl(row, v, bg="panel", font=(FONT_FAMILY, 9), anchor="w").pack(side="left")
    app._footer_btn(footer, "关闭", dialog.destroy)

def show_welcome(app):
    """欢迎页：首次启动配置 API Key / 体验试玩任务。"""
    import webbrowser
    import tkinter as tk
    from tkinter import ttk

    if app.cfg.get("api_key"):
        app.cfg["welcomed"] = True
        app.save_config(app.cfg)
        return
    t = app._theme()
    dialog, body, footer = app._dialog_shell(
        f"欢迎使用 {app.APP_NAME} {app.APP_NAME_EN}", 560, 440,
        subtitle="为 DeepSeek V4 深度优化的桌面 AI 工作台",
    )
    dialog.resizable(False, False)
    dialog.grab_set()
    for line in (
        "· 流式输出思考过程与回答，支持 1M 长上下文与缓存优化",
        "· 100+ Agent 工具：文档/代码/浏览器/数据/媒体/公众号写作",
        "· 产物面板：生成的文件一键打开，无需翻找目录",
        "· 多会话、上下文自动压缩、用量统计与预算控制",
        "· 提示词库、消息收藏、分支对话、深色主题",
    ):
        app._lbl(body, "✓ " + line, bg="panel", font=(FONT_FAMILY, 9)).pack(
            anchor="w", pady=1
        )
    app._lbl(
        body, "可选：系统菜单 → 「IM 通道配置」可接入企业微信/Telegram 推送与召唤（不配置不影响使用）。",
        role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
    ).pack(anchor="w", pady=(12, 0))
    app._lbl(
        body, "第一步：配置 API Key（可在 https://platform.deepseek.com 申请）",
        role="label_sec", bg="panel", font=(FONT_FAMILY, 9, "bold"),
    ).pack(anchor="w", pady=(12, 4))
    row = tk.Frame(body, bg=t["panel"])
    row.pack(fill="x")
    app._restyle.append((row, "panel"))
    app.welcome_key_var = tk.StringVar()
    entry = ttk.Entry(row, textvariable=app.welcome_key_var, show="*")
    entry.pack(side="left", fill="x", expand=True)
    app._mk_button(
        row, "获取 Key", lambda: webbrowser.open("https://platform.deepseek.com"), fsz=9
    ).pack(side="left", padx=(8, 0))

    def done():
        key = app.welcome_key_var.get().strip()
        if key:
            app.cfg["api_key"] = key
            app.key_var.set(key)
        app.cfg["welcomed"] = True
        app.save_config(app.cfg)
        dialog.destroy()

    app._footer_btn(footer, "开始使用", done, primary=True)
    app._footer_btn(
        footer,
        "先体验试玩任务",
        lambda: (done(), app.after(300, lambda: app._run_playground("写周报并存盘"))),
    )

def show_plugin_guide(app, plugin):
    """安装后引导：使用方式 + 快速试用（让用户 30 秒感知插件价值）。"""
    import deepseek_client as _dc

    meta = plugin.get("meta") or {}
    c = plugin.get("contents") or {}
    tools = c.get("tools") or []
    skills = c.get("skills") or []
    wf = c.get("workflows") or {}
    sc = c.get("scenario")
    dialog, body, footer = app._dialog_shell(
        f"✅ 插件已安装：{meta.get('icon', '🧩')} {meta.get('name', '')}",
        540, 300,
        subtitle="使用方式与快速试用",
    )
    lines = []
    if tools:
        lines.append(f"🔧 {len(tools)} 个工具：AI 会按需自动调用（与内置工具一起参与任务）")
    if skills:
        lines.append(f"⚡ {len(skills)} 个技能：输入框「⚡ 指令」菜单选择，或点下方按钮直接试用")
    if wf:
        lines.append(f"🔁 {len(wf)} 个流程：AI 自动 / 定时任务 / 「流程管理」手动运行")
    if sc:
        lines.append("🎭 含场景配置：可一键应用思考档/提示词/推荐工具")
    if not lines:
        lines.append("（该插件不含可交互能力）")
    for ln in lines:
        app._lbl(body, ln, bg="panel", font=(FONT_FAMILY, 9), wraplength=480,
                 justify="left").pack(anchor="w", pady=2)
    app._lbl(body, "插件管理：🛠 工具菜单 → 🧩 插件中心 → 我的插件（停用/卸载/导出分享）",
             role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(8, 0))
    if skills:
        s0 = skills[0]
        app._footer_btn(footer, "关闭", dialog.destroy)
        app._footer_btn(
            footer, f"试用技能：{s0.get('name', '')}",
            lambda s=s0: (app._insert_plugin_skill(s), dialog.destroy()),
            primary=True,
        )
    elif wf:
        w0 = next(iter(wf))
        app._footer_btn(footer, "关闭", dialog.destroy)
        app._footer_btn(
            footer, f"运行流程：{w0}",
            lambda n=w0: (dialog.destroy(), app._flash_status(f"🚀 正在运行流程「{n}」…"), _dc.run_workflow(n)),
            primary=True,
        )
    elif sc:
        app._footer_btn(footer, "关闭", dialog.destroy)
        app._footer_btn(
            footer, "应用场景配置",
            lambda: (app._apply_plugin_scenario(sc), dialog.destroy()),
            primary=True,
        )
    else:
        app._footer_btn(footer, "关闭", dialog.destroy)
