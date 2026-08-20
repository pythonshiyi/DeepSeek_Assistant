# -*- coding: utf-8 -*-
"""鲸语插件体系（.wtplugin）：零代码能力的插件化封装。

插件格式（单文件 JSON，.wtplugin 后缀）：
{
  "format": "wtplugin",
  "version": 1,
  "meta": {"name", "description", "author", "version"},
  "requires": ["playwright"],        # 可选：依赖的 pip 包（安装时自检）
  "contents": {
    "tools": [...],                  # 自定义 HTTP 工具（user_tools.json 格式）
    "skills": [...],                 # 技能/提示词模板（prompts.json 格式）
    "workflows": {...},              # 流程（workflows.json 格式）
    "scenario": {...}                # 可选：一键场景（name/thinking/system_prompt/enabled_tools）
  }
}

安装 = 写入 plugins/ 目录 + 合并进各数据文件（条目带 _source: plugin:<slug> 标记）；
卸载 = 仅移除本插件标记的条目（用户手动添加的同名条目保留）；
停用 = 移除条目但保留插件文件；启用 = 重新合并。
"""
import json
import logging
import os
import re

PLUGIN_EXT = ".wtplugin"
PLUGIN_FORMAT = "wtplugin"
PLUGIN_VERSION = 1
_SOURCE_KEY = "_source"
_SOURCE_PREFIX = "plugin:"

logger = logging.getLogger("whaletalk.plugins")


def _slug(name):
    """插件文件名安全化（字母数字中文-_，最长 40）。"""
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]", "_", str(name or "plugin"))
    return s[:40] or "plugin"


def _source(slug):
    return _SOURCE_PREFIX + slug


def validate_plugin(data):
    """校验插件结构。返回 (ok, error)。"""
    if not isinstance(data, dict):
        return False, "插件内容必须是 JSON 对象"
    if data.get("format") != PLUGIN_FORMAT:
        return False, f"不是鲸语插件（format 应为 {PLUGIN_FORMAT}）"
    meta = data.get("meta")
    if not isinstance(meta, dict) or not str(meta.get("name") or "").strip():
        return False, "缺少插件名称（meta.name）"
    contents = data.get("contents") or {}
    if not isinstance(contents, dict):
        return False, "contents 必须是对象"
    if not any(k in contents for k in ("tools", "skills", "workflows", "scenario")):
        return False, "插件未包含任何能力（tools / skills / workflows / scenario）"
    for t in contents.get("tools") or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if not isinstance(fn, dict) or not fn.get("name") or not fn.get("endpoint"):
            return False, "工具条目缺少 function.name / function.endpoint"
    for s in contents.get("skills") or []:
        if not isinstance(s, dict) or not s.get("name") or not s.get("text"):
            return False, "技能条目缺少 name / text"
    wf = contents.get("workflows")
    if wf is not None and not isinstance(wf, dict):
        return False, "workflows 必须是 {名称: {steps: [...]}}"
    return True, ""


