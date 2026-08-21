# -*- coding: utf-8 -*-
"""会话导出扩展：HTML / JSONL 格式（纯增量，不触碰现有 md/txt 导出逻辑）。"""
import html
import json
import logging
import os


def _image_ref_lines(msg):
    """user 消息的图片附件 → 导出可读的 [图片] 引用行列表。"""
    lines = []
    for p in msg.get("images") or []:
        p = str(p)
        if p.lower().startswith(("http://", "https://")):
            lines.append(f"[图片] {p}")
        else:
            lines.append(f"[图片] {os.path.basename(p)}")
    return lines


def build_markdown(messages, usage_total, session_start, cfg,
                   app_name="鲸语", app_name_en="WhaleTalk"):
    """把消息快照构建为 Markdown 导出文本（纯函数，供后台线程导出）。

    与 AssistantApp.build_markdown 等价，但接收快照参数而非访问会话状态。
    """
    lines = [
        f"# {app_name} {app_name_en} 会话记录",
        "",
        f"- 开始时间: {session_start:%Y-%m-%d %H:%M:%S}",
        f"- 模型: {cfg.get('model', '')}",
        f"- 场景: {cfg.get('scenario', '')} | 思考模式: {cfg.get('thinking', '')}",
        f"- 累计 Token: 输入 {usage_total.get('prompt', 0)} / 输出 {usage_total.get('completion', 0)}",
        "",
        "---",
    ]
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "user":
            content = (m.get("content") or "")
            img_lines = _image_ref_lines(m)
            if img_lines:
                content = content + "\n" + "\n".join(img_lines)
            lines += ["", "## 用户", "", content]
        elif role == "assistant":
            if m.get("reasoning_content"):
                lines += ["", "## 助手（思考过程）", "", "```text", m["reasoning_content"], "```"]
            if m.get("content"):
                lines += ["", "## 助手", "", m["content"]]
            for tc in m.get("tool_calls") or ():
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    lines += [
                        "",
                        f"## 工具调用: {fn.get('name')}",
                        "",
                        "```json",
                        fn.get("arguments") or "",
                        "```",
                    ]
        elif role == "tool":
            lines += ["", f"> 工具结果: {m.get('content')}", ""]
    return "\n".join(lines)


def export_html(messages, path, title="鲸语 WhaleTalk 会话记录", model="", scenario=""):
    """将会话消息渲染为自包含的单文件 HTML（内联样式，可离线打开）。"""
    import os

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    except Exception:
        pass
    parts = []
    parts.append(
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>"
        "body{font-family:'Microsoft YaHei UI',sans-serif;max-width:860px;"
        "margin:0 auto;padding:24px;background:#f5f6f8;color:#1d1f24;}"
        "h1{font-size:22px;}.meta{color:#8a9099;font-size:13px;margin-bottom:20px;}"
        ".msg{background:#fff;border:1px solid #e3e5e9;border-radius:10px;"
        "padding:12px 16px;margin:10px 0;white-space:pre-wrap;line-height:1.6;}"
        ".user{background:#3478f6;color:#fff;border-color:#3478f6;margin-left:40px;}"
        ".time{color:#8a9099;font-size:12px;margin:14px 0 4px;}"
        ".tool{background:#f7f0fa;border-color:#e6d5ee;color:#6b4a7a;font-size:13px;}"
        "code{background:#f2f4f7;border-radius:4px;padding:1px 5px;"
        "font-family:Consolas,monospace;font-size:13px;}"
        "pre{background:#f2f4f7;border-radius:6px;padding:10px 14px;overflow:auto;}"
        "pre code{background:none;padding:0;}</style></head><body>"
    )
    parts.append(f"<h1>{html.escape(title)}</h1>")
    meta = []
    if model:
        meta.append(f"模型：{html.escape(model)}")
    if scenario:
        meta.append(f"场景：{html.escape(scenario)}")
    parts.append(f"<div class='meta'>{' ｜ '.join(meta)}</div>")

    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            continue
        if role == "user":
            content = (m.get("content") or "")
            img_lines = _image_ref_lines(m)
            if img_lines:
                content = content + "\n" + "\n".join(img_lines)
            parts.append("<div class='time'>用户</div>")
            parts.append(f"<div class='msg user'>{_render_md_inline(content)}</div>")
        elif role == "assistant":
            reasoning = m.get("reasoning_content") or ""
            if reasoning:
                parts.append("<div class='time'>助手 · 思考过程</div>")
                parts.append(f"<div class='msg tool'>{html.escape(reasoning)}</div>")
            parts.append("<div class='time'>助手</div>")
            parts.append(f"<div class='msg'>{_render_md_inline(content)}</div>")
        elif role == "tool":
            parts.append(f"<div class='msg tool'>工具结果：{html.escape(str(content))}</div>")
    parts.append("</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path


def _render_md_inline(text):
    """极简 Markdown 内联渲染（代码围栏→pre，行内代码/粗体/链接）。"""
    import re as _re

    text = html.escape(text or "")
    parts = []
    in_code = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            if not in_code:
                parts.append("<pre><code>")
                in_code = True
            else:
                parts.append("</code></pre>")
            in_code = False
            continue
        if in_code:
            parts.append(line)
            continue
        line = _re.sub(r"`([^`]+?)`", r"<code>\1</code>", line)
        line = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        parts.append(line)
    if in_code:
        parts.append("</code></pre>")
    return "\n".join(parts)


def export_jsonl(messages, path):
    """将会话消息导出为 JSONL（每行一个 JSON 对象，兼容主流工具导入）。"""
    import os

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    except Exception:
        pass
    with open(path, "w", encoding="utf-8") as f:
        for m in messages:
            if m.get("role") == "system":
                continue
            try:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
            except (TypeError, ValueError):
                logging.warning("JSONL 导出跳过不可序列化消息")
    return path
