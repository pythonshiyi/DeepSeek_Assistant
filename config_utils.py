# -*- coding: utf-8 -*-
"""配置加载/规范化/保存。

从 main.py 中拆出，集中处理 config.json 的读取、默认值合并、字段钳制与
敏感字段 DPAPI 加解密。
"""
import json
import logging
import os

import crypto
import deepseek_client as _dc
from deepseek_client import (
    DEFAULT_BASE_URL,
    SCENARIOS,
    THINKING_MODES,
    TOOLS,
)
from config_defaults import (
    BUILTIN_TOOL_NAMES,
    DEFAULT_CONFIG,
    DEFAULT_SYSTEM_PROMPT,
)
from app_utils import as_bool
from layout import LAYOUT
from themes import THEMES
from user_tools import load_user_tools

logger = logging.getLogger("whaletalk.config_utils")

DEFAULT_CONFIG_PATH = None


def normalize_config(cfg):
    try:
        cfg["max_tokens"] = int(cfg.get("max_tokens", 16384))
    except (TypeError, ValueError):
        cfg["max_tokens"] = 16384
    cfg["max_tokens"] = max(1024, min(393216, cfg["max_tokens"]))  # V4 正式版最大输出 384K

    seed = str(cfg.get("seed", "")).strip()
    if seed:
        try:
            seed = str(int(seed))
        except ValueError:
            seed = ""
    cfg["seed"] = seed

    if cfg.get("thinking") not in THINKING_MODES:
        cfg["thinking"] = "high"
    if cfg.get("scenario") not in SCENARIOS:
        cfg["scenario"] = "通用"

    base_url = str(cfg.get("base_url", "")).strip()
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        base_url = DEFAULT_BASE_URL
    cfg["base_url"] = base_url

    cfg["model"] = str(cfg.get("model", "deepseek-v4-flash")).strip() or "deepseek-v4-flash"
    # 支持任意 OpenAI 兼容模型名（Profile 自定义端点场景），不再强制回退内置列表
    api_key = cfg.get("api_key")
    cfg["api_key"] = "" if api_key is None else str(api_key).strip()
    cfg["tools_enabled"] = as_bool(cfg.get("tools_enabled", True), True)
    cfg["restore_session"] = as_bool(cfg.get("restore_session", True), True)
    cfg["privacy_mode"] = as_bool(cfg.get("privacy_mode", False))
    cfg["check_update"] = as_bool(cfg.get("check_update", False))
    cfg["welcomed"] = as_bool(cfg.get("welcomed", False))
    cfg["browser_headless"] = as_bool(cfg.get("browser_headless", True), True)
    try:
        cfg["custom_temperature"] = max(0.0, min(2.0, float(cfg.get("custom_temperature", 1.0))))
    except (TypeError, ValueError):
        cfg["custom_temperature"] = 1.0
    try:
        cfg["custom_top_p"] = max(0.0, min(1.0, float(cfg.get("custom_top_p", 1.0))))
    except (TypeError, ValueError):
        cfg["custom_top_p"] = 1.0
    try:
        cfg["max_tool_rounds"] = max(1, min(100, int(cfg.get("max_tool_rounds", 100))))
    except (TypeError, ValueError):
        cfg["max_tool_rounds"] = 100
    try:
        cfg["monthly_budget"] = max(0.0, float(cfg.get("monthly_budget", 0.0)))
    except (TypeError, ValueError):
        cfg["monthly_budget"] = 0.0
    cfg["block_on_budget"] = as_bool(cfg.get("block_on_budget", False))
    try:
        all_tool_names = [t["function"]["name"] for t in TOOLS]
    except (KeyError, TypeError):
        all_tool_names = []
    try:
        user_tool_names = [t["function"]["name"] for t in load_user_tools()]
    except Exception:
        user_tool_names = []
    valid_tool_names = set(all_tool_names) | set(user_tool_names)
    raw_tools = cfg.get("enabled_tools")
    if isinstance(raw_tools, list):
        cfg["enabled_tools"] = [n for n in raw_tools if n in valid_tool_names]
        # 升级合并：新版本新增的安全基础工具自动启用（旧配置无感知升级）
        for n in BUILTIN_TOOL_NAMES:
            if n in valid_tool_names and n not in cfg["enabled_tools"]:
                cfg["enabled_tools"].append(n)
    else:
        cfg["enabled_tools"] = list(BUILTIN_TOOL_NAMES)
    cfg["system_prompt"] = str(cfg.get("system_prompt", DEFAULT_SYSTEM_PROMPT))
    # call_api 内网白名单：用户显式放行的本地/内网服务主机（精确匹配，建议 IP）
    try:
        raw_allow = cfg.get("call_api_allowed_hosts") or []
        if isinstance(raw_allow, list):
            _dc.CALL_API_ALLOWED_HOSTS = [
                str(h).strip() for h in raw_allow if str(h).strip()
            ]
    except Exception:
        _dc.CALL_API_ALLOWED_HOSTS = []

    try:
        cfg["max_context_chars"] = max(10000, int(cfg.get("max_context_chars", 500000)))
    except (TypeError, ValueError):
        cfg["max_context_chars"] = 500000
    try:
        cfg["max_context_tokens"] = max(8000, min(900000, int(cfg.get("max_context_tokens", 400000))))
    except (TypeError, ValueError):
        cfg["max_context_tokens"] = 400000
    try:
        cfg["min_kept_turns"] = max(3, min(500, int(cfg.get("min_kept_turns", 8))))
    except (TypeError, ValueError):
        cfg["min_kept_turns"] = 8
    try:
        cfg["timeout"] = max(10, min(600, float(cfg.get("timeout", 120))))
    except (TypeError, ValueError):
        cfg["timeout"] = 120
    try:
        cfg["font_size"] = max(8, min(18, int(cfg.get("font_size", 10))))
    except (TypeError, ValueError):
        cfg["font_size"] = 10
    cfg["font_family"] = str(cfg.get("font_family", "Microsoft YaHei UI") or "Microsoft YaHei UI").strip() or "Microsoft YaHei UI"
    if cfg.get("message_density") not in ("compact", "comfort", "loose"):
        cfg["message_density"] = "comfort"
    try:
        cfg["input_height"] = max(2, min(14, int(cfg.get("input_height", 4))))
    except (TypeError, ValueError):
        cfg["input_height"] = 4
    try:
        cfg["input_height_px"] = max(0, min(3000, int(cfg.get("input_height_px", 0))))
    except (TypeError, ValueError):
        cfg["input_height_px"] = 0
    try:
        cfg["sidebar_width"] = max(LAYOUT["sidebar_min"], min(LAYOUT["sidebar_max"], int(cfg.get("sidebar_width", LAYOUT["sidebar_default"]))))
    except (TypeError, ValueError):
        cfg["sidebar_width"] = LAYOUT["sidebar_default"]
    try:
        cfg["panel_width"] = max(LAYOUT["panel_min"], min(LAYOUT["panel_max"], int(cfg.get("panel_width", LAYOUT["panel_default"]))))
    except (TypeError, ValueError):
        cfg["panel_width"] = LAYOUT["panel_default"]
    # 自定义主题：仅接受 dict 值，且名称不覆盖内置主题
    try:
        custom_themes = cfg.get("custom_themes") or {}
        if not isinstance(custom_themes, dict):
            custom_themes = {}
        cfg["custom_themes"] = {
            str(k): dict(v) for k, v in custom_themes.items()
            if isinstance(v, dict) and str(k) not in THEMES
        }
    except Exception:
        cfg["custom_themes"] = {}
    if cfg.get("theme") not in THEMES and cfg.get("theme") not in cfg.get("custom_themes", {}):
        cfg["theme"] = "light"
    try:
        shortcuts = cfg.get("shortcuts") or {}
        cfg["shortcuts"] = {
            str(k): str(v) for k, v in shortcuts.items() if isinstance(v, (str, int))
        } if isinstance(shortcuts, dict) else {}
    except Exception:
        cfg["shortcuts"] = {}
    cfg["json_output"] = as_bool(cfg.get("json_output", False))
    cfg["beta_api"] = as_bool(cfg.get("beta_api", False))
    cfg["peak_warning"] = as_bool(cfg.get("peak_warning", True), True)
    cfg["full_auto"] = as_bool(cfg.get("full_auto", False))
    cfg["suggestions_enabled"] = as_bool(cfg.get("suggestions_enabled", True), True)
    cfg["pure_chat"] = as_bool(cfg.get("pure_chat", False))
    try:
        cfg["evolution_reminder_days"] = max(0, min(90, int(cfg.get("evolution_reminder_days", 7))))
    except (TypeError, ValueError):
        cfg["evolution_reminder_days"] = 7
    try:
        cfg["fold_early_threshold"] = max(0, min(20000, int(cfg.get("fold_early_threshold", 0))))
    except (TypeError, ValueError):
        cfg["fold_early_threshold"] = 0
    try:
        cfg["inbound_port"] = max(0, min(65535, int(cfg.get("inbound_port", 0))))
    except (TypeError, ValueError):
        cfg["inbound_port"] = 0
    cfg["inbound_token"] = str(cfg.get("inbound_token", "") or "").strip()
    cfg["image_api_key"] = str(cfg.get("image_api_key", "") or "").strip()
    cfg["image_base_url"] = str(cfg.get("image_base_url", "") or "").strip()
    cfg["image_model"] = str(cfg.get("image_model", "gpt-image-1")).strip() or "gpt-image-1"
    cfg["vision_self_review"] = as_bool(cfg.get("vision_self_review", False))
    cfg["minimize_to_tray"] = as_bool(cfg.get("minimize_to_tray", False))
    cfg["autostart"] = as_bool(cfg.get("autostart", False))
    cfg["strict_tools"] = as_bool(cfg.get("strict_tools", False))
    cfg["update_url"] = str(cfg.get("update_url", "") or "").strip()
    return cfg


