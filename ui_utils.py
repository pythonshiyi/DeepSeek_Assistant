# -*- coding: utf-8 -*-
"""UI 工具函数（依赖 Tkinter，但不依赖具体业务）。

从 main.py 中拆出，供菜单/弹窗/右键菜单等复用。
"""
import tkinter as tk


def destroy_menu(menu):
    """安全销毁临时菜单（tk_popup 非阻塞返回后菜单仍在显示，必须等 Unmap 后销毁）。

    递归销毁 cascade 子菜单：Tk 销毁父菜单不会自动销毁子菜单，
    每次右键弹出「快速动作」子菜单后必须一并回收，否则 Tcl 菜单 widget 持续泄漏。
    """
    try:
        end = menu.index("end")
        if end is not None:
            for i in range(end + 1):
                try:
                    sub = menu.entrycget(i, "menu")
                except tk.TclError:
                    sub = None
                if sub:
                    destroy_menu(sub)
    except tk.TclError:
        pass
    try:
        menu.destroy()
    except tk.TclError:
        pass
