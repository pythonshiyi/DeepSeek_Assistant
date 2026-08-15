# -*- coding: utf-8 -*-
"""本地 JSON 数据存取（最近产物/模式/失败/任务日志/记忆/调度）。

从 main.py 中拆出，统一使用 persistence.atomic_json_write 保证原子写。
"""
import json
import logging
import os

from persistence import atomic_json_write

logger = logging.getLogger("whaletalk.stores")


def load_recent(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(x) for x in data if str(x).strip()]
    except Exception:
        logger.exception("读取最近产物失败")
    return []


def save_recent(path, recent):
    return atomic_json_write(path, recent)


def load_patterns(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        logger.exception("读取成功模式失败")
    return []


def save_patterns(path, pats):
    return atomic_json_write(path, pats)


def load_failures(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        logger.exception("读取失败模式失败")
    return []


def save_failures(path, items):
    return atomic_json_write(path, items, indent=1)


def append_failures(path, new_items, max_failures=50):
    """追加失败记录：同 (工具, 错误摘要前 50 字符) 去重并更新时间戳，上限 50 条。"""
    if not new_items:
        return
    try:
        items = load_failures(path)
        for it in new_items:
            key = (str(it.get("tool") or ""), str(it.get("error") or "")[:50])
            replaced = False
            for old in items:
                if (str(old.get("tool") or ""), str(old.get("error") or "")[:50]) == key:
                    old["ts"] = it["ts"]
                    replaced = True
                    break
            if not replaced:
                items.append(it)
        if len(items) > max_failures:
            del items[: len(items) - max_failures]
        save_failures(path, items)
    except Exception:
        logger.exception("记录失败模式失败")


def failure_patterns_text(path):
    """已知失败模式注入（最近 3 条，固定格式缓存友好）。"""
    try:
        items = load_failures(path)
        if not items:
            return ""
        lines = ["[已知失败模式] 以下工具调用曾失败（遇到同类情况请规避或改用其他方式）："]
        for it in items[-3:]:
            tool = str(it.get("tool") or "?")
            err = str(it.get("error") or "")[:100]
            if err:
                lines.append(f"- {tool}：{err}")
        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception:
        logger.exception("构建失败模式文本失败")
        return ""


def load_tasklog(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("tasks", [])
                return data
    except Exception:
        logger.exception("读取项目任务记录失败")
    return {"tasks": []}


def save_tasklog(path, data):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        return atomic_json_write(path, data, indent=2)
    except Exception:
        logger.exception("保存项目任务记录失败")
        return False


def load_memory(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("enabled", False)
                data.setdefault("facts", [])
                return data
    except Exception:
        logger.exception("读取长期记忆失败")
    return {"enabled": False, "facts": []}


def save_memory(path, data):
    return atomic_json_write(path, data, indent=2)


def load_schedules(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        logger.exception("读取定时任务失败")
    return []


def save_schedules(path, schedules):
    return atomic_json_write(path, schedules, indent=2)
