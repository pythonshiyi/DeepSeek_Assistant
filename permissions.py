# -*- coding: utf-8 -*-
"""权限模型：智能体行动能力的闸门（默认拒绝一切，只显式放行）。

- 默认全部关闭：filesystem.allow_write / shell.allow_run_command 均为 false。
- 所有路径操作必须先 resolve() 规范化（防 .. 穿越），再经 check_filesystem 判定。
- 审批模式：auto（白名单内自动）/ confirm（每次弹窗确认）/ deny（禁止）。
- 审计日志 actions.log（10MB 轮转），隐私模式下可关闭。
- 纯增量模块，不修改现有工具语义；行动类工具在实现内部调用本模块判定。
"""
import json
import logging
import os
import shlex
import threading
from datetime import datetime

PERMISSIONS_PATH = None
WORKSPACE_DIR = None
AUDIT_LOG_DIR = None
AUDIT_ENABLED = True
FULL_AUTO = False  # 完全智能模式（完全体放行）：允许目录内全自动（免审批/免开关），系统阻止列表仍生效；由 main 按 config 同步

_lock = threading.Lock()
_data = None
_approval_callback = None  # (name, args) -> (allowed, reason)
_whitelist_callback = None  # (action_type, value) -> (allowed, reason)
# 已 resolve 的目录缓存（Windows 大小写归一化），避免每次检查重复 expanduser/abspath
_dirs_cache = {"blocked": None, "allowed": None, "workspace": None}

DEFAULT_PERMISSIONS = {
    "version": 1,
    "filesystem": {
        "allow_write": False,
        "allowed_dirs": [],
        "blocked_dirs": [
            "C:/Windows",
            "C:/Program Files",
            "C:/Program Files (x86)",
        ],
        "max_write_size": 5 * 1024 * 1024,
    },
    "shell": {
        "allow_run_command": False,
        "whitelist": ["python", "pip", "pytest", "git"],
        "blocklist": ["rm", "del", "format", "shutdown", "taskkill", "reg"],
        "timeout": 60,
    },
    "approval_mode": "auto",  # auto / confirm / deny
    "approval_timeout": 120,
    "plan_confirm": False,  # 每轮工具调用前先确认整轮计划
}

# 需要审批闸门的行动工具（confirm 模式下逐个确认）
ACTION_TOOLS = (
    "write_file",
    "edit_file",
    "run_command",
    "create_doc",
    "write_code_project",
    "publish_draft",
    "start_process",
    "stop_process",
    "run_python",
    "send_email",
    "pip_install",
    # ===== v2 能力层：新加入的高危行动工具 =====
    "delete_file",
    "batch_rename",
    "extract_archive",
    "database_execute",
    "screen_capture",
    "clipboard_get",
    "read_email",
    "image_generate",
    "run_workflow",
    # ===== 文档 / 媒体 / 云盘（写入或高危，走审批） =====
    "pdf_create",
    "qrcode",
    "media_ffmpeg",
    "webdav",
    # ===== 插件工坊（AI 安装插件 = 新增能力，走审批） =====
    "create_plugin",
)


def init(path, workspace, audit_dir=None):
    """模块初始化：注入配置文件路径与工作目录。"""
    global PERMISSIONS_PATH, WORKSPACE_DIR, AUDIT_LOG_DIR, _data
    PERMISSIONS_PATH = path
    WORKSPACE_DIR = workspace
    AUDIT_LOG_DIR = audit_dir
    _data = _load()
    try:
        os.makedirs(workspace, exist_ok=True)
    except Exception:
        logging.warning("创建工作目录失败: %s", workspace)


def set_audit_enabled(enabled):
    global AUDIT_ENABLED
    AUDIT_ENABLED = bool(enabled)


def set_full_auto(enabled):
    """完全智能模式：允许目录内的写/命令/审批全部自动放行（系统阻止列表仍生效）。"""
    global FULL_AUTO
    FULL_AUTO = bool(enabled)


def is_full_auto():
    return FULL_AUTO


def get_data():
    return _data


def set_data(data):
    global _data
    with _lock:
        _data = data


def _load():
    data = json.loads(json.dumps(DEFAULT_PERMISSIONS))
    if PERMISSIONS_PATH and os.path.exists(PERMISSIONS_PATH):
        try:
            with open(PERMISSIONS_PATH, "r", encoding="utf-8") as f:
                disk = json.load(f)
            for section in ("filesystem", "shell"):
                if isinstance(disk.get(section), dict):
                    data[section].update(disk[section])
            for key in ("approval_mode", "approval_timeout", "plan_confirm"):
                if key in disk:
                    data[key] = disk[key]
        except Exception:
            logging.exception("读取权限配置失败，使用默认值")
    # 默认允许工作目录
    if WORKSPACE_DIR and WORKSPACE_DIR not in data["filesystem"]["allowed_dirs"]:
        data["filesystem"]["allowed_dirs"].append(WORKSPACE_DIR)
    # 动态阻止用户配置目录（权限文件中的 ~ 也展开处理）
    appdata = os.path.expanduser("~/AppData")
    if appdata and appdata not in data["filesystem"]["blocked_dirs"]:
        data["filesystem"]["blocked_dirs"].append(appdata)
    return data


