# -*- coding: utf-8 -*-
"""主题 token 定义（浅色 / 纯黑）。

从 main.py 中拆出，供主窗口、任务面板、进程面板等共享。
"""

THEMES = {
    "light": {
        "name": "浅色",
        "bg": "#eef0f3",
        "page": "#f5f6f8",
        "panel": "#ffffff",
        "surface": "#eef0f3",
        "border": "#e3e5e9",
        "chat_bg": "#ffffff",
        "text": "#1d1f24",
        "text_sec": "#8a9099",
        "accent": "#3478f6",
        "accent_hover": "#2f6ce0",
        "accent_text": "#ffffff",
        "bubble_user": "#3478f6",
        "bubble_user_text": "#ffffff",
        "bubble_assistant": "#ffffff",
        "code_bg": "#f2f4f7",
        "code_fg": "#24292f",
        "input_bg": "#ffffff",
        "input_fg": "#1d1f24",
        "selection": "#bcd6ff",
        "thinking": "#98a2b3",
        "tool": "#af52de",
        "error": "#ff3b30",
        "warning": "#ff9500",
        "success": "#34c759",
        "disabled": "#c3c8cf",
        # ---- 1.11.0 定版新增 token：悬停/提示/引用/时间戳 ----
        "hover": "#e6ecf7",          # 列表/菜单悬停（带品牌蓝倾向）
        "note": "#8a9099",           # 时间戳与系统提示文字
        "mention": "#3478f6",        # 用户名提及/引用高亮
        "quote_bg": "#f2f6fd",       # 引用块背景（品牌蓝极浅）
        "input_placeholder": "#a6adb8",
    },
    "dark": {
        "name": "纯黑",
        "bg": "#000000",
        "page": "#000000",
        "panel": "#000000",
        "surface": "#1c1c1c",
        "border": "#3a3a3a",
        "chat_bg": "#000000",
        "text": "#f2f3f5",
        "text_sec": "#a8adb5",
        "accent": "#0a84ff",
        "accent_hover": "#3d9bff",
        "accent_text": "#ffffff",
        "bubble_user": "#0a84ff",
        "bubble_user_text": "#ffffff",
        "bubble_assistant": "#000000",
        "code_bg": "#161616",
        "code_fg": "#d6dde6",
        "input_bg": "#000000",
        "input_fg": "#f2f3f5",
        "selection": "#2058d8",
        "thinking": "#8b93a1",
        "tool": "#bf5af2",
        "error": "#ff453a",
        "warning": "#ff9f0a",
        "success": "#30d158",
        "disabled": "#4a4d55",
        # ---- 1.11.0 定版新增 token ----
        "hover": "#232323",
        "note": "#8b93a1",
        "mention": "#0a84ff",
        "quote_bg": "#101418",
        "input_placeholder": "#5a5f68",
    },
}
