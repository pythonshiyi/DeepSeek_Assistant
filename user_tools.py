# -*- coding: utf-8 -*-
"""用户自定义工具加载（mtime+size 缓存）。

从 main.py 中拆出，供工具中心/请求构造复用。
"""
import json
import logging
import os

logger = logging.getLogger("whaletalk.user_tools")

DEFAULT_USER_TOOLS_PATH = None

_USER_TOOLS_CACHE = {}  # (mtime, size) -> tools 列表（load_user_tools 缓存）


def load_user_tools(path=None):
    """读取用户自定义工具配置，返回 OpenAI function schema 列表。

    按文件 mtime+size 缓存：_worker 每轮请求都会调用，避免反复读盘+JSON 解析。
    """
    if path is None:
        path = DEFAULT_USER_TOOLS_PATH
    try:
        st = os.stat(path)
        key = (st.st_mtime, st.st_size)
    except OSError:
        return []
    cached = _USER_TOOLS_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tools = []
        for t in data:
            if not isinstance(t, dict):
                continue
            fn = t.get("function") if isinstance(t.get("function"), dict) else t
            name = str(fn.get("name", "")).strip()
            desc = str(fn.get("description", "")).strip()
            endpoint = str(fn.get("endpoint", "")).strip()
            if not (name and endpoint):
                continue
            params = fn.get("params")
            if not isinstance(params, list):
                raw_params = fn.get("parameters")
                props = {}
                if isinstance(raw_params, dict):
                    props = raw_params.get("properties") or {}
                params = [{"name": str(k)} for k in props if isinstance(props, dict)]
            props = {}
            required = []
            if isinstance(params, list):
                for p in params:
                    if not isinstance(p, dict):
                        continue
                    pname = str(p.get("name", "")).strip()
                    if not pname:
                        continue
                    props[pname] = {
                        "type": "string",
                        "description": str(p.get("description", "") or ""),
                    }
                    required.append(pname)
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc,
                        "parameters": {"type": "object", "properties": props, "required": required},
                        "endpoint": endpoint,
                        "method": str(fn.get("method", "POST") or "POST"),
                    },
                }
            )
        _USER_TOOLS_CACHE[key] = tools
        return tools
    except Exception:
        logger.exception("读取自定义工具失败")
        return []


def clear_cache():
    """清空自定义工具缓存（安装/卸载/编辑后调用）。"""
    _USER_TOOLS_CACHE.clear()