def load_config(config_path=None):
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    cfg = dict(DEFAULT_CONFIG)
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.update({k: data[k] for k in data if k in cfg})
        except Exception as e:
            logger.error("加载配置失败: %s", e)
    cfg["api_key"] = crypto.decrypt(cfg.get("api_key", ""))
    cfg["inbound_token"] = crypto.decrypt(cfg.get("inbound_token", ""))
    cfg["image_api_key"] = crypto.decrypt(cfg.get("image_api_key", ""))
    return normalize_config(cfg)


def save_config(cfg, config_path=None):
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    try:
        data = dict(cfg)
        try:
            data["api_key"] = crypto.encrypt(cfg.get("api_key", ""))
        except crypto.CryptError:
            # 加密失败：从磁盘保留原密文，绝不写明文、绝不静默删除 api_key
            old_key = None
            try:
                if config_path and os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        old_key = json.load(f).get("api_key")
            except Exception:
                pass
            if old_key:
                data["api_key"] = old_key
            else:
                data.pop("api_key", None)
            logger.error("API Key 加密失败，已保留磁盘原密文，请检查系统环境")
        for secret_key in ("inbound_token", "image_api_key"):
            try:
                data[secret_key] = crypto.encrypt(cfg.get(secret_key, ""))
            except crypto.CryptError:
                old_secret = None
                try:
                    if config_path and os.path.exists(config_path):
                        with open(config_path, "r", encoding="utf-8") as f:
                            old_secret = json.load(f).get(secret_key)
                except Exception:
                    pass
                if old_secret:
                    data[secret_key] = old_secret
                else:
                    data.pop(secret_key, None)
                logger.error("%s 加密失败，已保留磁盘原密文", secret_key)
        tmp = config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, config_path)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    except Exception as e:
        logger.error("保存配置失败: %s", e)
