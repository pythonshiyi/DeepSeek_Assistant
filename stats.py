import json
import logging
import os
import threading
from datetime import date

PRICING = {
    # 元 / 百万 tokens（2026-07 官方价格：缓存未命中输入 / 缓存命中 / 输出）
    "deepseek-v4-flash": {"prompt": 1.0, "completion": 2.0, "cache_hit": 0.02},
    "deepseek-v4-pro": {"prompt": 3.0, "completion": 6.0, "cache_hit": 0.025},
}

DEFAULT_PRICE = {"prompt": 1.0, "completion": 2.0, "cache_hit": 0.02}

_EMPTY_DAY = {"prompt": 0, "completion": 0, "cache_hit": 0, "cache_miss": 0}
_LOCK = threading.Lock()


def empty_day():
    """返回空 usage dict（公共只读接口）。"""
    return dict(_EMPTY_DAY)


def pricing():
    """返回定价表深拷贝（公共只读接口，防外部修改单价）。"""
    return {k: dict(v) for k, v in PRICING.items()}


def load_stats(path):
    """读取统计文件，返回 {day: {model: usage}}。文件损坏时返回空。"""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        cleaned = {}
        for day, models in data.items():
            if not isinstance(models, dict):
                continue
            cleaned[day] = {}
            for model, usage in models.items():
                if not isinstance(usage, dict):
                    continue
                try:
                    cleaned[day][model] = {
                        "prompt": int(usage.get("prompt", 0) or 0),
                        "completion": int(usage.get("completion", 0) or 0),
                        "cache_hit": int(usage.get("cache_hit", 0) or 0),
                        "cache_miss": int(usage.get("cache_miss", 0) or 0),
                    }
                except (TypeError, ValueError):
                    # 单条脏数据跳过，不丢弃整份统计
                    logging.warning("统计条目损坏已跳过: %s/%s", day, model)
                    continue
        return cleaned
    except Exception:
        logging.exception("读取用量统计失败")
        return {}


def save_stats(path, data):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)  # 原子替换：读者永远看到完整文件（旧版或新版）
    except Exception:
        logging.exception("保存用量统计失败")


def record_usage(path, model, usage, day=None):
    """记录一次 API 用量，模型与日期维度累计，线程安全（读-改-写全程持锁）。

    说明：load_stats 与 save_stats 之间必须持有 _LOCK，否则并发调用
    （多会话后台线程同时记录）会互相覆盖，造成用量丢失。
    """
    day = day or date.today().isoformat()
    model = model or "unknown"
    with _LOCK:
        data = load_stats(path)
        models = data.setdefault(day, {})
        acc = models.setdefault(model, empty_day())
        for key in ("prompt", "completion", "cache_hit", "cache_miss"):
            acc[key] = acc.get(key, 0) + int(usage.get(key, 0) or 0)
        save_stats(path, data)
    return data


def day_total(data, day=None):
    """某天全部模型合计，返回 usage dict。"""
    day = day or date.today().isoformat()
    total = empty_day()
    for usage in (data.get(day) or {}).values():
        for key in total:
            total[key] += usage.get(key, 0)
    return total


def all_total(data):
    total = empty_day()
    for models in data.values():
        for usage in models.values():
            for key in total:
                total[key] += usage.get(key, 0)
    return total


def model_total(data, model):
    total = empty_day()
    for models in data.values():
        usage = models.get(model)
        if usage:
            for key in total:
                total[key] += usage.get(key, 0)
    return total


def estimate_cost(usage, model):
    """按定价估算费用（元）。"""
    price = PRICING.get(model, DEFAULT_PRICE)
    miss = usage.get("prompt", 0) - usage.get("cache_hit", 0)
    miss = max(0, miss)
    cost = (
        miss * price["prompt"]
        + usage.get("cache_hit", 0) * price["cache_hit"]
        + usage.get("completion", 0) * price["completion"]
    ) / 1_000_000
    return cost


def format_cost(cost):
    if cost < 0.01:
        return "0.00"
    return f"{cost:.2f}"
