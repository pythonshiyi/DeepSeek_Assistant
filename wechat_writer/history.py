# -*- coding: utf-8 -*-
"""历史主题索引：已写主题/日期/标题/关键词，供选题与质检去重。"""
import json
import logging
import os

logger = logging.getLogger("wechat_writer.history")

DEFAULT_HISTORY = {"items": []}


def _load(path):
    if not path or not os.path.exists(path):
        return json.loads(json.dumps(DEFAULT_HISTORY))
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return json.loads(json.dumps(DEFAULT_HISTORY))
        return data
    except Exception:
        logger.exception("读取历史索引失败")
        return json.loads(json.dumps(DEFAULT_HISTORY))


def _save(path, data):
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        logger.exception("保存历史索引失败")
        return False


def add(path, record):
    """追加一条记录 {"date","topic","title","keywords","path"}。"""
    data = _load(path)
    items = data["items"]
    items.append({k: str(v or "") for k, v in record.items()})
    if len(items) > 1000:
        del items[: len(items) - 1000]
    data["items"] = items
    return _save(path, data)


def recent(path, n=14):
    """最近 N 条记录（时间倒序）。"""
    return _load(path)["items"][-n:][::-1]


def topics(path, n=14):
    """最近 N 条的主题词列表（供查重）。"""
    return [str(r.get("topic") or r.get("title") or "") for r in recent(path, n)]


def titles(path, n=14):
    return [str(r.get("title") or "") for r in recent(path, n)]


def clear(path):
    """清空历史（测试/重置用）。"""
    return _save(path, json.loads(json.dumps(DEFAULT_HISTORY)))
