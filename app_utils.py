# -*- coding: utf-8 -*-
"""应用级小工具：布尔转换、空壳目录判断、清理、干净退出标记、隐私日志。

从 main.py 中拆出的纯函数/低依赖工具。
"""
import logging
import os
import shutil

_EMPTY_SHELL_CACHE = {"checked": False, "value": False}


def as_bool(v, default=False):
    """健壮布尔转换：手改配置写字符串 "false" 不再被误判为 True。"""
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return default


def is_empty_shell(path):
    """判断目录是否为空壳（仅含空子目录、无任何文件）。

    scandir + 栈遍历：第一层发现任何文件立即返回 False（对含大量文件的
    DATA_DIR 从 O(全部条目) 降为 O(第一层)）；结果缓存避免迁移/崩溃检测双遍历。
    """
    if _EMPTY_SHELL_CACHE["checked"]:
        return _EMPTY_SHELL_CACHE["value"]
    empty = True
    try:
        stack = [path]
        while stack:
            d = stack.pop()
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            empty = False
                            break
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                    except OSError:
                        continue
            if not empty:
                break
    except Exception:
        empty = False
    _EMPTY_SHELL_CACHE.update(checked=True, value=empty)
    return empty


def delete_target(path, kind):
    """删除文件或目录内容，返回删除的文件数（失败静默跳过）。"""
    count = 0
    try:
        if kind == "file":
            if os.path.exists(path):
                os.remove(path)
                count = 1
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for f in files:
                    try:
                        os.remove(os.path.join(root, f))
                        count += 1
                    except Exception:
                        pass
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except Exception:
                        pass
    except Exception:
        logging.exception("清理失败: %s", path)
    return count


def write_clean_exit_flag(flag_path):
    try:
        os.makedirs(os.path.dirname(flag_path) or ".", exist_ok=True)
        with open(flag_path, "w", encoding="utf-8") as f:
            f.write("ok")
        return True
    except Exception:
        return False


def apply_privacy_logging(privacy, log_dir=None):
    """隐私模式：彻底移除文件日志（WARNING/ERROR 也不再落盘），关闭时恢复。"""
    root = logging.getLogger()
    fh = [
        h for h in root.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    if privacy:
        for h in fh:
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)
    elif not fh and log_dir:
        try:
            from logging.handlers import RotatingFileHandler

            handler = RotatingFileHandler(
                os.path.join(log_dir, "assistant.log"),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            root.addHandler(handler)
        except Exception:
            pass
