import json
import logging
import os

DEFAULT_PROMPTS = [
    {
        "name": "中译英",
        "text": "请将以下内容翻译成地道的英文，保持语气与格式：\n\n{{TEXT}}",
    },
    {
        "name": "英译中",
        "text": "请将以下内容翻译成自然流畅的中文，保持语气与格式：\n\n{{TEXT}}",
    },
    {
        "name": "代码审查",
        "text": "请审查以下代码，指出潜在 bug、安全问题和性能隐患，并给出修改建议：\n\n{{TEXT}}",
    },
    {
        "name": "解释代码",
        "text": "请逐段解释以下代码的作用与原理，适合初学者理解：\n\n{{TEXT}}",
    },
    {
        "name": "生成单元测试",
        "text": "请为以下代码编写完整的单元测试（使用 unittest 风格），覆盖正常与边界情况：\n\n{{TEXT}}",
    },
    {
        "name": "周报助手",
        "text": "根据以下工作内容要点，生成一份结构清晰的中文周报：\n\n{{TEXT}}",
    },
    {
        "name": "文章润色",
        "text": "请润色以下文字，使其更流畅、专业、简洁，不要改变原意：\n\n{{TEXT}}",
    },
    {
        "name": "SQL 优化",
        "text": "请分析并优化以下 SQL，给出改写后的语句和优化原因：\n\n{{TEXT}}",
    },
]


def load_prompts(path):
    """读取提示词库，返回 [{"name", "text"}]。文件缺失或损坏时返回默认模板。"""
    if not path or not os.path.exists(path):
        return [dict(p) for p in DEFAULT_PROMPTS]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("格式错误")
        items = []
        for p in data:
            if isinstance(p, dict) and p.get("name") and p.get("text"):
                item = {"name": str(p["name"]), "text": str(p["text"])}
                # 来源标记（插件技能）：插件卸载时据此识别
                if p.get("_source"):
                    item["_source"] = str(p["_source"])
                items.append(item)
        if not items:
            raise ValueError("为空")
        return items
    except Exception:
        logging.exception("读取提示词库失败，使用默认模板")
        return [dict(p) for p in DEFAULT_PROMPTS]


def save_prompts(path, prompts):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prompts, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        logging.exception("保存提示词库失败")
        return False


def apply_template(template, text):
    """将输入内容填入模板（替换 {{TEXT}} 占位符）。"""
    if "{{TEXT}}" in template:
        return template.replace("{{TEXT}}", text or "")
    if text:
        return template.rstrip() + "\n\n" + text
    return template