def parse_plugin_file(path):
    """读取 .wtplugin 文件并校验。返回 (plugin, error)。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return None, f"插件文件解析失败：{e}"
    ok, err = validate_plugin(data)
    if not ok:
        return None, err
    return data, ""


def list_plugins(plugins_dir):
    """扫描已安装插件目录。返回插件列表（含 slug / _file / enabled）。"""
    out = []
    if not plugins_dir or not os.path.isdir(plugins_dir):
        return out
    for fn in sorted(os.listdir(plugins_dir)):
        if not fn.endswith(PLUGIN_EXT):
            continue
        try:
            with open(os.path.join(plugins_dir, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("format") != PLUGIN_FORMAT:
                continue
            data["slug"] = fn[: -len(PLUGIN_EXT)]
            data["_file"] = os.path.join(plugins_dir, fn)
            data.setdefault("enabled", True)
            out.append(data)
        except Exception:
            continue
    return out


def save_plugin_file(plugin, plugins_dir):
    """把插件写入 plugins/ 目录（含 enabled 状态）。返回 (路径 or None)。"""
    if not plugins_dir:
        return None
    try:
        os.makedirs(plugins_dir, exist_ok=True)
        path = os.path.join(plugins_dir, _slug(plugin["meta"]["name"]) + PLUGIN_EXT)
        plugin.setdefault("enabled", True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(plugin, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path
    except Exception:
        logger.exception("保存插件文件失败")
        return None


def _tag(items, slug):
    """给条目打来源标记（浅拷贝，不改原始数据）。"""
    out = []
    for it in items or []:
        if isinstance(it, dict):
            it = dict(it)
            it[_SOURCE_KEY] = _source(slug)
        out.append(it)
    return out


def _merge(existing, new_items, key_fn, slug):
    """合并条目：同名覆盖（新条目带来源标记）。返回 (merged, added_names)。"""
    added = []
    for item in new_items:
        name = key_fn(item)
        if not name:
            continue
        existing = [x for x in existing if key_fn(x) != name]
        existing.append(item)
        added.append(name)
    return existing, added


def apply_plugin(plugin, paths):
    """安装/启用插件：写插件文件 + 合并进各数据文件。

    paths: {"plugins_dir", "user_tools", "prompts", "workflows"}（均为文件路径）
    返回 {"ok", "path", "added": {tools/skills/workflows: [names]}}。
    """
    meta = plugin.get("meta") or {}
    name = str(meta.get("name") or "")
    slug = _slug(name)
    contents = plugin.get("contents") or {}
    added = {"tools": [], "skills": [], "workflows": []}
    try:
        # 1. 插件文件（含 applied 记录，供卸载精确移除）
        applied = {"tools": [], "skills": [], "workflows": []}
        plugin.setdefault("enabled", True)

        # 2. 合并工具
        if contents.get("tools"):
            existing = _read_json(paths.get("user_tools"), [])
            new_tools = _tag(contents["tools"], slug)
            existing, names = _merge(
                existing, new_tools,
                key_fn=lambda t: (t.get("function") or {}).get("name") if isinstance(t, dict) else None,
                slug=slug,
            )
            _write_json(paths.get("user_tools"), existing)
            applied["tools"] = names
            added["tools"] = names

        # 3. 合并技能/提示词
        if contents.get("skills"):
            existing = _read_json(paths.get("prompts"), [])
            new_skills = _tag(contents["skills"], slug)
            existing, names = _merge(
                existing, new_skills,
                key_fn=lambda p: p.get("name") if isinstance(p, dict) else None,
                slug=slug,
            )
            _write_json(paths.get("prompts"), existing)
            applied["skills"] = names
            added["skills"] = names

        # 4. 合并流程
        wf = contents.get("workflows")
        if wf:
            existing = _read_json(paths.get("workflows"), {})
            names = []
            for wname, wdef in wf.items():
                existing[wname] = wdef
                names.append(wname)
            _write_json(paths.get("workflows"), existing)
            applied["workflows"] = names
            added["workflows"] = names

        plugin["applied"] = applied
        path = save_plugin_file(plugin, paths.get("plugins_dir"))
        return {"ok": True, "path": path, "added": added}
    except Exception as e:
        logger.exception("安装插件失败")
        return {"ok": False, "error": str(e), "added": added}


def unapply_plugin(plugin, paths):
    """卸载/停用插件：仅移除带本插件来源标记的条目（用户手动添加的同名条目保留）。

    优先从插件目录读取最新记录（含 applied 清单，安装时写入）。
    返回 {"ok", "removed": {tools/skills: [names], workflows: [names]}}。
    """
    slug = plugin.get("slug") or _slug(plugin.get("meta", {}).get("name") or "")
    src = _source(slug)
    # 从磁盘重读：applied 记录在安装时随文件写入，保证卸载精确
    if plugin.get("_file") and os.path.exists(str(plugin["_file"])):
        try:
            with open(plugin["_file"], "r", encoding="utf-8") as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                plugin = disk
        except Exception:
            pass
    removed = {"tools": [], "skills": [], "workflows": []}
    try:
        if paths.get("user_tools"):
            existing = _read_json(paths.get("user_tools"), [])
            kept, rn = _strip_source(existing, src, key_fn=lambda t: (t.get("function") or {}).get("name") if isinstance(t, dict) else None)
            if rn:
                _write_json(paths.get("user_tools"), kept)
                removed["tools"] = rn
        if paths.get("prompts"):
            existing = _read_json(paths.get("prompts"), [])
            kept, rn = _strip_source(existing, src, key_fn=lambda p: p.get("name") if isinstance(p, dict) else None)
            if rn:
                _write_json(paths.get("prompts"), kept)
                removed["skills"] = rn
        if paths.get("workflows"):
            # 流程按插件记录的名字精确移除（workflows.json 无内嵌来源标记）
            wf_names = (plugin.get("applied") or {}).get("workflows") or []
            existing = _read_json(paths.get("workflows"), {})
            rn = [n for n in wf_names if n in existing]
            for n in rn:
                existing.pop(n, None)
            if rn:
                _write_json(paths.get("workflows"), existing)
                removed["workflows"] = rn
        return {"ok": True, "removed": removed}
    except Exception as e:
        logger.exception("卸载插件失败")
        return {"ok": False, "error": str(e), "removed": removed}


def _strip_source(existing, src, key_fn):
    kept = []
    removed = []
    for it in existing:
        if isinstance(it, dict) and it.get(_SOURCE_KEY) == src:
            removed.append(key_fn(it))
        else:
            kept.append(it)
    return kept, removed


def _read_json(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except Exception:
        logger.exception("读取 %s 失败", path)
        return default


def _write_json(path, data):
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
        logger.exception("写入 %s 失败", path)
        return False


def missing_requires(plugin):
    """检查插件 requires 中未安装的 pip 包（importlib 探测）。返回缺失列表。"""
    missing = []
    for pkg in plugin.get("requires") or []:
        pkg = str(pkg or "").strip()
        if not pkg:
            continue
        mod = pkg.replace("-", "_")
        try:
            import importlib.util

            if importlib.util.find_spec(mod) is None:
                missing.append(pkg)
        except (ImportError, ValueError):
            missing.append(pkg)
    return missing


def _ratings_path(plugins_dir):
    return os.path.join(plugins_dir or "", "ratings.json")


def load_ratings(plugins_dir):
    """读取本地插件评分：{slug: {score, count, reviews: [...]}}。"""
    try:
        path = _ratings_path(plugins_dir)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        logger.exception("读取插件评分失败")
    return {}


def save_rating(plugins_dir, slug, score, review=""):
    """写入/追加一条评分（0-5 分）。返回 (ok, error)。"""
    try:
        score = max(0, min(5, int(score)))
    except (TypeError, ValueError):
        return False, "评分必须是 0-5 的整数"
    try:
        ratings = load_ratings(plugins_dir)
        entry = ratings.setdefault(slug, {"score": 0, "count": 0, "reviews": []})
        if review:
            entry["reviews"].append(str(review)[:500])
            entry["reviews"] = entry["reviews"][-50:]
        entry["count"] = int(entry.get("count", 0)) + 1
        prev = int(entry.get("score", 0))
        entry["score"] = prev + score
        path = _ratings_path(plugins_dir)
        atomic = _write_json(path, ratings)
        if not atomic:
            return False, "评分文件写入失败"
        return True, ""
    except Exception as e:
        return False, str(e)


def plugin_rating_summary(plugins_dir, slug):
    """返回插件平均分与评分次数。"""
    ratings = load_ratings(plugins_dir)
    entry = ratings.get(slug)
    if not entry or not int(entry.get("count", 0)):
        return None
    avg = round(int(entry.get("score", 0)) / int(entry["count"]), 1)
    return {"avg": avg, "count": int(entry["count"])}
