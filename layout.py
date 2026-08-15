# -*- coding: utf-8 -*-
"""布局定版规范（Layout Specification v1.0）。

所有模块的默认尺寸、范围与档位在此统一定义，禁止散落魔法数字。
"""

LAYOUT = {
    "window_w": 1280,           # 默认窗口宽
    "window_h": 820,            # 默认窗口高（16:10 内容比例）
    "window_min_w": 880,        # 最小窗口宽（小于紧凑阈值：窄窗口自动让位给聊天区）
    "window_min_h": 620,        # 最小窗口高
    "menu_h": 34,               # 菜单栏高度
    "status_h": 30,             # 状态栏高度
    "sidebar_default": 260,     # 侧栏默认宽
    "sidebar_min": 200,         # 侧栏最小宽
    "sidebar_max": 420,         # 侧栏最大宽
    "panel_default": 280,       # 设置面板默认宽
    "panel_min": 240,           # 设置面板最小宽
    "panel_max": 480,           # 设置面板最大宽
    "panel_files_w": 460,       # 文件视图宽度
    "content_min": 560,         # 聊天内容列最小宽
    "content_max": 860,         # 聊天内容列最大宽
    "content_margin": 120,      # 聊天列两侧总留白（聊天列 = tw - margin）
    "compact_panel_w": 1000,    # 窗口 <此宽度收起右侧面板
    "compact_sidebar_w": 1120,  # 窗口 <此宽度收起侧栏（窄窗口优先保聊天区）
    "dialog_s": 420,            # 对话框窄档
    "dialog_m": 520,            # 对话框中档
    "dialog_l": 640,            # 对话框宽档
    "dialog_h_s": 300,          # 对话框矮档
    "dialog_h_m": 420,          # 对话框中档高
    "dialog_h_l": 460,          # 对话框高档
    "dialog_h_editor": 540,     # 编辑器类高度
    "dialog_h_editor_l": 620,   # 大编辑器类高度
}
