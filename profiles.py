# -*- coding: utf-8 -*-
"""Profile 多账号配置读写（API Key DPAPI 加密）。

从 main.py 中拆出，供 Profile 管理对话框与客户端切换复用。
"""
import json
import logging
import os

import crypto

logger = logging.getLogger("whaletalk.profiles")

DEFAULT_PROFILES_PATH = None


def load_profiles(path=None):
    """读取 Profile 列表，返回 {"profiles": {name: cfg}, "current": name}。

    密文为 "dpapi:" 前缀（crypto.decrypt）；旧版明文自动兼容（无前缀原样返回）。
    """
    if path is None:
        path = DEFAULT_PROFILES_PATH
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            profiles = data.get("profiles") or {}
            if isinstance(profiles, dict):
                cleaned = {}
                for name, p in profiles.items():
                    if isinstance(p, dict) and name:
                        cleaned[str(name)] = {
                            "api_key": crypto.decrypt(str(p.get("api_key", "") or "")),
                            "base_url": str(p.get("base_url", "") or ""),
                            "model": str(p.get("model", "") or ""),
                        }
                return {"profiles": cleaned, "current": str(data.get("current") or "")}
    except Exception:
        logger.exception("读取 Profile 失败")
    return {"profiles": {}, "current": ""}


def save_profiles(data, path=None):
    """保存 Profile 列表；api_key 一律经 DPAPI 加密，绝不明文落盘。

    加密失败（CryptError）时整次保存失败（fail-closed），磁盘保持旧文件。
    """
    if path is None:
        path = DEFAULT_PROFILES_PATH
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        encrypted = {}
        for name, p in (data.get("profiles") or {}).items():
            encrypted[str(name)] = {
                "api_key": crypto.encrypt(str(p.get("api_key", "") or "")),
                "base_url": str(p.get("base_url", "") or ""),
                "model": str(p.get("model", "") or ""),
            }
        out = {"profiles": encrypted, "current": str(data.get("current") or "")}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        logger.exception("保存 Profile 失败（api_key 未落盘）")
        return False