def save():
    """保存权限配置（不落盘 allowed_dirs 中的自动工作目录，避免冗余）。"""
    if not PERMISSIONS_PATH:
        return False
    try:
        data = json.loads(json.dumps(_data))
        try:
            data["filesystem"]["allowed_dirs"].remove(WORKSPACE_DIR)
        except (ValueError, TypeError):
            pass
        with open(PERMISSIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        logging.exception("保存权限配置失败")
        return False


def resolve(path):
    """规范化路径：展开 ~、绝对化、去 .. 、realpath 解析链接（杜绝路径穿越与 junction 逃逸）。

    - 相对路径锚定到 WORKSPACE_DIR（不依赖进程 CWD）。
    - realpath 解析符号链接/联接点：允许目录内的 link 指向外部时按真实位置判定。
    - Windows 下统一大小写与分隔符（normcase）。
    非法返回 None。
    """
    try:
        p = str(path or "").strip()
        if not p:
            return None
        p = os.path.expanduser(p)
        if not os.path.isabs(p) and WORKSPACE_DIR:
            p = os.path.join(WORKSPACE_DIR, p)
        p = os.path.realpath(os.path.abspath(os.path.normpath(p)))
        return os.path.normcase(p)
    except Exception:
        return None


def _under(path, base):
    """路径是否位于 base 之下（两者均已 resolve 归一化大小写与分隔符）。"""
    base = base.rstrip("\\/")
    return path == base or path.startswith(base + os.sep)


def _dirs(dirs):
    out = []
    for d in dirs or []:
        p = resolve(d)
        if p:
            out.append(p)
    return out


def _cached_dirs(key, dirs):
    """目录列表缓存：check 高频调用，避免每次 expanduser/abspath/realpath。

    签名用内容元组（白名单运行期会增删，不能只比对列表对象身份）。
    """
    sig = tuple(str(d) for d in (dirs or []))
    cache = _dirs_cache.get(key)
    if cache is None or cache[0] != sig:
        _dirs_cache[key] = (sig, _dirs(dirs))
    return _dirs_cache[key][1]


def check_filesystem(path, write=False):
    """文件系统访问判定。返回 (allowed, reason)。"""
    if not _data:
        return False, "权限模块未初始化"
    p = resolve(path)
    if p is None:
        return False, f"权限拒绝：路径无效：{path}"
    if write and not _data["filesystem"].get("allow_write", False) and not FULL_AUTO:
        return (
            False,
            "权限拒绝：写文件未开启（🛠 工具中心 → 权限 → filesystem.allow_write）。"
            "如需授权，可调用 request_permission(action_type='write') 一键开启",
        )
    blocked = _cached_dirs("blocked", _data["filesystem"].get("blocked_dirs"))
    for b in blocked:
        if _under(p, b):
            return False, f"权限拒绝：路径在阻止列表内：{p}"
    allowed = _cached_dirs("allowed", _data["filesystem"].get("allowed_dirs"))
    if not allowed or not any(_under(p, a) for a in allowed):
        return (
            False,
            f"权限拒绝：路径不在允许目录内：{p}（允许目录：{_data['filesystem'].get('allowed_dirs')}）。"
            "如需授权该目录，可调用 request_permission(action_type='dir', value='<路径>') 请求加入白名单",
        )
    return True, ""


def max_write_size():
    try:
        return int(_data["filesystem"].get("max_write_size", 5 * 1024 * 1024))
    except (TypeError, ValueError):
        return 5 * 1024 * 1024


def check_shell(command):
    """命令判定：解析 argv、白名单/黑名单。返回 (allowed, reason, argv)。"""
    if not _data or (not _data["shell"].get("allow_run_command", False) and not FULL_AUTO):
        return (
            False,
            "权限拒绝：终端执行未开启（🛠 工具中心 → 权限 → shell.allow_run_command）。"
            "如需授权，可调用 request_permission(action_type='command') 请求开启",
            None,
        )
    try:
        # Windows 下必须用 posix=False：默认 POSIX 模式把反斜杠当转义符，
        # `python C:\Users\me\a.py` 会被拆成 'C:Usersmea.py'（路径被静默破坏）
        argv = shlex.split(str(command or ""), posix=(os.name != "nt"))
    except ValueError as e:
        return False, f"命令解析失败：{e}", None
    if not argv:
        return False, "命令为空", None
    base = os.path.basename(argv[0]).lower()
    blocklist = [str(b).lower() for b in _data["shell"].get("blocklist", [])]
    if base in blocklist:
        return False, f"权限拒绝：命令在阻止列表：{argv[0]}", None
    whitelist = [
        os.path.basename(str(w)).lower()
        for w in _data["shell"].get("whitelist", [])
    ]
    if base not in whitelist:
        return (
            False,
            f"权限拒绝：命令不在白名单：{argv[0]}（白名单：{_data['shell'].get('whitelist')}）。"
            "如需授权，可调用 request_permission(action_type='command', value='<命令名>') 请求加入白名单",
            None,
        )
    return True, "", argv


def shell_timeout():
    try:
        return int(_data["shell"].get("timeout", 60))
    except (TypeError, ValueError):
        return 60


def approval_mode():
    try:
        return str(_data.get("approval_mode", "auto"))
    except Exception:
        return "auto"


def approval_timeout():
    try:
        return float(_data.get("approval_timeout", 120))
    except (TypeError, ValueError):
        return 120.0


def set_approval_callback(cb):
    """设置 confirm 模式下的用户确认回调：(name, args) -> (allowed, reason)。"""
    global _approval_callback
    _approval_callback = cb


def set_whitelist_callback(cb):
    """设置白名单请求回调：(action_type, value) -> (allowed, reason)。"""
    global _whitelist_callback
    _whitelist_callback = cb


def add_to_whitelist(action_type, value):
    """把操作加入白名单并保存。返回 (allowed, message)。"""
    atype = str(action_type or "").strip().lower()
    value = str(value or "").strip()
    if atype == "write":
        _data["filesystem"]["allow_write"] = True
    elif atype == "dir":
        if not value:
            return False, "目录路径不能为空"
        p = resolve(value)
        if p is None:
            return False, f"目录路径无效：{value}"
        if p not in _data["filesystem"]["allowed_dirs"]:
            _data["filesystem"]["allowed_dirs"].append(p)
    elif atype == "command":
        if not value:
            return False, "命令不能为空"
        # 只取首个命令名（拒绝 "git status" 这类带参数串混入白名单——check_shell 只比对 argv[0]）
        try:
            base = os.path.basename(
                shlex.split(value, posix=(os.name != "nt"))[0]
            ).lower()
        except (ValueError, IndexError):
            base = ""
        if not base:
            return False, "命令名无效"
        blocklist = [str(b).lower() for b in _data["shell"].get("blocklist", [])]
        if base in blocklist:
            return False, f"命令 {value} 在阻止列表内，禁止加入白名单"
        whitelist = [str(w).lower() for w in _data["shell"].get("whitelist", [])]
        if base not in whitelist:
            # 与 check_shell 的 basename 比较对齐：存 basename，不存全路径/带参串
            _data["shell"]["whitelist"].append(base)
        # 一键授权：同时开启命令执行总开关，使该命令立即可用
        _data["shell"]["allow_run_command"] = True
    else:
        return False, f"不支持的白名单类型：{action_type}（支持 dir / command / write）"
    save()
    return True, "已加入白名单"


def request_whitelist(action_type, value):
    """请求用户把操作加入白名单（弹窗确认）。返回 (allowed, message)。

    完全智能模式下直接放行；否则走用户确认回调。
    """
    if FULL_AUTO:
        return add_to_whitelist(action_type, value)
    if _whitelist_callback is None:
        return False, "白名单请求通道不可用"
    try:
        return _whitelist_callback(action_type, value)
    except Exception:
        logging.exception("白名单请求回调异常")
        return False, "白名单请求通道异常"


def request_approval(name, args):
    """审批闸门（在 chat 工具循环中调用）。完全智能模式直接放行。"""
    if FULL_AUTO:
        return True, ""
    mode = approval_mode()
    if mode == "deny":
        return False, "权限拒绝：审批模式为 deny"
    if mode == "auto":
        return True, ""
    if _approval_callback is None:
        return False, "权限拒绝：审批通道不可用"
    try:
        return _approval_callback(name, args)
    except Exception:
        logging.exception("审批回调异常")
        return False, "审批通道异常"


def _audit_sanitize(s, limit=200):
    """审计字段净化：换行/控制字符转义 + 截断，防模型可控参数伪造日志行。"""
    if not isinstance(s, str):
        s = str(s)
    s = s.replace("\r", "\\r").replace("\n", "\\n")
    if len(s) > limit:
        s = s[:limit] + "…"
    return s


def audit(action, target, detail="", result="ok"):
    """写审计日志（隐私模式下跳过）。"""
    if not AUDIT_ENABLED or not AUDIT_LOG_DIR:
        return
    try:
        os.makedirs(AUDIT_LOG_DIR, exist_ok=True)
        line = (
            f"{datetime.now():%Y-%m-%d %H:%M:%S} [{_audit_sanitize(action, 40)}] "
            f"{_audit_sanitize(target)} | {_audit_sanitize(detail)} | "
            f"{_audit_sanitize(result, 40)}\n"
        )
        path = os.path.join(AUDIT_LOG_DIR, "actions.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        try:
            if os.path.getsize(path) > 10 * 1024 * 1024:
                os.replace(path, path + ".1")
        except OSError:
            pass
    except Exception:
        logging.exception("审计日志写入失败")
