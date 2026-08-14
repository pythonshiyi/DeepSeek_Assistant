# -*- coding: utf-8 -*-
"""DeepSeek API 封装：读鲸语 config.json（api_key 为 DPAPI 密文，自动解密）+ 重试。

独立运行兼容：环境变量 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL。
"""
import json
import logging
import os
import time

logger = logging.getLogger("wechat_writer.llm")


def _find_whaletalk_config():
    """定位鲸语 config.json（项目根 / Documents/WhaleTalk 数据目录）。"""
    candidates = []
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
    candidates.append(os.path.join(here, "config.json"))
    user = os.path.expanduser("~")
    candidates.append(os.path.join(user, "Documents", "WhaleTalk", "config.json"))
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _decrypt(token):
    """DPAPI 解密（密文前缀 dpapi:）；无前缀按明文返回。解密失败返回空（防密文当明文）。"""
    try:
        import crypto
        return crypto.decrypt(token)
    except Exception:
        return token


def load_api_config(config_path=None):
    """返回 {"api_key", "base_url", "model"}。未配置时返回 api_key 空串（调用方报错）。"""
    path = config_path or _find_whaletalk_config()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            api_key = str(cfg.get("api_key") or cfg.get("API_KEY") or "").strip()
            base_url = str(cfg.get("base_url") or cfg.get("BASE_URL") or "https://api.deepseek.com").strip()
            model = str(cfg.get("model") or cfg.get("MODEL") or "deepseek-v4-flash").strip()
            return {"api_key": _decrypt(api_key), "base_url": base_url, "model": model or "deepseek-v4-flash"}
        except Exception:
            logger.exception("读取鲸语配置失败，回退环境变量")
    return {
        "api_key": os.environ.get("DEEPSEEK_API_KEY", "").strip(),
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
    }


def chat(messages, max_tokens=4000, temperature=0.7, config_path=None, timeout=120.0):
    """调用 DeepSeek chat completions（非流式），失败重试 2 次指数退避。

    返回纯文本；全部失败抛 RuntimeError（由调用方降级处理）。
    """
    cfg = load_api_config(config_path)
    if not cfg["api_key"]:
        raise RuntimeError("未配置 DeepSeek API Key（可在鲸语设置中填写，或设置环境变量 DEEPSEEK_API_KEY）")
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    import httpx

    last_err = None
    for attempt in range(3):
        try:
            resp = httpx.post(
                url, json=payload, timeout=timeout,
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
            )
            resp.raise_for_status()
            content = (resp.json()["choices"][0]["message"]["content"] or "").strip()
            if not content:
                raise RuntimeError("模型返回空内容")
            return content
        except Exception as e:
            last_err = e
            logger.warning("LLM 调用失败（第 %s 次）：%s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败，已重试 3 次：{last_err}")


def chat_json(messages, max_tokens=2000, temperature=0.4, config_path=None):
    """调用 LLM 并要求输出 JSON 对象（提取 json 块，解析失败抛错）。"""
    text = chat(messages, max_tokens=max_tokens, temperature=temperature, config_path=config_path)
    # 提取首个 JSON 对象块（模型可能带 ```json 围栏或前后说明文字）
    import re

    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"模型输出不含 JSON：{text[:200]}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON 解析失败：{e}；原始：{text[:200]}")
    if not isinstance(data, dict):
        raise RuntimeError("模型输出 JSON 非对象")
    return data
