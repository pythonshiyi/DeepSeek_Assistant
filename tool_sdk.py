# -*- coding: utf-8 -*-
"""可扩展 Tool SDK：自定义工具模板生成 / 校验 / 文档生成。

供“非程序员”快速注册 HTTP 工具，并被 Agent 自动调用。
"""
import json
import logging
import os

logger = logging.getLogger("whaletalk.tool_sdk")

ALLOWED_TYPES = {"string", "integer", "number", "boolean", "array", "object"}


def validate_tool(tool):
    """校验单个自定义工具 schema。返回 (ok, error)。"""
    if not isinstance(tool, dict):
        return False, "工具必须是对象"
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else None
    if not fn:
        return False, "缺少 function 对象"
    name = str(fn.get("name") or "").strip()
    if not name or not name.replace("_", "").isalnum():
        return False, "工具名必须为字母/数字/下划线"
    endpoint = str(fn.get("endpoint") or "").strip()
    if not endpoint.startswith(("http://", "https://")):
        return False, "endpoint 必须是 http(s) 地址"
    params = fn.get("parameters") or {}
    props = params.get("properties") if isinstance(params, dict) else None
    if props is not None:
        if not isinstance(props, dict):
            return False, "parameters.properties 必须是对象"
        for pname, p in props.items():
            if not isinstance(p, dict):
                return False, f"参数 {pname} 必须是对象"
            ptype = p.get("type", "string")
            if ptype not in ALLOWED_TYPES:
                return False, f"参数 {pname} 类型 {ptype} 不支持"
    return True, ""


def generate_tool_template(name, description, endpoint, params_text="", method="POST"):
    """根据简单描述生成标准自定义工具 schema。"""
    name = str(name or "").strip()
    if not name:
        return None
    params_text = str(params_text or "")
    props = {}
    required = []
    for raw in params_text.split(","):
        p = raw.strip()
        if not p:
            continue
        props[p] = {"type": "string", "description": p}
        required.append(p)
    return {
        "type": "function",
        "function": {
            "name": name[:40],
            "description": str(description or "")[:200],
            "parameters": {"type": "object", "properties": props, "required": required},
            "endpoint": str(endpoint or "").strip(),
            "method": str(method or "POST").upper(),
        },
    }


def render_tool_docs(tools):
    """把自定义工具列表渲染为 Markdown 文档。"""
    lines = ["# 自定义工具文档\n"]
    if not tools:
        lines.append("（暂无自定义工具）")
        return "\n".join(lines)
    for t in tools:
        fn = t.get("function") if isinstance(t.get("function"), dict) else {}
        name = fn.get("name") or "?"
        desc = fn.get("description") or ""
        endpoint = fn.get("endpoint") or ""
        method = fn.get("method") or "POST"
        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        lines.append(f"## {name}")
        lines.append(f"- 描述：{desc}")
        lines.append(f"- 方法：{method}")
        lines.append(f"- 端点：{endpoint}")
        if props:
            lines.append("- 参数：")
            for p, info in props.items():
                ptype = info.get("type", "string") if isinstance(info, dict) else "string"
                lines.append(f"  - `{p}`：{ptype}")
        lines.append("")
    return "\n".join(lines)


def validate_user_tools(path):
    """校验 user_tools.json 全部条目，返回 [(名称, 错误)]。"""
    errors = []
    try:
        if not path or not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return [("<整体>", "内容必须是 JSON 数组")]
        for tool in data:
            if not isinstance(tool, dict):
                errors.append(("<非对象>", "条目不是对象"))
                continue
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
            name = str(fn.get("name") or "?")
            ok, err = validate_tool(tool)
            if not ok:
                errors.append((name, err))
    except Exception as e:
        errors.append(("<读取失败>", str(e)))
    return errors


def append_tool(path, tool):
    """把生成的自定义工具追加到 user_tools.json（存在则覆盖同名）。"""
    if not path:
        return False, "路径为空"
    existing = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    name = (tool.get("function") or {}).get("name")
    existing = [t for t in existing if not (isinstance(t, dict) and (t.get("function") or {}).get("name") == name)]
    existing.append(tool)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True, ""
    except Exception as e:
        return False, str(e)
