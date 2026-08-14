import json
import logging
import logging.handlers
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import bisect
from datetime import date, datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import tkinter.font as tkfont

from shared import (
    cron_field_ok,
    cron_match,
    PATH_RE,
    OCR_IMAGE_PS,
)

from deepseek_client import (
    DeepSeekClient,
    SCENARIOS,
    THINKING_MODES,
    MODELS,
    DEFAULT_BASE_URL,
    TOOLS,
    check_balance,
    is_peak_hour,
    set_process_output_callback,
)
import deepseek_client as _dc
import mdparse
import tokens
import stats
import prompts
import exporters
import permissions
import crypto
import plugins as plugins_mod
from splash import SplashScreen
from taskpanel import TaskPanel
from processpanel import ProcessPanel

try:
    import tkinterdnd2

    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

try:
    import pystray
    from PIL import Image as _TrayImage

    TRAY_AVAILABLE = True
except Exception:
    TRAY_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

APP_NAME = "鲸语"
APP_NAME_EN = "WhaleTalk"

LEGACY_DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "DeepSeek_Assistant")
DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", APP_NAME_EN)
LOG_DIR = os.path.join(DATA_DIR, "logs")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
SNAPSHOT_PATH = os.path.join(HISTORY_DIR, "session_latest.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")
PROMPTS_PATH = os.path.join(DATA_DIR, "prompts.json")
USER_TOOLS_PATH = os.path.join(DATA_DIR, "user_tools.json")
PROFILES_PATH = os.path.join(DATA_DIR, "profiles.json")
PERMISSIONS_PATH = os.path.join(DATA_DIR, "permissions.json")
WORKSPACE_DIR = os.path.join(DATA_DIR, "workspace")
DRAFT_PATH = os.path.join(DATA_DIR, "draft.json")
MEMORY_PATH = os.path.join(DATA_DIR, "memory.json")
SCHEDULES_PATH = os.path.join(DATA_DIR, "schedules.json")
PATTERNS_PATH = os.path.join(DATA_DIR, "patterns.json")
USER_ROLES_PATH = os.path.join(DATA_DIR, "user_roles.json")  # 用户自定义角色（可增删改分类）
SESSIONS_DIR = os.path.join(HISTORY_DIR, "sessions")
FAILURES_PATH = os.path.join(DATA_DIR, "failures.json")  # 失败模式库（工具失败→AI 规避参考）
PLUGINS_DIR = os.path.join(DATA_DIR, "plugins")  # 插件体系（.wtplugin 安装目录）
SAMPLE_PLUGINS_DIR = os.path.join(BASE_DIR, "sample_plugins")  # 内置示例插件（插件画廊）

EVOLUTIONS_DIR = os.path.join(BASE_DIR, "evolutions")

# 常驻行为指令（随 memory_text 注入，固定内容缓存友好）
TASK_QUALITY_GUIDE = (
    "[任务执行要求]\n"
    "1. 需要调用工具的任务，先输出执行计划（做什么/用什么工具/预期结果），再开始执行。\n"
    "2. 任务结束时执行自检：声明的产物是否都已创建？进程是否存活？代码/测试是否已运行验证？"
    "有失败项自动补做，并在回复中明确说明完成情况。\n"
    "3. 创建网页/服务后，用 start_process 启动并用 fetch_url 验证可访问。\n"
    "4. 你有自我审查能力：分析自身代码必须用 project_info / read_project_file（这两个工具始终可用，"
    "项目位于程序安装目录而非工作区）。审查产出是报告文档（用 create_doc 写入工作区 code-review/，"
    "包含问题/替换代码/验证方式），供开发 AI 实施，不要直接修改代码。\n"
    "5. 写文件/创建工程后，必须用 verify_files 或 list_dir 核验产物真实存在，"
    "发现缺失立即修正，不得继续后续步骤。\n"
    "6. 任务完成前，核验全部声明的产物文件；缺失则补建并重新验证。"
)
ARCHIVES_DIR = os.path.join(DATA_DIR, "archives")


_EMPTY_SHELL_CACHE = {"checked": False, "value": False}


def _is_empty_shell(path):
    """判断目录是否为空壳（仅含空子目录、无任何文件）。

    scandir + 栈遍历：第一层发现任何文件立即返回 False（对含大量文件的
    DATA_DIR 从 O(全部条目) 降为 O(第一层)）；结果缓存避免迁移/崩溃检测双遍历。
    """
    if _EMPTY_SHELL_CACHE["checked"]:
        return _EMPTY_SHELL_CACHE["value"]
    empty = True
    try:
        stack = [path]
        while stack:
            d = stack.pop()
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            empty = False
                            break
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                    except OSError:
                        continue
            if not empty:
                break
    except Exception:
        empty = False
    _EMPTY_SHELL_CACHE.update(checked=True, value=empty)
    return empty


def migrate_legacy_data():
    """首次运行新版本时，将旧数据目录（DeepSeek_Assistant）整体迁移到 WhaleTalk。

    仅当旧目录存在时执行；若新目录已存在但只是空壳（模块加载刚创建的空结构、
    不含任何文件），先移除空壳再迁移；新目录含真实数据或删除失败则不迁移
    （不阻塞启动，新目录自动重建）。
    """
    try:
        if not os.path.isdir(LEGACY_DATA_DIR):
            return False
        if os.path.exists(DATA_DIR):
            if not _is_empty_shell(DATA_DIR):
                return False
            shutil.rmtree(DATA_DIR, ignore_errors=True)
            if os.path.exists(DATA_DIR):
                return False
        os.makedirs(os.path.dirname(DATA_DIR), exist_ok=True)
        os.rename(LEGACY_DATA_DIR, DATA_DIR)
        return True
    except Exception as e:
        print(f"[鲸语] 旧数据目录迁移失败（不影响使用）: {e}")
        return False


migrate_legacy_data()

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(HISTORY_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(ARCHIVES_DIR, exist_ok=True)

permissions.init(PERMISSIONS_PATH, WORKSPACE_DIR, audit_dir=LOG_DIR)

# Agent 工具依赖注入：长期记忆文件 / 邮件 SMTP 配置（浏览器模式在 setup 时按配置同步）
_dc.MEMORY_FILE = MEMORY_PATH
_dc.EMAIL_CONFIG_FILE = os.path.join(DATA_DIR, "email_config.json")
_dc.DB_CONFIG_FILE = os.path.join(DATA_DIR, "db_config.json")
_dc.WEBHOOK_CONFIG_FILE = os.path.join(DATA_DIR, "webhooks.json")
_dc.BROWSER_HEADLESS = True
_dc.WORKING_DIR = None  # 工作目录传导：run_command/start_process 的 cwd（由 _set_active_dir 更新）
_dc.BROWSER_PROFILE_DIR = os.path.join(DATA_DIR, "browser_profile")
# v2 能力层注入：任务调度 / 知识库 / 流程 / 检查点 / 用量统计
_dc.SCHEDULES_FILE = SCHEDULES_PATH
_dc.KNOWLEDGE_INDEX_FILE = os.path.join(DATA_DIR, "knowledge_index.json")
_dc.WORKFLOWS_FILE = os.path.join(DATA_DIR, "workflows.json")
_dc.CHECKPOINT_FILE = os.path.join(DATA_DIR, "task_checkpoint.json")
_dc.STATS_FILE = STATS_PATH
_dc.PATTERNS_FILE = PATTERNS_PATH
# 文档 / RSS / KV / WebDAV 数据文件
_dc.RSS_SOURCES_FILE = os.path.join(DATA_DIR, "rss_sources.json")
_dc.KV_CACHE_DIR = os.path.join(DATA_DIR, "kv_cache")
_dc.WEBDAV_CONFIG_FILE = os.path.join(DATA_DIR, "webdav_config.json")
_dc.PLUGIN_PATHS = {
    "plugins_dir": PLUGINS_DIR,
    "user_tools": USER_TOOLS_PATH,
    "prompts": PROMPTS_PATH,
    "workflows": os.path.join(DATA_DIR, "workflows.json"),
}


def load_profiles(path=PROFILES_PATH):
    """读取 Profile 列表，返回 {"profiles": {name: cfg}, "current": name}。

    密文为 "dpapi:" 前缀（crypto.decrypt）；旧版明文自动兼容（无前缀原样返回）。
    """
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
        logging.exception("读取 Profile 失败")
    return {"profiles": {}, "current": ""}


def save_profiles(data, path=PROFILES_PATH):
    """保存 Profile 列表；api_key 一律经 DPAPI 加密，绝不明文落盘。

    加密失败（CryptError）时整次保存失败（fail-closed），磁盘保持旧文件。
    """
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
        logging.exception("保存 Profile 失败（api_key 未落盘）")
        return False


def load_user_tools(path=None):
    """读取用户自定义工具配置，返回 OpenAI function schema 列表。

    按文件 mtime+size 缓存：_worker 每轮请求都会调用，避免反复读盘+JSON 解析。
    """
    if path is None:
        path = USER_TOOLS_PATH
    try:
        st = os.stat(path)
        key = (st.st_mtime, st.st_size)
    except OSError:
        return []
    cached = _USER_TOOLS_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tools = []
        for t in data:
            if not isinstance(t, dict):
                continue
            fn = t.get("function") if isinstance(t.get("function"), dict) else t
            name = str(fn.get("name", "")).strip()
            desc = str(fn.get("description", "")).strip()
            endpoint = str(fn.get("endpoint", "")).strip()
            if not (name and endpoint):
                continue
            params = fn.get("params")
            if not isinstance(params, list):
                raw_params = fn.get("parameters")
                props = {}
                if isinstance(raw_params, dict):
                    props = raw_params.get("properties") or {}
                params = [{"name": str(k)} for k in props if isinstance(props, dict)]
            props = {}
            required = []
            if isinstance(params, list):
                for p in params:
                    if not isinstance(p, dict):
                        continue
                    pname = str(p.get("name", "")).strip()
                    if not pname:
                        continue
                    props[pname] = {
                        "type": "string",
                        "description": str(p.get("description", "") or ""),
                    }
                    required.append(pname)
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc,
                        "parameters": {"type": "object", "properties": props, "required": required},
                        "endpoint": endpoint,
                        "method": str(fn.get("method", "POST") or "POST"),
                    },
                }
            )
        _USER_TOOLS_CACHE[key] = tools
        return tools
    except Exception:
        logging.exception("读取自定义工具失败")
        return []


_USER_TOOLS_CACHE = {}  # (mtime, size) -> tools 列表（load_user_tools 缓存）

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "assistant.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
    ],
)
DEFAULT_SYSTEM_PROMPT = (
    "你是一个强大的AI助手，具备以下核心能力：\n"
    "1. 任务拆解：将复杂问题分解为可执行的子任务\n"
    "2. 工具调用：识别并调用合适的工具/函数完成任务\n"
    "3. 代码执行：编写、调试、优化代码\n"
    "4. 错误恢复：遇到错误时自主修正并继续执行\n"
    "5. 长上下文管理：在长达100万Token的上下文中保持任务状态一致性\n"
    "请使用中文回答。"
)

# 纯对话模式人格：纯正向设定，不出现任何工具/任务/功能概念（避免暗示）
DIALOG_SYSTEM_PROMPT = (
    "你是一位博学、友善、富有文采的 AI 对话伙伴。\n"
    "请以自然、真诚、温暖的方式与人交流：认真倾听、深入思考、坦诚回答。\n"
    "写作时言之有物、表达优美；讨论时观点清晰、有理有据；闲聊时轻松亲切。\n"
    "请使用中文回答。"
)

# 内建基础工具（始终默认启用）；行动层工具默认不启用，需用户开启
BUILTIN_TOOL_NAMES = [
    "get_date",
    "ask_user",
    "write_memory",
    "read_memory",
    "get_weather",
    "run_python",
    "read_file",
    "fetch_url",
    "search_web",
    # v2 能力层：安全的基础工具默认启用（高危工具仅出现在工具设置对话框）
    "list_schedules",
    "cancel_schedule",
    "notify_desktop",
    "clipboard_set",
    "knowledge_index",
    "knowledge_search",
    "task_checkpoint_save",
    "task_checkpoint_load",
    "run_workflow",
    "usage_report",
    "daily_brief",
    "create_plugin",
    # 公众号写作能力（安全：只产草稿不发布，发布权在用户；permissions 白名单已含 publish_draft）
    "run_wechat_writer",
    "publish_draft",
]

DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "scenario": "通用",
    "thinking": "high",
    "max_tokens": 16384,
    "seed": "",
    "tools_enabled": True,
    "enabled_tools": list(BUILTIN_TOOL_NAMES),
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "max_context_chars": 500000,
    "max_context_tokens": 400000,
    "min_kept_turns": 8,
    "timeout": 120,
    "restore_session": True,
    "font_size": 10,
    "theme": "light",
    "md_render": True,
    "custom_temperature": 1.0,
    "custom_top_p": 1.0,
    "privacy_mode": False,
    "check_update": False,
    "welcomed": False,
    "max_tool_rounds": 10,
    "monthly_budget": 0.0,
    "block_on_budget": False,
    "input_height": 4,
    "input_height_px": 0,
    "sidebar_width": 260,
    "browser_headless": True,
    "json_output": False,
    "beta_api": False,
    "peak_warning": True,
    "fold_early_threshold": 0,
    "current_profile": "",
    "notify_on_done": False,
    "ssrf_trusted": [],
    "project_context": False,
    "full_auto": False,
    "active_dir": "",
    "evolution_reminder_days": 7,
    "suggestions_enabled": True,
    "pure_chat": False,
    # v2 能力层配置
    "inbound_port": 0,        # Webhook 接收端端口（0=关闭）
    "inbound_token": "",      # Webhook 接收端鉴权 token
    "image_api_key": "",      # 图片生成 API Key
    "image_base_url": "",     # 图片生成端点（默认 = base_url）
    "image_model": "gpt-image-1",
    "window_geometry": "",    # 窗口大小位置记忆（如 1280x820+100+50）
    "minimize_to_tray": False,  # 关闭时最小化到系统托盘（需 pystray）
    "autostart": False,       # 开机自启（注册表 Run 键）
    "strict_tools": False,    # strict 工具模式（Beta）：模型严格遵循工具 JSON Schema
    "update_url": "",         # 更新检查源（latest.json，如 https://example.com/latest.json）
    "call_api_allowed_hosts": [],  # call_api 内网/回环白名单（精确主机名，建议 IP；如 ["127.0.0.1"]）
}

VERSION = "2.12.10"

ROLES = {
    "通用助手": {
        "prompt": DEFAULT_SYSTEM_PROMPT,
        "thinking": "high",
        "desc": "默认全能助手，任务拆解/工具调用/长上下文管理",
    },
    "智能体": {
        "prompt": (
            "你是一位面向开发与创作任务的自主智能体。工作协议：\n"
            "1) 目标先行：先理解任务目标与可验收的结果，主动澄清缺失的关键信息；\n"
            "2) 规划执行：拆解步骤，为每一步挑选合适的工具（读/写/运行/检索/抓取），"
            "按依赖顺序执行，能自动完成的不再询问；\n"
            "3) 产物落地：代码与文档写入文件系统（write_file / write_code_project / create_doc），"
            "服务与命令通过 run_command / start_process 运行；\n"
            "4) 验证闭环：每次产出后主动验证——运行测试、抓取页面、检查文件，失败即定位修正；\n"
            "5) 结果汇报：以结构化方式汇报完成项、验证证据（文件位置/测试输出/截图）、"
            "遗留风险与下一步建议。\n"
            "请使用中文，把「完成并交付可验证的产物」作为最高准则。"
        ),
        "thinking": "max",
        "desc": "自主任务执行：规划-执行-验证闭环，交付可验证产物",
    },
    "翻译官": {
        "prompt": (
            "你是一位专业翻译。翻译时：1) 准确传达原意，不增删改；2) 目标语言地道自然；"
            "3) 保留专有名词与格式；4) 代码/术语/缩写按领域惯例处理。请使用中文说明翻译取舍。"
        ),
        "thinking": "high",
        "desc": "中英互译，保留原意与格式，翻译取舍说明",
    },
    "代码评审专家": {
        "prompt": (
            "你是一位资深代码评审专家。评审时按严重度输出：1) 潜在 bug；2) 安全问题；"
            "3) 性能隐患；4) 可维护性建议。给出具体修复代码，并指出验证方式。使用中文。"
        ),
        "thinking": "max",
        "desc": "代码评审：bug/安全/性能/可维护性 + 修复代码",
    },
    "面试官": {
        "prompt": (
            "你是一位严谨的面试官。模拟面试：1) 围绕目标岗位与简历逐层提问；"
            "2) 追问回答中的技术要点与边界；3) 回答后给出评价与改进建议。使用中文。"
        ),
        "thinking": "high",
        "desc": "模拟面试：逐层提问 + 追问 + 评价建议",
    },
    "写作润色师": {
        "prompt": (
            "你是一位资深写作顾问。润色时：1) 保持原意；2) 提升表达流畅度与文采；"
            "3) 调整结构层次；4) 给出润色前后对比与修改说明。使用中文。"
        ),
        "thinking": "high",
        "desc": "文章润色：前后对比 + 修改说明",
    },
    "心理咨询伙伴": {
        "prompt": (
            "你是一位温暖、专业的倾听者。交流时：1) 先共情理解，不急于评判；"
            "2) 用开放式问题引导表达；3) 给出温和可行的建议；4) 涉及危机情况时建议寻求专业帮助。"
        ),
        "thinking": "high",
        "desc": "温暖倾听与陪伴，开放式引导与温和建议",
    },
    "周报助手": {
        "prompt": (
            "你是一位职场周报助理。根据提供的要点生成结构化周报：本周完成 / 下周计划 / "
            "风险与求助 / 成果数据。语言简洁专业，使用中文。"
        ),
        "thinking": "high",
        "desc": "结构化周报：完成/计划/风险/数据",
    },
}

PLAYGROUND_TASKS = {
    "写周报并存盘": (
        "请帮我写一篇本周工作周报（包含：本周完成、下周计划、风险与求助），"
        "完成后用 create_doc 保存为 Markdown 到工作区，并告诉我文件位置。"
    ),
    "创建迷你网站": (
        "请用 write_code_project 在工作目录创建一个小型网站（index.html + style.css + about.html），"
        "然后用 start_process 启动本地服务器，用 fetch_url 验证页面可访问，"
        "再用 web_screenshot 截图确认页面效果，最后给出访问地址、工程结构与验证结果。"
    ),
    "网页调研汇总": (
        "请用 fetch_url 抓取 2 个关于 DeepSeek API 的官方网页，"
        "汇总模型列表、价格与思考模式要点，输出结构化报告。"
    ),
    "数据分析计算": (
        "请用 run_python 计算 1 到 100 的所有质数之和，"
        "并解释算法思路与结果。"
    ),
    "代码审查修复": (
        "请审查以下代码，指出问题并用 edit_file 修复：\n"
        "```python\n"
        "def fib(n):\n"
        "    if n <= 1: return n\n"
        "    return fib(n-1) + fib(n-1)\n"
        "```\n"
        "修复后用 run_python 验证 fib(10)。"
    ),
    "本地文件检索": (
        "请用 search_local 在工作区检索包含『报告』的文件，"
        "列出命中文件与行内容，并总结。"
    ),
    "生成测试报告": (
        "请在工作区创建一个简单的 Python 模块与 pytest 测试文件，"
        "用 run_command 运行 pytest，根据结果用 edit_file 修复后重跑，输出测试报告。"
    ),
    "公众号草稿": (
        "请把下面的内容整理成一篇公众号文章草稿（标题 + 三段式正文），"
        "用 publish_draft 存入草稿箱：\n"
        "鲸语 WhaleTalk 是一款深度适配 DeepSeek 的桌面 AI 助手，"
        "支持流式对话、智能体工具链与本地文件创作。"
    ),
    "生成本地文档": (
        "请用 create_doc 生成一份《鲸语使用手册》Markdown 到工作区，"
        "涵盖：对话技巧、智能体权限、文件创作、数据安全四个部分。"
    ),
    "翻译并存盘": (
        "请把以下内容翻译成地道的英文，用 create_doc 保存为 translation.md 到工作区：\n"
        "好的 AI 工具应该让用户专注思考，而不是专注配置。"
    ),
}

TASK_TEMPLATES = {
    "写代码并运行": (
        "请完成以下编程任务。步骤：1) 分析需求；2) 编写代码；3) 调用 run_python "
        "工具实际运行并验证结果；4) 根据运行结果修正；5) 给出最终代码与运行结论。\n\n任务："
    ),
    "网页调研": (
        "请对以下主题进行调研。步骤：1) 分析主题；2) 通过 fetch_url 抓取 2-4 个相关网页；"
        "3) 汇总关键信息；4) 输出结构化报告（含来源链接）。\n\n主题："
    ),
    "数据分析": (
        "请处理以下数据分析任务。步骤：1) 明确分析目标；2) 若涉及数据请用 run_python "
        "处理并输出结果；3) 给出结论与可视化建议。\n\n任务："
    ),
    "创建代码工程": (
        "请创建以下代码工程。步骤：1) 分析需求与目录结构；2) 使用 write_code_project "
        "在允许目录（如工作区）创建多文件工程；3) 用 run_command 运行验证（如 python main.py）；"
        "4) 给出工程结构与运行结论。\n\n需求："
    ),
    "生成本地文档": (
        "请生成以下文档。步骤：1) 分析内容；2) 使用 create_doc 在允许目录生成 .md 文档；"
        "3) 总结文档结构与要点。\n\n内容："
    ),
    "执行项目测试": (
        "请执行以下测试任务。步骤：1) 定位项目目录；2) 使用 run_command 运行 pytest；"
        "3) 分析测试结果，失败则用 edit_file 修复后重跑；4) 输出测试报告。\n\n任务："
    ),
}

# 更新源：默认指向 GitHub Releases（返回 {"tag_name": "v2.12.10", "html_url": ...}）。
# 也兼容自定义 latest.json 格式 {"version": "...", "url": "..."}（见 check_for_update）；
# config.json 的 update_url 优先于此处。
UPDATE_URL = "https://api.github.com/repos/pythonshiyi/WhaleTalk/releases/latest"
CLEAN_EXIT_FLAG = os.path.join(DATA_DIR, ".clean_exit")

MAX_CONTEXT_TOKENS = 1_000_000
SCENARIO_DEFAULT_THINKING = {"通用": "high", "编程": "max", "Agent": "max"}

# ============ 布局定版规范（Layout Specification v1.0）============
# 所有模块的默认尺寸、范围与档位在此统一定义，禁止散落魔法数字
LAYOUT = {
    "window_w": 1280,           # 默认窗口宽
    "window_h": 820,            # 默认窗口高（16:10 内容比例）
    "window_min_w": 880,        # 最小窗口宽（小于紧凑阈值：窄窗口自动让位给聊天区）
    "window_min_h": 620,        # 最小窗口高
    "menu_h": 34,               # 菜单栏高度
    "status_h": 30,             # 状态栏高度
    "sidebar_default": 260,     # 侧栏默认宽
    "sidebar_min": 200,         # 侧栏最小宽
    "sidebar_max": 420,         # 侧栏最大宽
    "panel_default": 280,       # 设置面板默认宽
    "panel_min": 240,           # 设置面板最小宽
    "panel_max": 480,           # 设置面板最大宽
    "panel_files_w": 460,       # 文件视图宽度
    "content_min": 560,         # 聊天内容列最小宽
    "content_max": 860,         # 聊天内容列最大宽
    "content_margin": 120,      # 聊天列两侧总留白（聊天列 = tw - margin）
    "compact_panel_w": 1000,    # 窗口 <此宽度收起右侧面板
    "compact_sidebar_w": 1120,  # 窗口 <此宽度收起侧栏（窄窗口优先保聊天区）
    "dialog_s": 420,            # 对话框窄档
    "dialog_m": 520,            # 对话框中档
    "dialog_l": 640,            # 对话框宽档
    "dialog_h_s": 300,          # 对话框矮档
    "dialog_h_m": 420,          # 对话框中档高
    "dialog_h_l": 460,          # 对话框高档
    "dialog_h_editor": 540,     # 编辑器类高度
    "dialog_h_editor_l": 620,   # 大编辑器类高度
}
FLUSH_INTERVAL_MS = 40
POLL_IDLE_MS = 500
SNAPSHOT_INTERVAL_S = 2.0
SEARCH_DEBOUNCE_MS = 200
STREAM_IDLE_WARNING_S = 10
MAX_BLOCKS = 8000
PAGED_RENDER_THRESHOLD = 200   # blocks 超过该数量时全量渲染走分帧（防长会话一次性布局卡死）
PAGED_RENDER_SIZE = 250        # 每帧渲染的 block 数
PAGED_RENDER_MS = 25           # 帧间隔（事件循环得以处理输入/拖动/切换）
MAX_SEARCH_MATCHES = 2000
CODE_FENCE_RE = re.compile(r"^```\w*\s*$", re.MULTILINE)

RECENT_PATH = os.path.join(DATA_DIR, "recent_outputs.json")

FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"
PLACEHOLDER_TEXT = "输入问题，Enter 发送，Shift+Enter 换行"

# 可选依赖清单（依赖状态对话框）：(导入名, 显示名, 影响功能, 安装命令)
OPTIONAL_DEPS = [
    ("PIL", "Pillow", "图片处理/应用内图片预览/OCR/图表/图标", "pip install pillow"),
    ("pystray", "pystray", "系统托盘常驻", "pip install pystray"),
    ("playwright", "playwright", "浏览器操作/网页截图", "pip install playwright && playwright install chromium"),
    ("faster_whisper", "faster-whisper", "语音转文字", "pip install faster-whisper"),
    ("fitz", "PyMuPDF", "PDF 提取", "pip install PyMuPDF"),
    ("reportlab", "reportlab", "PDF 生成", "pip install reportlab"),
    ("docx", "python-docx", "Word 读写", "pip install python-docx"),
    ("pptx", "python-pptx", "PPT 读取", "pip install python-pptx"),
    ("feedparser", "feedparser", "RSS 聚合/每日简报/公众号写作", "pip install feedparser"),
    ("qrcode", "qrcode", "二维码生成", "pip install qrcode"),
    ("pyzbar", "pyzbar", "二维码识别", "pip install pyzbar（另需系统 zbar）"),
    ("diskcache", "diskcache", "KV 存储", "pip install diskcache"),
    ("imageio_ffmpeg", "imageio-ffmpeg", "音视频处理（内置 ffmpeg）", "pip install imageio-ffmpeg"),
    ("markdown", "markdown", "公众号写作 HTML 输出", "pip install markdown"),
    ("win32com", "pywin32", "语音朗读/语音合成", "pip install pywin32"),
    ("tkinterdnd2", "tkinterdnd2", "文件拖拽到输入框", "pip install tkinterdnd2"),
    ("tiktoken", "tiktoken", "精确 token 估算（缺省回退字符估算）", "pip install tiktoken"),
]


class CappedList(list):
    def __init__(self, items=(), maxlen=MAX_BLOCKS):
        super().__init__(items)
        self.maxlen = maxlen
        self._trim()

    def append(self, item):
        super().append(item)
        self._trim()

    def _trim(self):
        # 摊还 O(1)：超限时一次删掉超出的部分（del 头删为 O(n)，逐条删是 O(n²)）
        if len(self) > self.maxlen:
            del self[: len(self) - self.maxlen]

    def extend(self, items):
        super().extend(items)
        self._trim()


def _index_num(idx):
    """把 Tk 索引 "line.char" 转成可比较的数值键 (line, char)，供 bisect 使用。"""
    ln, _, ch = str(idx).partition(".")
    try:
        return (int(ln), int(ch or "0"))
    except ValueError:
        return (10**9, 0)


def _stream_defer_needed(buf):
    """流式 Markdown 渲染：缓冲含未闭合标记时暂缓整块（等标记闭合再渲染）。

    覆盖：代码围栏 ```、粗体 **、行内代码 `、链接 [ ] ( )。
    半角括号差值恰好为 1 才暂缓（多对不配对时视为普通文本，避免过度延迟）；
    生成结束由 _finish(force=True) 强制兜底，内容永不丢失。
    """
    if buf.count("```") % 2 == 1:
        return True
    if buf.count("**") % 2 == 1:
        return True
    if buf.count("`") % 2 == 1:
        return True
    if abs(buf.count("[") - buf.count("]")) == 1:
        return True
    if abs(buf.count("(") - buf.count(")")) == 1:
        return True
    return False


def _tail_start_offset(raw):
    """最后一个未完块在 raw 中的起始偏移（块边界 = 空行 / 块起始行 / 表格分隔行）。"""
    lines = raw.split("\n")
    offsets = []
    off = 0
    for ln in lines:
        offsets.append(off)
        off += len(ln) + 1
    for i in range(len(lines) - 1, -1, -1):
        ln = lines[i]
        if not ln.strip():
            return offsets[i] + len(ln) + 1
        if mdparse._starts_block(ln) or mdparse._is_table_sep(ln):
            return offsets[i] + len(ln) + 1
    return 0


def _stream_tail_split(raw):
    """把流式未渲染内容按段落边界切分，返回 (稳定部分, 尾部部分)。

    尾部 = 仍在书写、尚未到达段落边界的最后一个块（未闭合的段落等），
    它随流式逐帧整体重绘；稳定部分按完整 Markdown 渲染。段落只在
    "空行 / 块起始行 / 表格分隔行"处断开，而不是按输出 chunk 断开——
    修复"一句话被 40ms 输出块拆成多行"的分段问题。
    """
    if not raw:
        return "", ""
    if raw.endswith("\n\n"):
        return raw, ""
    blocks = mdparse.parse_blocks(raw)
    if not blocks:
        return "", raw
    last = blocks[-1]
    if last[0] != "plain" and raw.endswith("\n"):
        # 标题/列表/引用/表格/围栏等块已整行结束 → 全部稳定
        return raw, ""
    start = _tail_start_offset(raw)
    return raw[:start], raw[start:]


def _destroy_menu(menu):
    """安全销毁临时菜单（tk_popup 非阻塞返回后菜单仍在显示，必须等 Unmap 后销毁）。

    递归销毁 cascade 子菜单：Tk 销毁父菜单不会自动销毁子菜单，
    每次右键弹出「快速动作」子菜单后必须一并回收，否则 Tcl 菜单 widget 持续泄漏。
    """
    try:
        end = menu.index("end")
        if end is not None:
            for i in range(end + 1):
                try:
                    sub = menu.entrycget(i, "menu")
                except tk.TclError:
                    sub = None
                if sub:
                    _destroy_menu(sub)
    except tk.TclError:
        pass
    try:
        menu.destroy()
    except tk.TclError:
        pass


def _build_markdown(messages, usage_total, session_start, cfg):
    """把消息快照构建为 Markdown 导出文本（纯函数，供后台线程导出）。

    与 AssistantApp.build_markdown 等价，但接收快照参数而非访问会话状态。
    """
    lines = [
        f"# {APP_NAME} {APP_NAME_EN} 会话记录",
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
            lines += ["", "## 用户", "", m.get("content") or ""]
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

THEMES = {
    "light": {
        "name": "浅色",
        "bg": "#eef0f3",
        "page": "#f5f6f8",
        "panel": "#ffffff",
        "surface": "#eef0f3",
        "border": "#e3e5e9",
        "chat_bg": "#ffffff",
        "text": "#1d1f24",
        "text_sec": "#8a9099",
        "accent": "#3478f6",
        "accent_hover": "#2f6ce0",
        "accent_text": "#ffffff",
        "bubble_user": "#3478f6",
        "bubble_user_text": "#ffffff",
        "bubble_assistant": "#ffffff",
        "code_bg": "#f2f4f7",
        "code_fg": "#24292f",
        "input_bg": "#ffffff",
        "input_fg": "#1d1f24",
        "selection": "#bcd6ff",
        "thinking": "#98a2b3",
        "tool": "#af52de",
        "error": "#ff3b30",
        "warning": "#ff9500",
        "success": "#34c759",
        "disabled": "#c3c8cf",
        # ---- 1.11.0 定版新增 token：悬停/提示/引用/时间戳 ----
        "hover": "#e6ecf7",          # 列表/菜单悬停（带品牌蓝倾向）
        "note": "#8a9099",           # 时间戳与系统提示文字
        "mention": "#3478f6",        # 用户名提及/引用高亮
        "quote_bg": "#f2f6fd",       # 引用块背景（品牌蓝极浅）
        "input_placeholder": "#a6adb8",
    },
    "dark": {
        "name": "纯黑",
        "bg": "#000000",
        "page": "#000000",
        "panel": "#000000",
        "surface": "#1c1c1c",
        "border": "#3a3a3a",
        "chat_bg": "#000000",
        "text": "#f2f3f5",
        "text_sec": "#a8adb5",
        "accent": "#0a84ff",
        "accent_hover": "#3d9bff",
        "accent_text": "#ffffff",
        "bubble_user": "#0a84ff",
        "bubble_user_text": "#ffffff",
        "bubble_assistant": "#000000",
        "code_bg": "#161616",
        "code_fg": "#d6dde6",
        "input_bg": "#000000",
        "input_fg": "#f2f3f5",
        "selection": "#2058d8",
        "thinking": "#8b93a1",
        "tool": "#bf5af2",
        "error": "#ff453a",
        "warning": "#ff9f0a",
        "success": "#30d158",
        "disabled": "#4a4d55",
        # ---- 1.11.0 定版新增 token ----
        "hover": "#232323",
        "note": "#8b93a1",
        "mention": "#0a84ff",
        "quote_bg": "#101418",
        "input_placeholder": "#5a5f68",
    },
}


def split_code_blocks(text):
    segments = []
    pos = 0
    is_code = False
    for match in CODE_FENCE_RE.finditer(text):
        if match.start() > pos:
            segments.append(("code" if is_code else "assistant", text[pos : match.start()]))
        is_code = not is_code
        pos = match.end()
        if pos < len(text) and text[pos] in "\r\n":
            pos += 1
            if pos < len(text) and text[pos] == "\n":
                pos += 1
    if pos < len(text):
        segments.append(("code" if is_code else "assistant", text[pos:]))
    return segments


def _as_bool(v, default=False):
    """健壮布尔转换：手改配置写字符串 "false" 不再被误判为 True。"""
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return default


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
    cfg["tools_enabled"] = _as_bool(cfg.get("tools_enabled", True), True)
    cfg["restore_session"] = _as_bool(cfg.get("restore_session", True), True)
    cfg["privacy_mode"] = _as_bool(cfg.get("privacy_mode", False))
    cfg["check_update"] = _as_bool(cfg.get("check_update", False))
    cfg["welcomed"] = _as_bool(cfg.get("welcomed", False))
    cfg["browser_headless"] = _as_bool(cfg.get("browser_headless", True), True)
    try:
        cfg["custom_temperature"] = max(0.0, min(2.0, float(cfg.get("custom_temperature", 1.0))))
    except (TypeError, ValueError):
        cfg["custom_temperature"] = 1.0
    try:
        cfg["custom_top_p"] = max(0.0, min(1.0, float(cfg.get("custom_top_p", 1.0))))
    except (TypeError, ValueError):
        cfg["custom_top_p"] = 1.0
    try:
        cfg["max_tool_rounds"] = max(1, min(50, int(cfg.get("max_tool_rounds", 10))))
    except (TypeError, ValueError):
        cfg["max_tool_rounds"] = 10
    try:
        cfg["monthly_budget"] = max(0.0, float(cfg.get("monthly_budget", 0.0)))
    except (TypeError, ValueError):
        cfg["monthly_budget"] = 0.0
    cfg["block_on_budget"] = _as_bool(cfg.get("block_on_budget", False))
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
    if cfg.get("theme") not in THEMES:
        cfg["theme"] = "light"
    cfg["json_output"] = _as_bool(cfg.get("json_output", False))
    cfg["beta_api"] = _as_bool(cfg.get("beta_api", False))
    cfg["peak_warning"] = _as_bool(cfg.get("peak_warning", True), True)
    cfg["full_auto"] = _as_bool(cfg.get("full_auto", False))
    cfg["suggestions_enabled"] = _as_bool(cfg.get("suggestions_enabled", True), True)
    cfg["pure_chat"] = _as_bool(cfg.get("pure_chat", False))
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
    cfg["minimize_to_tray"] = _as_bool(cfg.get("minimize_to_tray", False))
    cfg["autostart"] = _as_bool(cfg.get("autostart", False))
    cfg["strict_tools"] = _as_bool(cfg.get("strict_tools", False))
    cfg["update_url"] = str(cfg.get("update_url", "") or "").strip()
    return cfg


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.update({k: data[k] for k in data if k in cfg})
        except Exception as e:
            logging.error("加载配置失败: %s", e)
    cfg["api_key"] = crypto.decrypt(cfg.get("api_key", ""))
    return normalize_config(cfg)


def previous_run_crashed(flag_path=CLEAN_EXIT_FLAG):
    """检查上次运行是否异常退出：返回 True 表示异常（无干净退出标记），并消费标记。

    首次运行（标记文件不存在且数据目录为空壳结构）不视为崩溃：
    写入干净标记并返回 False，避免新用户收到误导性警告。
    """
    if os.path.exists(flag_path):
        try:
            os.remove(flag_path)
        except Exception:
            pass
        return False
    if _is_empty_shell(DATA_DIR):  # 首次运行：仅有本次启动创建的空目录结构
        write_clean_exit_flag(flag_path)
        return False
    return True


def write_clean_exit_flag(flag_path=CLEAN_EXIT_FLAG):
    try:
        os.makedirs(os.path.dirname(flag_path) or ".", exist_ok=True)
        with open(flag_path, "w", encoding="utf-8") as f:
            f.write("ok")
        return True
    except Exception:
        return False


def save_config(cfg):
    try:
        data = dict(cfg)
        try:
            data["api_key"] = crypto.encrypt(cfg.get("api_key", ""))
        except crypto.CryptError:
            # 加密失败：从磁盘保留原密文，绝不写明文、绝不静默删除 api_key
            old_key = None
            try:
                if os.path.exists(CONFIG_PATH):
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        old_key = json.load(f).get("api_key")
            except Exception:
                pass
            if old_key:
                data["api_key"] = old_key
            else:
                data.pop("api_key", None)
            logging.error("API Key 加密失败，已保留磁盘原密文，请检查系统环境")
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    except Exception as e:
        logging.error("保存配置失败: %s", e)


def _delete_target(path, kind):
    """删除文件或目录内容，返回删除的文件数（失败静默跳过）。"""
    count = 0
    try:
        if kind == "file":
            if os.path.exists(path):
                os.remove(path)
                count = 1
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for f in files:
                    try:
                        os.remove(os.path.join(root, f))
                        count += 1
                    except Exception:
                        pass
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except Exception:
                        pass
    except Exception:
        logging.exception("清理失败: %s", path)
    return count


def _apply_privacy_logging(privacy):
    """隐私模式：彻底移除文件日志（WARNING/ERROR 也不再落盘），关闭时恢复。"""
    root = logging.getLogger()
    fh = [
        h for h in root.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    if privacy:
        for h in fh:
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)
    elif not fh:
        try:
            root.addHandler(
                logging.handlers.RotatingFileHandler(
                    os.path.join(LOG_DIR, "assistant.log"),
                    maxBytes=10 * 1024 * 1024,
                    backupCount=5,
                    encoding="utf-8",
                )
            )
        except Exception:
            pass


class AssistantApp:
    def _ensure_current(self):
        if getattr(self, "_current", None) is None:
            self._current = {
                "tab": None,
                "text": None,
                "messages": [],
                "usage_total": {"prompt": 0, "completion": 0, "cache_hit": 0, "cache_miss": 0},
                "last_usage": None,
                "assistant_answered": False,
                "session_start": 0,
                "blocks": CappedList(),
            }
        return self._current

    @property
    def chat_text(self):
        return self._ensure_current()["text"]

    @property
    def messages(self):
        return self._ensure_current()["messages"]

    @messages.setter
    def messages(self, value):
        self._ensure_current()["messages"] = value

    @property
    def usage_total(self):
        return self._ensure_current()["usage_total"]

    @usage_total.setter
    def usage_total(self, value):
        self._ensure_current()["usage_total"] = value

    @property
    def last_usage(self):
        return self._ensure_current()["last_usage"]

    @last_usage.setter
    def last_usage(self, value):
        self._ensure_current()["last_usage"] = value

    @property
    def assistant_answered(self):
        return self._ensure_current()["assistant_answered"]

    @assistant_answered.setter
    def assistant_answered(self, value):
        self._ensure_current()["assistant_answered"] = value

    @property
    def session_start(self):
        return self._ensure_current()["session_start"]

    @session_start.setter
    def session_start(self, value):
        self._ensure_current()["session_start"] = value

    @property
    def blocks(self):
        return self._ensure_current()["blocks"]

    @blocks.setter
    def blocks(self, value):
        self._ensure_current()["blocks"] = value

    def __init__(self, root):
        _t_init = time.perf_counter()
        self.root = root
        self.cfg = load_config()
        # SSRF 信任主机白名单（回环默认放行；内网经白名单显式信任后放行）
        _dc.set_ssrf_trusted(self.cfg.get("ssrf_trusted") or [])
        self._apply_screen_font_default()
        _t_cfg = time.perf_counter()
        self._sessions = []
        self._current = None
        self.client = None
        self.busy = False
        self.stop_event = None
        self.after = self.root.after
        self._pending = {"thinking": "", "content": ""}
        self._pending_tools = []
        self._pending_tool_durations = []
        self._agent_tool_count = 0
        self._last_stream_activity = 0.0
        self._auto_ckpt_tools = []  # 本轮工具链（自动断点用，worker 线程追加）
        self._ui_queue = queue.Queue()
        self._poller_id = None
        self._pending_send = None
        self._needs_compression = False
        self._resend_index = None
        self._search_open = False
        self._search_matches = []
        self._search_index = 0
        self._search_after_id = None
        self._placeholder_active = False
        self._updating_list = False
        self._list_visible = []
        self._restyle = []
        self._wheel_owner = None  # 当前接管全局滚轮的滚动面板（_scroll_panel 所有权模式）
        self._recent_cache = self._load_recent()  # 最近产物进程内缓存（_record_recent_output 只改内存）
        self._recent_dirty = False
        self._send_when_ready = None  # 分帧渲染期间挂起的待发消息
        self._search_when_ready = False  # 分帧渲染期间挂起的搜索
        self._pending_appends = []  # 分帧渲染期间挂起的文本追加
        self._speak_thread = None  # 朗读线程引用（防叠加）
        self._session_name_cache = {}  # id(session) -> 基础显示名（重命名时失效）
        self._list_last_names = None  # 会话列表上次渲染的显示名（无变化跳过重建）
        self._list_refresh_after = None  # 会话列表搜索防抖句柄
        self._inject_text = ""  # 待 worker 线程解析的相关文件引用文本
        self._compression_note_shown = False  # 压缩提示防重复
        self._schedules_lock = _dc.SCHEDULES_LOCK  # 定时任务文件读写串行化（与 AI 调度工具共享）
        self._stream_start = None
        self._stream_block_start = None
        self._last_snapshot_time = 0.0
        self._fold_ranges = {}
        self._fold_nums = {}  # 折叠数值键缓存：与 _fold_ranges[text] 同步维护，点击命中免重建列表
        self._filelink_ranges = {}
        self._md_render = bool(self.cfg.get("md_render", True))
        self.md_var = tk.BooleanVar(value=self._md_render)
        self._link_ranges = {}
        self._menu_msg_index = None
        self._status_after = None
        previous_run_crashed()  # 维护干净退出标记状态（不再弹窗提示）
        self._follow_bottom = True  # 智能跟随状态：True=贴底跟随（仅手动滚动置 False）
        permissions.set_approval_callback(self._request_approval_dialog)
        permissions.set_whitelist_callback(self._request_whitelist_callback)
        permissions.set_audit_enabled(not bool(self.cfg.get("privacy_mode", False)))
        permissions.set_full_auto(bool(self.cfg.get("full_auto", False)))
        if self.cfg.get("privacy_mode"):
            _apply_privacy_logging(True)
        self._ctx_counts = None
        self._paged_render = None
        self._paged_render_after = None
        # 消息列表并发保护：worker（压缩/裁剪/流式追加）与主线程（快照/回填/重建）
        # 对 self.messages 的复合读写（del 切片 + 索引访问）必须互斥，防长度漂移
        self._messages_lock = threading.RLock()
        self._snapshot_writing = False  # 快照后台写盘进行中标志（防并发写互相覆盖）
        self._pending_stats = {}
        self._last_stats_flush = 0.0
        self._round_aborted = False  # 本轮生成被截断/中断（max_tokens 达上限 / 流断线 / 异常）
        self._monthly_cost_cache = 0.0
        self._monthly_cost_time = None
        self._peak_notified = ""
        self._variant_seed_override = None
        self._stream_begin = 0.0
        self._thinking_received = False
        self._stream_tail = ""  # 流式"未闭合段落"原始文本（段落边界渲染用）
        self._stream_tail_rng = None  # 尾部在聊天 Text 中的 (起始, 结束) 索引
        self._stream_tail_links = []  # 尾部已注册的链接区间（删除尾部时一并清理）
        self._input_token_after = None
        self._hist_index = None
        self._hist_draft = ""
        self._draft_after = None
        self.task_panel = None
        self.proc_panel = None
        self._current_inject_text = ""
        set_process_output_callback(self._process_output)
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        self._inbound_server = None
        self._start_inbound_server()
        if self.cfg.get("privacy_mode"):
            logging.disable(logging.INFO)
        self.build_ui()
        _t_ui = time.perf_counter()
        self.setup_widgets_from_config()
        self.new_conversation(export_old=False)
        # 快照 JSON 解析 + 重建延后 100ms：先让窗口显示一帧，启动不再整段阻塞
        self.after(100, self._restore_snapshot)
        self._restore_draft()
        self._poller_id = self.after(FLUSH_INTERVAL_MS, self._poll_ui)
        # 注：崩溃检测不再弹窗提示（体验决策）——快照恢复始终静默进行
        if self.cfg.get("check_update") and (
            self.cfg.get("update_url") or UPDATE_URL
        ):
            self.after(3000, lambda: self.check_for_update(manual=False))
        if not self.cfg.get("welcomed"):
            self.after(500, self._show_welcome)
        self.after(400, self._apply_dark_titlebar)
        self.after(5000, self._maybe_remind_evolution)
        self._quit_from_tray = False  # 托盘「退出」触发的关闭不走最小化拦截
        self._tray_icon = None
        self._tray_thread = None
        self.after(1500, self._start_tray)
        # 启动性能观测：各阶段耗时写入日志（后续版本据此优化热点）
        logging.info(
            "启动耗时 %.2fs（配置 %.2fs · UI 构建 %.2fs）",
            time.perf_counter() - _t_init, _t_cfg - _t_init, _t_ui - _t_cfg,
        )

    def build_ui(self):
        t = self._theme()
        self.root.title(f"{APP_NAME} {APP_NAME_EN} v{VERSION}")
        # 窗口几何：默认最大化启动（Windows zoomed，铺满屏幕工作区，天然上下左右居中）；
        # 最大化不可用时回退记忆几何（屏幕内校验：尺寸超屏收缩、位置越界校正），
        # 再回退默认 1280x820 居中
        try:
            self.root.state("zoomed")
            self._window_geo_restored = True
        except tk.TclError:
            self._window_geo_restored = False
        if not getattr(self, "_window_geo_restored", False):
            geo = str(self.cfg.get("window_geometry") or "").strip()
            if geo and re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", geo):
                try:
                    m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geo)
                    w, h = int(m.group(1)), int(m.group(2))
                    x, y = int(m.group(3)), int(m.group(4))
                    sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                    if w >= LAYOUT["window_min_w"] and h >= LAYOUT["window_min_h"]:
                        # 尺寸超出屏幕（分辨率变小/换显示器）：收缩到屏幕内，避免
                        # 窗口整体偏右下方、底部在屏幕外
                        if w > sw:
                            w = sw
                        if h > sh:
                            h = sh
                        # 位置校正：窗口完全落在屏幕内（多显示器负坐标/屏幕变化时
                        # 左移或上移，保证启动即可见可用）
                        if x < 0:
                            x = 0
                        if y < 0:
                            y = 0
                        if x + w > sw:
                            x = max(0, sw - w)
                        if y + h > sh:
                            y = max(0, sh - h)
                        self.root.geometry(f"{w}x{h}+{x}+{y}")
                        self._window_geo_restored = True
                except (AttributeError, TypeError, ValueError):
                    pass
        if not getattr(self, "_window_geo_restored", False):
            try:
                sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                # 大屏自适应默认几何：屏幕的 72% 宽 × 80% 高（上限 1680x1050，
                # 下限布局最小尺寸）——大屏 PC 不再局促
                w = max(LAYOUT["window_min_w"], min(1680, int(sw * 0.72)))
                h = max(LAYOUT["window_min_h"], min(1050, int(sh * 0.80)))
                self.root.geometry(
                    f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 10)}"
                )
            except Exception:
                self.root.geometry(f"{LAYOUT['window_w']}x{LAYOUT['window_h']}")
        self.root.minsize(LAYOUT["window_min_w"], LAYOUT["window_min_h"])
        self.root.configure(bg=t["page"])

        self._menu_bar()
        self._side_panel()
        self._sidebar()
        self._status_bar()
        self._input_area()
        self._search_bar()
        self._input_handle()
        self._chat_area()

        self.root.bind("<Control-n>", lambda e: (self.new_conversation(), "break"))
        self.root.bind("<Control-N>", lambda e: (self.new_conversation(), "break"))
        self.root.bind("<Control-f>", self.toggle_search)
        self.root.bind("<Control-F>", self.toggle_search)
        self.root.bind(
            "<Control-e>", lambda e: (self.export_history(), "break")
        )
        self.root.bind(
            "<Control-E>", lambda e: (self.export_history(), "break")
        )
        self.root.bind(
            "<Control-Shift-s>", lambda e: (self.export_session_json(), "break")
        )
        self.root.bind(
            "<Control-Shift-f>", lambda e: (self.open_global_search(), "break")
        )
        self.root.bind(
            "<Control-Shift-q>", lambda e: (self._paste_clipboard_ask(), "break")
        )
        self.root.bind("<Control-Shift-Q>", lambda e: (self._paste_clipboard_ask(), "break"))
        self.root.bind("<Control-w>", lambda e: (self.close_tab(), "break"))
        self.root.bind("<Control-W>", lambda e: (self.close_tab(), "break"))
        self.root.bind("<F1>", lambda e: (self.show_help(), "break"))
        self.root.bind("<F5>", lambda e: (self.regenerate(), "break"))
        # 菜单 Alt 快捷键导航（Alt+F/E/V/T/A/S/H 打开对应菜单，正式桌面应用惯例）
        self._alt_menu_bindings = {"f": 0, "e": 1, "v": 2, "t": 3, "a": 4, "s": 5, "h": 6}
        for _k, _idx in self._alt_menu_bindings.items():
            self.root.bind(
                f"<Alt-{_k}>",
                lambda e, i=_idx: self._open_menu_alt(i),
            )
            self.root.bind(
                f"<Alt-{_k.upper()}>",
                lambda e, i=_idx: self._open_menu_alt(i),
            )
        self.root.bind("<Control-k>", lambda e: (self.show_command_palette(), "break"))
        self.root.bind("<Control-K>", lambda e: (self.show_command_palette(), "break"))
        self.root.bind("<F11>", lambda e: (self.toggle_fullscreen(), "break"))
        self.root.bind("<Control-Shift-t>", lambda e: (self.show_tool_hub(), "break"))
        self.root.bind("<Control-Shift-T>", lambda e: (self.show_tool_hub(), "break"))
        self.root.bind("<Control-Shift-p>", lambda e: (self.show_plugin_hub(), "break"))
        self.root.bind("<Control-Shift-P>", lambda e: (self.show_plugin_hub(), "break"))
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.bind("<Map>", lambda e: self._apply_dark_titlebar())
        self.root.bind("<FocusIn>", lambda e: self._apply_dark_titlebar())
        self._manual_hidden = {"sidebar": False, "panel": False}
        self.apply_theme()

    def _menu_bar(self):
        t = self._theme()
        menu_opts = dict(
            tearoff=0,
            bg=t["panel"],
            fg=t["text"],
            activebackground=t["accent"],
            activeforeground=t["accent_text"],
            bd=0,
            relief="flat",
            font=(FONT_FAMILY, 9),
        )
        self._menus = []
        self._menu_by_title = {}  # 顶级菜单标题 → Menu（测试/主题刷新定位用）
        self._menu_buttons = []
        bar = tk.Frame(self.root, bg=t["panel"])
        bar.pack(side="top", fill="x")
        self._restyle.append((bar, "panel"))
        self.menu_bar_frame = bar

        def add_menu(title, setup):
            menu = tk.Menu(self.root, **menu_opts)
            self._menus.append(menu)
            self._menu_by_title[title] = menu  # 顶级菜单标题映射（含级联子菜单时 _menus 不同步）
            setup(menu)
            btn = tk.Button(
                bar,
                text=title,
                bg=t["panel"],
                fg=t["text"],
                activebackground=t.get("hover", t["surface"]),
                activeforeground=t["text"],
                relief="flat",
                bd=0,
                highlightthickness=0,
                padx=10,
                pady=4,
                cursor="hand2",
                font=(FONT_FAMILY, 9),
            )
            btn.config(command=lambda m=menu, b=btn: self._post_menu(m, b))
            btn.pack(side="left")
            self._menu_buttons.append(btn)
            self._restyle.append((btn, "menu_btn"))
            return menu

        def _file_menu(fm):
            fm.add_command(label="新会话", accelerator="Ctrl+N", command=self.new_conversation)
            fm.add_command(label="新建临时会话", command=lambda: self.add_tab(ephemeral=True))
            fm.add_command(label="删除当前会话", command=self.close_tab)
            fm.add_command(label="历史会话库", command=self.show_history_sessions)
            fm.add_command(label="导入会话…", command=self.import_session_file)
            fm.add_separator()
            fm.add_command(label="导出历史 (MD/TXT/HTML/JSONL)", accelerator="Ctrl+E", command=self.export_history)
            fm.add_command(label="导出会话 JSON", accelerator="Ctrl+Shift+S", command=self.export_session_json)
            fm.add_separator()
            fm.add_command(label="退出", command=self.on_close)

        def _edit_menu(em):
            em.add_command(label="编辑重发", command=self.edit_last_message)
            em.add_command(label="重新生成", accelerator="F5", command=self.regenerate)
            em.add_command(label="生成变体", command=self.regenerate_variant)
            em.add_command(label="浏览变体…", command=self.show_variants)
            em.add_command(label="继续生成（Beta 续写）", command=self.continue_generation)
            em.add_separator()
            em.add_command(label="粘贴剪贴板并提问 (Ctrl+Shift+Q)", command=self._paste_clipboard_ask)
            em.add_command(label="复制回复", command=self.copy_last_reply)
            em.add_command(label="复制代码", command=self.copy_last_code)
            em.add_command(label="复制全部对话", command=self._copy_all)
            em.add_separator()
            em.add_command(label="查找对话内容", accelerator="Ctrl+F", command=self.toggle_search)
            em.add_command(label="全局搜索全部会话", accelerator="Ctrl+Shift+F", command=self.open_global_search)
            em.add_command(label="🗺 会话轨迹", command=self.show_session_timeline)
            em.add_separator()
            em.add_command(label="查看收藏", command=self.show_stars)
            em.add_command(label="长期记忆…", command=self.manage_memory)
            em.add_command(label="复制分享文本", command=self.copy_share_text)
            em.add_command(label="🔊 朗读最后回复", command=self.speak_last_reply)

        def _tools_menu(tm):
            # 中心入口（最高频导航按钮，置顶直连）
            tm.add_command(label="🛠 工具中心", accelerator="Ctrl+Shift+T", command=self.show_tool_hub)
            tm.add_command(label="🧩 插件中心", accelerator="Ctrl+Shift+P", command=self.show_plugin_hub)
            tm.add_separator()

            # ── 账户与用量 ──
            account = tk.Menu(tm, **menu_opts)
            self._menus.append(account)
            account.add_command(label="💰 查余额", command=self.check_balance)
            account.add_command(label="📊 用量统计", command=self.show_stats)
            account.add_command(label="🎯 预算设置", command=self.edit_budget)
            account.add_command(label="📐 上下文详情", command=self.show_context_details)
            account.add_command(label="⚖ 模型对比", command=self.compare_models)
            tm.add_cascade(label="账户与用量", menu=account)

            # ── 任务与模板 ──
            tasks = tk.Menu(tm, **menu_opts)
            self._menus.append(tasks)
            task_menu = tk.Menu(tasks, **menu_opts)
            self._menus.append(task_menu)
            for tname in TASK_TEMPLATES:
                task_menu.add_command(
                    label=tname, command=lambda n=tname: self.run_task_template(n)
                )
            tasks.add_cascade(label="Agent 任务模板", menu=task_menu)
            tasks.add_command(label="批量任务…", command=self.show_batch_task)
            tasks.add_command(label="📰 生成每日简报", command=lambda: self.send(
                text="请生成今日简报：采集当日 AI/科技资讯，提炼要点与点评，并保存到工作区。"
            ))
            tasks.add_command(label="📄 生成会话纪要", command=self.generate_session_summary)
            tasks.add_command(label="🗺 会话轨迹", command=self.show_session_timeline)
            tm.add_cascade(label="任务与模板", menu=tasks)

            # ── 文件与产物 ──
            files = tk.Menu(tm, **menu_opts)
            self._menus.append(files)
            files.add_command(label="📁 工作目录…", command=self.choose_working_dir)
            files.add_command(label="🌳 工作区文件树…", command=self.show_workspace_tree)
            files.add_command(label="📦 最近产物…", command=self.show_recent_outputs)
            files.add_command(label="🖥 进程终端…", command=self.show_process_terminal)
            tm.add_cascade(label="文件与产物", menu=files)

            # ── 能力扩展 ──
            caps = tk.Menu(tm, **menu_opts)
            self._menus.append(caps)
            caps.add_command(label="🔧 自定义工具（Agent SDK）", command=self.manage_user_tools)
            caps.add_command(label="👤 Profile 管理（多账号）", command=self.manage_profiles)
            caps.add_command(label="📝 提示词库管理", command=self.manage_prompts)
            caps.add_command(label="✂ FIM 代码补全…", command=self.show_fim_dialog)
            caps.add_command(label="🔌 依赖状态…", command=self.show_dependencies)
            tm.add_cascade(label="能力扩展", menu=caps)

            # ── 自我进化 ──
            evo = tk.Menu(tm, **menu_opts)
            self._menus.append(evo)
            evo.add_command(label="🧬 自我进化（代码提案）…", command=self.show_evolutions)
            evo.add_command(label="🧬 自我审查（生成报告）…", command=self.show_evolution_audit)
            evo.add_command(label="🧬 功能建议（升级方向）…", command=self.show_feature_suggestions)
            evo.add_command(label="🧬 打开审查报告目录", command=self.open_review_reports)
            tm.add_cascade(label="🧬 自我进化", menu=evo)

            # ── 系统 ──
            sysm = tk.Menu(tm, **menu_opts)
            self._menus.append(sysm)
            sysm.add_command(label="🔧 失败模式库…", command=self.show_failures)
            sysm.add_command(label="🔗 推送与数据库配置…", command=self.show_external_config)
            sysm.add_command(label="🖼 从剪贴板图片提取文字 (OCR)…", command=self._ocr_clipboard)
            sysm.add_command(label="🧹 数据清理…", command=self.show_cleanup)
            sysm.add_command(label="⌨ 命令面板", accelerator="Ctrl+K", command=self.show_command_palette)
            tm.add_cascade(label="系统", menu=sysm)

        def _automation_menu(am):
            # 自动化：定时触发与流程编排（独立顶级菜单，精确分类）
            am.add_command(label="⏰ 定时任务…", command=self.manage_schedules)
            am.add_command(label="🔁 流程管理…", command=self.manage_workflows)
            am.add_separator()
            am.add_command(label="📚 知识库管理…", command=self.manage_knowledge)
            am.add_command(label="📍 任务检查点…", command=self.show_checkpoint)
            am.add_command(label="📋 项目任务记录…", command=self.show_tasklog)
            am.add_separator()
            am.add_command(label="📰 生成每日简报", command=lambda: self.send(
                text="请生成今日简报：采集当日 AI/科技资讯，提炼要点与点评，并保存到工作区。"
            ))

        def _view_menu(vm):
            vm.add_command(label="切换主题", command=self.toggle_theme)
            vm.add_command(label="增大字号", accelerator="Ctrl++", command=lambda: self._step_font(1))
            vm.add_command(label="减小字号", accelerator="Ctrl+-", command=lambda: self._step_font(-1))
            vm.add_checkbutton(label="Markdown 渲染", variable=self.md_var, command=self.toggle_md_render)
            vm.add_separator()
            vm.add_command(label="设置面板（右侧）", command=self.toggle_side_panel)
            vm.add_command(label="会话列表（左侧）", command=self.toggle_sidebar)
            vm.add_command(label="⛶ 全屏模式", accelerator="F11", command=self.toggle_fullscreen)
            vm.add_separator()
            self.suggest_var2 = tk.BooleanVar(value=bool(self.cfg.get("suggestions_enabled", True)))
            vm.add_checkbutton(
                label="主动建议（对话结束后提示）",
                variable=self.suggest_var2,
                command=lambda: (
                    self.cfg.__setitem__("suggestions_enabled", bool(self.suggest_var2.get())),
                    save_config(self.cfg),
                ),
            )

        def _settings_menu(sm):
            # ── AI 行为 ──
            sm.add_command(label="🎭 角色与提示词…", command=self.show_roles)
            sm.add_separator()
            self.strict_tools_var = tk.BooleanVar(
                value=bool(self.cfg.get("strict_tools", False))
            )
            sm.add_checkbutton(
                label="strict 工具模式（Beta，开启时自动启用 Beta API）", variable=self.strict_tools_var,
                command=lambda: self._on_strict_tools_toggle(),
            )
            sm.add_separator()
            # ── 应用行为 ──
            self.notify_done_var = tk.BooleanVar(value=bool(self.cfg.get("notify_on_done", False)))
            sm.add_checkbutton(
                label="完成通知（任务栏闪烁 + 提示音 + 桌面通知）",
                variable=self.notify_done_var,
                command=lambda: (
                    self.cfg.__setitem__("notify_on_done", bool(self.notify_done_var.get())),
                    save_config(self.cfg),
                ),
            )
            self.proj_ctx_var = tk.BooleanVar(value=bool(self.cfg.get("project_context", False)))
            sm.add_checkbutton(
                label="项目上下文（注入工作区概览）",
                variable=self.proj_ctx_var,
                command=lambda: (
                    self.cfg.__setitem__("project_context", bool(self.proj_ctx_var.get())),
                    save_config(self.cfg),
                ),
            )
            self.privacy_var = tk.BooleanVar(value=bool(self.cfg.get("privacy_mode", False)))
            sm.add_checkbutton(
                label="隐私模式（不保存快照/统计/日志）", variable=self.privacy_var,
                command=self._on_privacy_toggle,
            )
            self.autostart_var = tk.BooleanVar(value=self._autostart_enabled())
            sm.add_checkbutton(
                label="开机自启（后台启动，常驻托盘）", variable=self.autostart_var,
                command=lambda: self._on_autostart_toggle(),
            )
            self.minimize_tray_var = tk.BooleanVar(
                value=bool(self.cfg.get("minimize_to_tray", False))
            )
            sm.add_checkbutton(
                label="关闭时最小化到托盘", variable=self.minimize_tray_var,
                command=lambda: self._on_minimize_tray_toggle(),
            )
            sm.add_separator()
            sm.add_command(label="保存配置", command=self.save_widgets_to_config)

        def _help_menu(hm):
            hm.add_command(label="使用说明", command=self.show_help)
            hm.add_command(label="检查更新", command=lambda: self.check_for_update(manual=True))
            hm.add_command(label="关于", command=self.show_about)

        add_menu("文件(F)", lambda fm: _file_menu(fm))
        add_menu("编辑(E)", lambda em: _edit_menu(em))
        add_menu("视图(V)", lambda vm: _view_menu(vm))
        add_menu("工具(T)", lambda tm: _tools_menu(tm))
        add_menu("自动化(A)", lambda am: _automation_menu(am))
        add_menu("设置(S)", lambda sm: _settings_menu(sm))
        add_menu("帮助(H)", lambda hm: _help_menu(hm))

        # 固定建议区：菜单栏右侧停靠，不弹窗不遮挡（对话结束后如有建议在此展示）
        self.suggestion_frame = tk.Frame(bar, bg=t["panel"])
        self.suggestion_lbl = tk.Label(
            self.suggestion_frame, text="", bg=t["panel"], fg=t["text"],
            font=(FONT_FAMILY, 9),
        )
        self.suggestion_lbl.pack(side="left")
        self.suggestion_btn = tk.Button(
            self.suggestion_frame, text="采纳", command=self._suggestion_apply,
            bg=t["accent"], fg=t["accent_text"],
            activebackground=t["accent_hover"], activeforeground=t["accent_text"],
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=8, pady=2,
        )
        self.suggestion_btn.pack(side="left", padx=(6, 0))
        self.suggestion_close_btn = tk.Button(
            self.suggestion_frame, text="✕", command=self._hide_suggestion,
            bg=t["panel"], fg=t["text_sec"],
            activebackground=t.get("hover", t["surface"]), activeforeground=t["text"],
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            font=(FONT_FAMILY, 9), padx=4, pady=2,
        )
        self.suggestion_close_btn.pack(side="left", padx=(4, 0))
        self.suggestion_frame.pack_forget()
        self._suggestion_state = None
        self._suggestion_after = None
        self._restyle.append((self.suggestion_frame, "panel"))
        self._restyle.append((self.suggestion_lbl, ("label", "label_text", "panel")))
        self._restyle.append((self.suggestion_btn, "primary_btn"))
        self._restyle.append((self.suggestion_close_btn, "flat_btn"))

    def _post_menu(self, menu, btn):
        # 注意：顶层菜单是应用生命周期持久复用的对象（add_menu 创建并常驻），
        # 弹出后绝不能 destroy——销毁后第二次点击会抛 TclError 导致菜单失效。
        # 只有每次新建的临时菜单（右键/指令弹出）才需要在弹出后 destroy。
        try:
            menu.tk_popup(
                btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height()
            )
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    def _open_menu_alt(self, idx):
        """Alt+字母 打开对应菜单（0=文件 1=编辑 2=视图 3=工具 4=设置 5=帮助）。"""
        try:
            if 0 <= idx < len(self._menu_buttons):
                self._post_menu(self._menus[idx], self._menu_buttons[idx])
        except Exception:
            pass

    def _apply_menu_theme(self, t):
        for menu in getattr(self, "_menus", []) or []:
            try:
                menu.configure(
                    bg=t["panel"],
                    fg=t["text"],
                    activebackground=t["accent"],
                    activeforeground=t["accent_text"],
                    disabledforeground=t["disabled"],
                )
            except tk.TclError:
                pass
    def _apply_dark_titlebar(self):
        """通过 DWM API 让 Windows 标题栏跟随主题（深色黑/浅色白）。

        Win10 2004~21H2 上该属性仅在系统深色应用模式下渲染；Win11 22H2+
        独立生效。设置后追加 SWP_FRAMECHANGED 强制重绘以提高生效概率。
        """
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            if not hwnd:
                return
            dark = self.cfg.get("theme") == "dark"
            value = ctypes.c_int(1 if dark else 0)
            ok = False
            for attr in (20, 19):
                try:
                    r = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
                    )
                    ok = ok or (r == 0)
                except Exception:
                    continue
            # 强制重绘标题栏边框（Win10 上提高 DWM 属性生效概率）
            try:
                SWP_NOSIZE = 0x0001
                SWP_NOMOVE = 0x0002
                SWP_NOZORDER = 0x0004
                SWP_FRAMECHANGED = 0x0020
                ctypes.windll.user32.SetWindowPos(
                    hwnd, None, 0, 0, 0, 0,
                    SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED,
                )
            except Exception:
                pass
        except Exception:
            pass

    def _on_minimize_tray_toggle(self):
        """关闭时最小化到托盘开关：托盘不可用（未装 pystray）时提示并回滚勾选。

        托盘尚未启动（延迟 1.5s 启动）时先补启动再判断可用性。
        """
        want = bool(self.minimize_tray_var.get())
        if want and not TRAY_AVAILABLE:
            self.minimize_tray_var.set(False)
            messagebox.showwarning(
                "托盘不可用",
                "未安装 pystray，无法使用系统托盘。\n\n请运行：pip install pystray",
            )
            return
        if want and not getattr(self, "_tray_icon", None):
            self._start_tray()  # 用户此刻就需要：立即补启动（不等待 1.5s 延迟）
        if want and not self._tray_alive():
            self.minimize_tray_var.set(False)
            messagebox.showwarning(
                "托盘启动失败",
                "系统托盘启动失败（不影响主程序使用）。\n"
                "关闭窗口将按正常方式退出，不会最小化到托盘。",
            )
            return
        self.cfg["minimize_to_tray"] = want
        save_config(self.cfg)
        self._flash_status("已开启：关闭窗口最小化到托盘" if want else "已关闭：关闭窗口将直接退出")

    def _on_strict_tools_toggle(self):
        """strict 工具模式开关：需 Beta 端点（strict 模式官方要求 base_url 为 /beta）。

        开启时自动打开 Beta API 并保存；关闭时仅保存开关。
        """
        want = bool(self.strict_tools_var.get())
        if want:
            if not self.cfg.get("beta_api"):
                if not messagebox.askyesno(
                    "strict 工具模式",
                    "strict 模式需要 Beta 端点（https://api.deepseek.com/beta）。\n"
                    "将自动开启「Beta API」开关。继续？",
                ):
                    self.strict_tools_var.set(False)
                    return
                self.cfg["beta_api"] = True
                self.beta_var.set(True)
            self._flash_status("strict 工具模式已开启：模型将严格遵循工具参数格式（Beta）")
        else:
            self._flash_status("strict 工具模式已关闭")
        self.cfg["strict_tools"] = want
        save_config(self.cfg)

    def _on_autostart_toggle(self):
        """开机自启开关：注册表写入失败时提示并回滚勾选。"""
        want = bool(self.autostart_var.get())
        if want and not self._set_autostart(True):
            self.autostart_var.set(False)
            messagebox.showwarning("开机自启失败", "注册表写入失败，无法开启开机自启。")
            return
        if not want:
            self._set_autostart(False)
        self.cfg["autostart"] = want
        save_config(self.cfg)
        self._flash_status("已开启开机自启" if want else "已关闭开机自启")

    def _on_privacy_toggle(self):
        self.cfg["privacy_mode"] = bool(self.privacy_var.get())
        permissions.set_audit_enabled(not self.cfg["privacy_mode"])
        _apply_privacy_logging(self.cfg["privacy_mode"])
        # 开启时 logging.disable(INFO) 全局抑制；关闭必须恢复，否则本次运行 INFO 日志永久丢失
        if self.cfg["privacy_mode"]:
            logging.disable(logging.INFO)
        else:
            logging.disable(logging.NOTSET)
        save_config(self.cfg)
        self.update_status()
        self._flash_status("已开启隐私模式" if self.cfg["privacy_mode"] else "已关闭隐私模式")

    def _show_welcome(self):
        if self.cfg.get("api_key"):
            self.cfg["welcomed"] = True
            save_config(self.cfg)
            return
        t = self._theme()
        dialog, body, footer = self._dialog_shell(
            f"欢迎使用 {APP_NAME} {APP_NAME_EN}", 560, 440,
            subtitle="为 DeepSeek V4 深度优化的桌面 AI 工作台",
        )
        dialog.resizable(False, False)
        dialog.grab_set()
        for line in (
            "· 流式输出思考过程与回答，支持 1M 长上下文与缓存优化",
            "· 100+ Agent 工具：文档/代码/浏览器/数据/媒体/公众号写作",
            "· 产物面板：生成的文件一键打开，无需翻找目录",
            "· 多会话、上下文自动压缩、用量统计与预算控制",
            "· 提示词库、消息收藏、分支对话、深色主题",
        ):
            self._lbl(body, "✓ " + line, bg="panel", font=(FONT_FAMILY, 9)).pack(
                anchor="w", pady=1
            )
        self._lbl(
            body, "第一步：配置 API Key（可在 https://platform.deepseek.com 申请）",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9, "bold"),
        ).pack(anchor="w", pady=(18, 4))
        row = tk.Frame(body, bg=t["panel"])
        row.pack(fill="x")
        self._restyle.append((row, "panel"))
        self.welcome_key_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.welcome_key_var, show="*")
        entry.pack(side="left", fill="x", expand=True)
        self._mk_button(
            row, "获取 Key", lambda: webbrowser.open("https://platform.deepseek.com"), fsz=9
        ).pack(side="left", padx=(8, 0))

        def done():
            key = self.welcome_key_var.get().strip()
            if key:
                self.cfg["api_key"] = key
                self.key_var.set(key)
            self.cfg["welcomed"] = True
            save_config(self.cfg)
            dialog.destroy()

        self._footer_btn(footer, "开始使用", done, primary=True)
        self._footer_btn(
            footer,
            "先体验试玩任务",
            lambda: (done(), self.after(300, lambda: self._run_playground("写周报并存盘"))),
        )

    def check_for_update(self, manual=True):
        url = str(self.cfg.get("update_url") or "").strip() or UPDATE_URL
        if not url:
            if manual:
                messagebox.showinfo(
                    "检查更新",
                    f"未配置更新源（config.json 的 update_url，或 main.py 的 UPDATE_URL），当前版本 {VERSION}",
                )
            return
        if manual:
            self._flash_status("正在检查更新…")

        def worker():
            try:
                resp = _dc._http_client().get(url, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                # GitHub Releases: tag_name（"v2.12.10"）; 自定义源: version
                latest = str(
                    data.get("version") or data.get("tag_name") or ""
                ).strip().lstrip("v")
                if latest:
                    self._ui_queue.put((
                        "update",
                        (latest, str(data.get("url") or data.get("html_url") or "")),
                    ))
            except Exception as e:
                logging.warning("检查更新失败: %s", e)
                if manual:
                    self._ui_queue.put(("error", f"检查更新失败: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_root_configure(self, event):
        """紧凑模式：窄窗口自动收起侧栏/设置面板（防抖处理）。"""
        if getattr(self, "_compact_pending", False):
            return
        self._compact_pending = True
        try:
            self.after(60, self._apply_compact)
        except tk.TclError:
            pass

    def _apply_compact(self):
        self._compact_pending = False
        try:
            w = self.root.winfo_width()
            if w <= LAYOUT["compact_sidebar_w"]:
                if self.sidebar_frame.winfo_manager() and not self._manual_hidden.get("sidebar"):
                    self.sidebar_frame.pack_forget()
                    self.chat_resize_handle.pack_forget()
            else:
                if not self.sidebar_frame.winfo_manager() and not self._manual_hidden.get("sidebar"):
                    # 用 before 保持 侧栏→分隔条→聊天区 的原始 pack 顺序：
                    # 直接 pack 会把它们追加到 slave 列表末尾，布局错乱成 聊天区在左
                    self.sidebar_frame.pack(side="left", fill="y", before=self.chat_frame)
                    self.chat_resize_handle.pack(side="left", fill="y", before=self.chat_frame)
            if w <= LAYOUT["compact_panel_w"]:
                if self.side_panel.winfo_manager() and not self._manual_hidden.get("panel"):
                    self.side_panel.pack_forget()
            else:
                if not self.side_panel.winfo_manager() and not self._manual_hidden.get("panel"):
                    # 与 toggle_side_panel 一致：恢复原始 pack 顺序，避免布局错乱
                    self.side_panel.pack(side="right", fill="y", before=self.input_frame)
        except tk.TclError:
            pass

    def toggle_side_panel(self):
        if self.side_panel.winfo_manager():
            self.side_panel.pack_forget()
            self._manual_hidden["panel"] = True
        else:
            # 用 before 恢复原始 pack 顺序（初始构建时 side_panel 先于 input_frame/handle/chat_frame
            # 打包）：直接 pack 会把 side_panel 追加到 slave 列表末尾，pack 重新分配时
            # 输入框（side=bottom）不再被右侧面板挤占而变宽，聊天区却被后 pack 的面板切窄，
            # 表现为输入框大小变动 + 挤压右侧栏位置
            self.side_panel.pack(side="right", fill="y", before=self.input_frame)
            self._manual_hidden["panel"] = False

    def toggle_sidebar(self):
        """视图菜单：左侧会话列表显隐（手动隐藏不受紧凑模式干扰）。"""
        if self.sidebar_frame.winfo_manager():
            self.sidebar_frame.pack_forget()
            self.chat_resize_handle.pack_forget()
            self._manual_hidden["sidebar"] = True
        else:
            self.sidebar_frame.pack(side="left", fill="y", before=self.chat_frame)
            self.chat_resize_handle.pack(side="left", fill="y", before=self.chat_frame)
            self._manual_hidden["sidebar"] = False

    def _step_font(self, delta):
        size = int(self.cfg.get("font_size", 10)) + delta
        self.cfg["font_size"] = max(8, min(18, size))
        self.apply_font_size()
        try:
            self.font_size_combo.set(str(self.cfg["font_size"]))
        except Exception:
            pass
        save_config(self.cfg)

    def show_about(self):
        """关于对话框：品牌信息 + 能力一览（正式版品牌视觉）。"""
        dialog, body, footer = self._dialog_shell(
            f"关于 {APP_NAME} {APP_NAME_EN}", 460, 420, subtitle="深海蓝鲸 · 专业桌面 AI 工作台"
        )
        self._lbl(body, f"🐋 {APP_NAME} {APP_NAME_EN}", role="label_accent", bg="panel",
                  font=(FONT_FAMILY, 18, "bold")).pack(anchor="w", pady=(0, 2))
        self._lbl(body, f"版本 {VERSION} · 基于 DeepSeek V4 API", role="label_sec", bg="panel",
                  font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(0, 14))
        for line in (
            "· 流式思考与回答、1M 长上下文、缓存命中优化",
            "· 100+ Agent 工具：文档/代码/浏览器/数据/媒体/云盘/公众号写作",
            "· 产物面板与产物条：生成的文件一键直达",
            "· 会话快照、用量统计、预算控制、隐私模式",
            "· 自我进化：AI 可感知自身代码并提出改进提案",
        ):
            self._lbl(body, "✓ " + line, bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w", pady=1)
        self._lbl(
            body, f"\n{APP_NAME} 是独立产品，与 DeepSeek 官方无任何关联。",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w")
        self._footer_btn(footer, "关闭", dialog.destroy)

    def show_help(self):
        """帮助对话框：常用操作速查（正式版排版）。"""
        t = self._theme()
        dialog, body, footer = self._dialog_shell(
            "使用说明", 520, 460, subtitle="常用操作速查 · F1 随时打开"
        )
        rows = [
            ("发送消息", "Enter 发送 · Shift+Enter 换行 · Ctrl+Enter 快速发送"),
            ("新会话 / 关闭", "Ctrl+N 新建 · Ctrl+W 关闭 · 双击会话名重命名"),
            ("对话搜索", "Ctrl+F 会话内搜索 · Ctrl+Shift+F 全局搜索"),
            ("导出历史", "Ctrl+E 导出 MD/TXT/HTML/JSONL"),
            ("命令面板", "Ctrl+K 唤起全部常用操作"),
            ("重新生成", "F5 重新生成最后回复"),
            ("编辑重发", "聊天区右键消息 → 编辑此消息"),
            ("消息操作", "右键消息：复制/收藏/固定/分叉/引用/朗读/快速动作"),
            ("产物查看", "右侧面板「📂 文件」Tab + 输入框上方产物条"),
            ("生成中打断", "直接发送新消息即可打断并继续"),
            ("设置面板", "左栏「⚙ 设置」按钮收起/展开右侧面板"),
            ("主题与字号", "菜单 视图 → 切换主题 / 增大/减小字号"),
        ]
        for k, v in rows:
            row = tk.Frame(body, bg=t["panel"])
            row.pack(fill="x", pady=2)
            self._restyle.append((row, "panel"))
            self._lbl(row, k, role="label_accent", bg="panel", font=(FONT_FAMILY, 9, "bold"),
                      width=12, anchor="w").pack(side="left")
            self._lbl(row, v, bg="panel", font=(FONT_FAMILY, 9), anchor="w").pack(side="left")
        self._footer_btn(footer, "关闭", dialog.destroy)

    PROMPT_KEYWORDS = {
        "中译英": ["翻译", "translate", "英文"],
        "英译中": ["翻译", "translate", "中文"],
        "代码审查": ["审查", "review", "bug", "代码"],
        "解释代码": ["解释", "讲解", "这是什么"],
        "生成单元测试": ["测试", "test", "单元测试"],
        "周报助手": ["周报", "日报", "总结"],
        "文章润色": ["润色", "改写", "优化"],
        "SQL 优化": ["sql", "查询", "数据库"],
    }

    def _show_prompt_menu(self):
        items = prompts.load_prompts(PROMPTS_PATH)
        query = self.input_text.get("1.0", "end-1c").lower()
        scored = []
        for p in items:
            score = 0
            for kw in self.PROMPT_KEYWORDS.get(p["name"], []):
                if kw in query:
                    score += 1
            scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        t = self._theme()
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=t["panel"],
            fg=t["text"],
            activebackground=t.get("hover", t["surface"]),
            activeforeground=t["text"],
            bd=1,
            relief="flat",
        )
        if scored and scored[0][0] > 0:
            menu.add_command(label="智能推荐（匹配当前输入）", state="disabled")
            for score, p in scored:
                if score <= 0:
                    break
                menu.add_command(
                    label=f"★ {p['name']}", command=lambda p=p: self._insert_prompt(p)
                )
            menu.add_separator()
        for score, p in scored:
            menu.add_command(
                label=p["name"], command=lambda p=p: self._insert_prompt(p)
            )
        menu.add_separator()
        menu.add_command(label="管理提示词…", command=self.manage_prompts)
        try:
            menu.tk_popup(
                self.btn_prompts.winfo_rootx(),
                self.btn_prompts.winfo_rooty() + self.btn_prompts.winfo_height(),
            )
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass
            # tk_popup 非阻塞返回，菜单仍在事件循环中显示；立即 destroy
            # 会让菜单一闪即逝（用户点击无反应）。延迟回收：3 秒后菜单
            # 早已被用户操作关闭（选择菜单项或点击外部），此时销毁安全。
            self.root.after(3000, lambda: _destroy_menu(menu))

    def _insert_prompt(self, prompt):
        text = self.input_text.get("1.0", "end-1c")
        filled = prompts.apply_template(prompt["text"], text)
        self._clear_placeholder()
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", filled)
        self.input_text.focus_set()
        self._flash_status(f"已插入指令: {prompt['name']}")

    def manage_prompts(self):
        t = self._theme()
        items = prompts.load_prompts(PROMPTS_PATH)
        dialog, body, footer = self._dialog_shell(
            "提示词库", 580, 460,
            subtitle="预置常用模板，输入区「⚡ 指令」一键插入；{{TEXT}} 会被输入框内容替换",
        )

        left = tk.Frame(body, bg=t["panel"])
        left.pack(side="left", fill="y", padx=(4, 8))
        self._restyle.append((left, "panel"))
        listbox = tk.Listbox(
            left,
            width=22,
            bg=t["input_bg"],
            fg=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
            exportselection=False,
        )
        listbox.pack(fill="both", expand=True)
        for p in items:
            listbox.insert("end", p["name"])

        right = tk.Frame(body, bg=t["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(8, 4))
        self._restyle.append((right, "panel"))
        self._lbl(right, "名称", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w")
        name_var = tk.StringVar()
        ttk.Entry(right, textvariable=name_var).pack(fill="x", pady=(2, 6))
        self._lbl(right, "内容（{{TEXT}} 会被输入框内容替换）", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w")
        text = tk.Text(
            right,
            height=10,
            wrap="word",
            bg=t["input_bg"],
            fg=t["input_fg"],
            insertbackground=t["input_fg"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
        )
        text.pack(fill="both", expand=True, pady=(2, 8))

        def select_item(_e=None):
            sel = listbox.curselection()
            if not sel:
                return
            p = items[sel[0]]
            name_var.set(p["name"])
            text.delete("1.0", "end")
            text.insert("1.0", p["text"])

        def new_item():
            listbox.selection_clear(0, "end")
            name_var.set("")
            text.delete("1.0", "end")
            text.focus_set()

        def save_item():
            name = name_var.get().strip()
            content = text.get("1.0", "end").strip()
            if not name or not content:
                return
            sel = listbox.curselection()
            if sel:
                items[sel[0]] = {"name": name, "text": content}
            else:
                items.append({"name": name, "text": content})
            if prompts.save_prompts(PROMPTS_PATH, items):
                listbox.delete(0, "end")
                for p in items:
                    listbox.insert("end", p["name"])
                self._flash_status("提示词库已保存")

        def delete_item():
            sel = listbox.curselection()
            if not sel:
                return
            items.pop(sel[0])
            if prompts.save_prompts(PROMPTS_PATH, items):
                listbox.delete(0, "end")
                for p in items:
                    listbox.insert("end", p["name"])
                name_var.set("")
                text.delete("1.0", "end")

        listbox.bind("<<ListboxSelect>>", select_item)
        self._footer_hint(footer, "模板内容中的 {{TEXT}} 会被输入框内容替换")
        self._footer_btn(footer, "关闭", dialog.destroy)
        self._footer_btn(footer, "删除", delete_item)
        self._footer_btn(footer, "保存", save_item, primary=True)
        self._footer_btn(footer, "新增", new_item)

    def _sidebar(self):
        t = self._theme()
        sw = int(self.cfg.get("sidebar_width", 224) or 224)
        frame = tk.Frame(self.root, bg=t["page"], width=sw)
        frame.pack(side="left", fill="y")
        frame.pack_propagate(False)
        self._restyle.append((frame, "page"))
        self.sidebar_frame = frame

        head = tk.Frame(frame, bg=t["page"])
        head.pack(fill="x", padx=12, pady=(10, 6))
        self._restyle.append((head, "page"))
        self._lbl(head, "对话", role="label_sec", font=(FONT_FAMILY, 10, "bold")).pack(side="left")
        self.btn_new = self._mk_button(head, "＋ 新建", lambda: self.add_tab(), kind="primary", fsz=9)
        self.btn_new.pack(side="right")

        self.session_search_var = tk.StringVar()
        self.session_search_entry = ttk.Entry(
            frame, textvariable=self.session_search_var
        )
        self.session_search_entry.pack(fill="x", padx=10, pady=(0, 2))
        # 搜索词变化防抖 200ms：每敲一个字符就全量重建 Listbox 会让列表卡顿
        self.session_search_var.trace_add(
            "write", lambda *a: self._schedule_session_list_refresh()
        )
        self.session_count_label = self._lbl(
            frame, "", role="label_sec", bg="page", font=(FONT_FAMILY, 9)
        )
        self.session_count_label.pack(anchor="w", padx=12, pady=(0, 4))

        self.session_list = tk.Listbox(
            frame,
            bg=t["panel"],
            fg=t["text"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            selectborderwidth=0,
            activestyle="none",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT_FAMILY, 9),
            exportselection=False,
        )
        self.session_list.pack(fill="both", expand=True, padx=10)
        self._restyle.append((self.session_list, "listbox"))
        # 会话列表细滚动条：place 叠加（不参与 pack，长会话名撑宽列表时也不会被压缩掉）
        list_sb = tk.Scrollbar(
            frame,
            orient="vertical",
            command=self.session_list.yview,
            relief="flat",
            bd=0,
            highlightthickness=0,
            width=10,
        )
        list_sb.place(relx=1.0, x=-10, y=0, width=10, relheight=1.0)
        self.session_list.configure(yscrollcommand=list_sb.set)
        self._restyle.append((list_sb, "list_scrollbar"))
        self._session_list_scrollbar = list_sb

        def _on_list_wheel(event):
            try:
                if abs(event.delta) >= 120:
                    # 标准鼠标滚轮：每格滚动 3 行（Tk 默认步长），避免滚轮过于费力
                    self.session_list.yview_scroll(-3 * int(event.delta / 120), "units")
                else:
                    # 高精度触控板 delta < 120：按像素滚动，避免 int() 截断成死区
                    self.session_list.yview_scroll(-int(event.delta / 10), "pixels")
            except Exception:
                pass
            return "break"

        self.session_list.bind("<MouseWheel>", _on_list_wheel)
        self.session_list.bind("<Button-4>", lambda e: (self.session_list.yview_scroll(-1, "units"), "break")[1])
        self.session_list.bind("<Button-5>", lambda e: (self.session_list.yview_scroll(1, "units"), "break")[1])
        self.session_list.bind("<<ListboxSelect>>", self._on_tab_changed)
        self.session_list.bind("<Double-1>", self._on_tab_double_click)
        self.session_list.bind("<Button-3>", self._on_session_menu)
        self.session_list.bind("<Delete>", lambda e: self.close_tab())
        self.session_list.bind("<F2>", lambda e: self.rename_tab())

        foot = tk.Frame(frame, bg=t["page"])
        foot.pack(fill="x", padx=10, pady=(4, 10))
        self._restyle.append((foot, "page"))
        self.btn_toggle_panel = self._mk_button(foot, "⚙ 设置", self.toggle_side_panel, fsz=9)
        self.btn_toggle_panel.pack(side="left")
        self.btn_balance = self._mk_button(foot, "余额", self.check_balance, fsz=9)
        self.btn_balance.pack(side="right")

        # 聊天区宽度分隔条：默认与页面同色（不明显），悬停/拖拽时高亮；双击恢复默认
        handle = tk.Frame(self.root, bg=t["page"], width=6, cursor="sb_h_double_arrow")
        handle.pack(side="left", fill="y")
        self._restyle.append((handle, "chat_resize_handle"))
        self.chat_resize_handle = handle
        handle.bind("<Button-1>", self._on_width_press)
        handle.bind("<Double-1>", self._reset_sidebar_width)
        handle.bind(
            "<Enter>", lambda e: handle.configure(bg=self.theme_color("accent"))
        )
        handle.bind(
            "<Leave>", lambda e: handle.configure(bg=self.theme_color("page"))
        )
        self._width_drag_start_x = None
        self._width_drag_base = 0

    def _set_sidebar_width(self, w):
        """设置侧栏宽度（LAYOUT 档位内），chat_frame 自动重排，聊天列随之调整。"""
        try:
            w = max(LAYOUT["sidebar_min"], min(LAYOUT["sidebar_max"], int(w)))
        except (TypeError, ValueError):
            return
        try:
            self.sidebar_frame.configure(width=w)
        except tk.TclError:
            pass

    def _on_width_press(self, event):
        self._width_drag_start_x = event.x_root
        try:
            self._width_drag_base = int(self.sidebar_frame.winfo_width())
        except (tk.TclError, TypeError, ValueError):
            self._width_drag_base = int(self.cfg.get("sidebar_width", 224) or 224)
        return "break"

    def _on_width_drag(self, event):
        if getattr(self, "_width_drag_start_x", None) is None:
            return None
        self._set_sidebar_width(self._width_drag_base + (event.x_root - self._width_drag_start_x))
        return "break"

    def _on_width_release(self, _event=None):
        if getattr(self, "_width_drag_start_x", None) is None:
            return None
        self._width_drag_start_x = None
        try:
            w = int(self.sidebar_frame.winfo_width())
        except (tk.TclError, TypeError, ValueError):
            w = 224
        self.cfg["sidebar_width"] = max(160, min(480, w))
        save_config(self.cfg)
        return "break"

    def _reset_sidebar_width(self, _event=None):
        """双击分隔条：恢复默认宽度并持久化。"""
        self._set_sidebar_width(224)
        self.cfg["sidebar_width"] = 224
        save_config(self.cfg)
        return "break"

    def _side_panel(self):
        t = self._theme()
        panel = tk.Frame(self.root, bg=t["panel"], width=LAYOUT["panel_default"])
        panel.pack(side="right", fill="y")
        panel.pack_propagate(False)
        self._restyle.append((panel, "panel"))
        self.side_panel = panel
        self._side_tab = "settings"  # settings / files

        head = tk.Frame(panel, bg=t["panel"])
        head.pack(fill="x", padx=14, pady=(10, 4))
        self._restyle.append((head, "panel"))
        self.btn_side_settings = self._mk_button(head, "⚙ 设置", lambda: self._switch_side_tab("settings"), fsz=9, kind="primary")
        self.btn_side_settings.pack(side="left")
        self.btn_side_files = self._mk_button(head, "📂 文件", lambda: self._switch_side_tab("files"), fsz=9)
        self.btn_side_files.pack(side="left", padx=(6, 0))
        self.btn_theme = self._mk_button(head, "🌙 深色", self.toggle_theme, fsz=9)
        self.btn_theme.pack(side="right")
        self._line(panel)

        self.panel_settings_body = tk.Frame(panel, bg=t["panel"])
        self.panel_settings_body.pack(fill="both", expand=True, padx=14, pady=10)
        self._restyle.append((self.panel_settings_body, "panel"))

        self.panel_files_body = tk.Frame(panel, bg=t["panel"])
        self._restyle.append((self.panel_files_body, "panel"))
        self._build_files_panel(self.panel_files_body)

        body = self.panel_settings_body

        def group(title, setup):
            g = tk.Frame(body, bg=t["panel"])
            g.pack(fill="x", pady=(0, 12))
            self._restyle.append((g, "panel"))
            self._lbl(g, title, role="label_sec", bg="panel", font=(FONT_FAMILY, 9, "bold")).pack(
                anchor="w", pady=(0, 4)
            )
            setup(g)

        def model_group(g):
            self.model_combo = ttk.Combobox(
                g, width=18, values=list(MODELS.keys()), state="normal"  # 可输入任意模型名
            )
            self.model_combo.pack(fill="x")
            self.model_combo.bind("<<ComboboxSelected>>", lambda e: self._update_context_bar())
            self._lbl(g, "场景 = 模型采样参数（温度 / 思考强度）", role="label_sec",
                      bg="panel", font=(FONT_FAMILY, 8)).pack(anchor="w", pady=(6, 0))
            self.scenario_combo = ttk.Combobox(
                g, width=8, values=list(SCENARIOS.keys()), state="readonly"
            )
            self.scenario_combo.pack(fill="x", pady=(6, 0))
            self.scenario_combo.bind("<<ComboboxSelected>>", self.on_scenario_change)
            self._lbl(g, "任务能力由「自主模式」表达：完全智能 = 全部工具 / 纯对话 = 无工具",
                      role="label_sec", bg="panel", font=(FONT_FAMILY, 8)).pack(anchor="w", pady=(6, 0))
            self.thinking_combo = ttk.Combobox(
                g, width=14, values=list(THINKING_MODES.values()), state="readonly"
            )
            self.thinking_combo.pack(fill="x", pady=(6, 0))
            self.thinking_combo.bind("<<ComboboxSelected>>", self._on_thinking_changed)
            row3 = tk.Frame(g, bg=t["panel"])
            row3.pack(fill="x", pady=(6, 0))
            self._restyle.append((row3, "panel"))
            self._lbl(row3, "输出上限", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="left")
            self.max_tokens_spin = ttk.Spinbox(row3, from_=1024, to=393216, increment=4096, width=10)
            self.max_tokens_spin.pack(side="left", padx=(6, 10))
            self._lbl(row3, "seed", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="left")
            self.seed_var = tk.StringVar()
            ttk.Entry(row3, textvariable=self.seed_var, width=10).pack(side="left", padx=(4, 0))
            self.custom_row = tk.Frame(g, bg=t["panel"])
            self._restyle.append((self.custom_row, "panel"))
            self._lbl(
                self.custom_row, "温度", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)
            ).pack(side="left")
            self.custom_temp_var = tk.StringVar(value="1.00")
            ttk.Spinbox(
                self.custom_row, from_=0.0, to=2.0, increment=0.05, width=6,
                textvariable=self.custom_temp_var, format="%.2f",
            ).pack(side="left", padx=(4, 10))
            self._lbl(
                self.custom_row, "top_p", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)
            ).pack(side="left")
            self.custom_topp_var = tk.StringVar(value="1.00")
            ttk.Spinbox(
                self.custom_row, from_=0.0, to=1.0, increment=0.05, width=6,
                textvariable=self.custom_topp_var, format="%.2f",
            ).pack(side="left", padx=(4, 0))
            self.json_var = tk.BooleanVar(value=False)
            self.json_check = ttk.Checkbutton(
                g, text="JSON 输出 (response_format)", variable=self.json_var
            )
            self.beta_var = tk.BooleanVar(value=False)
            self.beta_check = ttk.Checkbutton(
                g, text="Beta API（前缀续写 / FIM 补全）", variable=self.beta_var
            )
            # 高级参数折叠：温度/top_p/JSON/Beta 默认收起，减少高频面板信息密度
            self.adv_expanded = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                g, text="高级参数（温度 / JSON / Beta）", variable=self.adv_expanded,
                command=self._on_adv_toggle,
            ).pack(anchor="w", pady=(6, 0))
            self.adv_frame = tk.Frame(g, bg=t["panel"])
            self._restyle.append((self.adv_frame, "panel"))
            self.custom_row.pack(in_=self.adv_frame, fill="x", pady=(4, 0))
            self.json_check.pack(in_=self.adv_frame, anchor="w", pady=(6, 0))
            self.beta_check.pack(in_=self.adv_frame, anchor="w", pady=(2, 0))

        group("模型与参数", model_group)

        def role_row(g):
            self._lbl(g, "人格 = 系统提示词（说什么）", role="label_sec", bg="panel",
                      font=(FONT_FAMILY, 8)).pack(anchor="w", pady=(0, 2))
            self._role_lbl = self._lbl(g, "", role="label_sec", bg="panel",
                                       font=(FONT_FAMILY, 9))
            self._role_lbl.pack(anchor="w", pady=(2, 0))
            self._mk_button(g, "🎭 角色与提示词…", self.show_roles, fsz=9).pack(anchor="w", pady=(4, 0))

        group("AI 人格", role_row)

        def tools_group(g):
            self.tools_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(g, text="启用工具 (Agent 自动调用)", variable=self.tools_var).pack(anchor="w")
            self.btn_tools = self._mk_button(g, "工具设置", self.edit_tools, fsz=9)
            self.btn_tools.pack(anchor="w", pady=(6, 0))
            self.browser_visible_var = tk.BooleanVar(value=not bool(self.cfg.get("browser_headless", True)))
            ttk.Checkbutton(
                g,
                text="🖥 浏览器可见（有头预览）",
                variable=self.browser_visible_var,
                command=self._on_browser_mode_change,
            ).pack(anchor="w", pady=(6, 0))
            self._lbl(
                g,
                "开启后 AI 操作浏览器会弹出真实窗口，可实时观看；关闭为无头静默。",
                role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
                wraplength=205, justify="left",
            ).pack(anchor="w", pady=(0, 2))

        group("工具", tools_group)

        def auto_group(g):
            self.mode_var = tk.StringVar(value="standard")
            ttk.Radiobutton(
                g, text="标准模式（工具与审批按配置）",
                value="standard", variable=self.mode_var, command=self._on_mode_change,
            ).pack(anchor="w")
            ttk.Radiobutton(
                g, text="🤖 完全智能（允许目录内全自动，免审批）",
                value="full_auto", variable=self.mode_var, command=self._on_mode_change,
            ).pack(anchor="w", pady=(4, 0))
            ttk.Radiobutton(
                g, text="💬 纯对话（不注入工具，回归纯粹对话）",
                value="pure_chat", variable=self.mode_var, command=self._on_mode_change,
            ).pack(anchor="w", pady=(4, 0))
            self._lbl(
                g,
                "三种模式互斥：任务用完全智能/标准，对话写作用纯对话。",
                role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
                wraplength=205, justify="left",
            ).pack(anchor="w", pady=(6, 0))

        group("自主模式", auto_group)

        def api_group(g):
            self.key_var = tk.StringVar()
            self.key_entry = ttk.Entry(g, textvariable=self.key_var, show="*")
            self.key_entry.pack(fill="x")

        group("API Key", api_group)

        def look_group(g):
            row = tk.Frame(g, bg=t["panel"])
            row.pack(fill="x")
            self._restyle.append((row, "panel"))
            self._lbl(row, "字号", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="left")
            self.font_size_combo = ttk.Combobox(
                row, width=5, values=[str(i) for i in range(8, 19)], state="readonly"
            )
            self.font_size_combo.pack(side="left", padx=(6, 0))
            self.font_size_combo.bind("<<ComboboxSelected>>", self.on_font_size_change)
            ttk.Checkbutton(g, text="Markdown 渲染", variable=self.md_var, command=self.toggle_md_render).pack(
                anchor="w", pady=(6, 0)
            )

        group("外观", look_group)

        bar = tk.Frame(panel, bg=t["panel"])
        bar.pack(fill="x", side="bottom", padx=14, pady=10)
        self._restyle.append((bar, "panel"))
        self.btn_save = self._mk_button(bar, "保存配置", self.save_widgets_to_config, kind="primary", fsz=9)
        self.btn_save.pack(side="left")

    def _switch_side_tab(self, which):
        """右侧面板 Tab 切换：设置 / 📂 文件（文件视图自动加宽面板）。"""
        if which == self._side_tab:
            return
        self._side_tab = which
        is_files = which == "files"
        try:
            if is_files:
                self.panel_settings_body.pack_forget()
                self.panel_files_body.pack(fill="both", expand=True, padx=8, pady=8)
                self.side_panel.configure(width=LAYOUT["panel_files_w"])  # pack_propagate(False)：widget width 生效
            else:
                self.panel_files_body.pack_forget()
                self.panel_settings_body.pack(fill="both", expand=True, padx=14, pady=10)
                self.side_panel.configure(width=LAYOUT["panel_default"])
            self.btn_side_settings.configure(relief="sunken" if not is_files else "flat")
            self.btn_side_files.configure(relief="sunken" if is_files else "flat")
        except tk.TclError:
            pass
        if is_files:
            self._refresh_files_panel()

    # ---- 📂 文件面板：树形浏览工作区/草稿箱/最近产物/数据目录（懒加载）----
    def _build_files_panel(self, body):
        t = self._theme()
        top = tk.Frame(body, bg=t["panel"])
        top.pack(fill="x")
        self._restyle.append((top, "panel"))
        self._mk_button(top, "刷新", self._refresh_files_panel, fsz=9).pack(side="left")
        self._mk_button(top, "打开工作区", lambda: self._open_path(WORKSPACE_DIR), fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(top, "打开草稿箱", lambda: self._open_path(os.path.join(WORKSPACE_DIR, "drafts")), fsz=9).pack(side="left", padx=(6, 0))
        self.files_tree = ttk.Treeview(body, show="tree", selectmode="browse", style="Files.Treeview")
        self.files_tree.pack(fill="both", expand=True, pady=(6, 0))
        sb = ttk.Scrollbar(body, orient="vertical", command=self.files_tree.yview, style="Files.Vertical.TScrollbar")
        sb.pack(side="right", fill="y", before=self.files_tree)
        self.files_tree.configure(yscrollcommand=sb.set)
        self.files_tree.bind("<<TreeviewOpen>>", self._on_files_open)
        self.files_tree.bind("<Double-1>", self._on_files_double)
        self.files_tree.bind("<Button-3>", self._on_files_menu)
        self._refresh_files_panel()

    def _files_root_nodes(self):
        """文件面板根节点：[(item_id, label, path_or_None, is_dir)]。"""
        roots = []
        if WORKSPACE_DIR:
            roots.append(("ws", "📁 工作区", WORKSPACE_DIR, True))
            roots.append(("drafts", "📄 草稿箱", os.path.join(WORKSPACE_DIR, "drafts"), True))
        roots.append(("recent", "⭐ 最近产物", None, True))
        roots.append(("data", "📁 数据目录", DATA_DIR, True))
        return roots

    def _refresh_files_panel(self):
        if not getattr(self, "files_tree", None):
            return
        try:
            self.files_tree.delete(*self.files_tree.get_children())
            for iid, label, path, is_dir in self._files_root_nodes():
                self.files_tree.insert("", "end", iid=iid, text=label,
                                       open=False, tags=("dir",) if is_dir else ("file",))
                # recent 节点 path 为 None 但同样可展开（懒加载最近产物）
                if is_dir and (path or iid == "recent"):
                    self.files_tree.insert(iid, "end", text="…", tags=("placeholder",))
            if self._side_tab == "files":
                self.files_tree.see("ws")
        except tk.TclError:
            pass

    def _files_entry_path(self, iid):
        """从树节点解析真实路径；最近产物节点 iid 直接存绝对路径。"""
        # 绝对路径 iid（最近产物子节点）：直接返回，防 parent=recent 解析失败
        if isinstance(iid, str) and os.path.isabs(iid):
            return iid
        try:
            if iid == "ws":
                return WORKSPACE_DIR
            if iid == "data":
                return DATA_DIR
            if iid == "drafts":
                return os.path.join(WORKSPACE_DIR, "drafts")
            if iid == "recent":
                return None
        except Exception:
            return None
        # 普通节点：路径存在则返回
        try:
            p = self.files_tree.item(iid, "text")
            parent = self.files_tree.parent(iid)
            if parent:
                base = self._files_entry_path(parent)
                if base and p:
                    return os.path.join(base, p)
        except tk.TclError:
            pass
        return None

    def _on_files_open(self, event=None):
        """展开目录时懒加载子项（占位节点替换为真实内容）。"""
        try:
            tree = self.files_tree
            sel = tree.selection()
            iid = sel[0] if sel else (tree.focus() or None)
            if not iid:
                return
            if iid == "recent":
                # 最近产物：填充真实存在的文件。
                # iid 直接存完整路径（绝对路径）：_files_entry_path 能直接解析，
                # 否则 parent=recent 返回 None 导致双击无反应（真实 bug）
                children = tree.get_children(iid)
                if children and tree.item(children[0], "tags") == ("placeholder",):
                    tree.delete(*children)
                    for p in self._recent_cache:
                        if os.path.exists(p):
                            tree.insert(iid, "end", iid=p, text=os.path.basename(p), tags=("file",))
                return
            path = self._files_entry_path(iid)
            if not path or not os.path.isdir(path):
                return
            children = tree.get_children(iid)
            if children and tree.item(children[0], "tags") == ("placeholder",):
                tree.delete(*children)
                self._fill_files_dir(iid, path)
        except tk.TclError:
            pass

    def _fill_files_dir(self, iid, path):
        """填充目录子项（目录/文件混排，跳过隐藏项）。"""
        try:
            entries = []
            try:
                with os.scandir(path) as it:
                    for e in it:
                        try:
                            if e.name.startswith(".") or e.name in ("__pycache__", ".venv", "node_modules"):
                                continue
                            if e.is_dir(follow_symlinks=False):
                                entries.append((e.name, True))
                            elif e.is_file(follow_symlinks=False):
                                entries.append((e.name, False))
                        except OSError:
                            continue
            except OSError:
                return
            entries.sort(key=lambda x: (not x[1], x[0].lower()))
            for name, is_dir in entries[:300]:
                ciid = self.files_tree.insert(
                    iid, "end", text=name,
                    tags=("dir",) if is_dir else ("file",),
                )
                if is_dir:
                    self.files_tree.insert(ciid, "end", text="…", tags=("placeholder",))
        except tk.TclError:
            pass

    def _on_files_double(self, event):
        try:
            tree = self.files_tree
            iid = tree.identify_row(event.y)
            if not iid:
                return
            if iid in ("ws", "drafts", "data"):
                if tree.item(iid, "open"):
                    tree.item(iid, open=False)
                else:
                    tree.item(iid, open=True)
                    self._on_files_open()
                return
            path = self._files_entry_path(iid)
            if not path:
                return
            if os.path.isdir(path):
                if tree.item(iid, "open"):
                    tree.item(iid, open=False)
                else:
                    tree.item(iid, open=True)
                    self._on_files_open()
            else:
                self._open_path(path)
        except tk.TclError:
            pass

    def _on_files_menu(self, event):
        try:
            tree = self.files_tree
            iid = tree.identify_row(event.y)
            if not iid:
                return
            tree.selection_set(iid)
            t = self._theme()
            path = self._files_entry_path(iid)
            menu = tk.Menu(self.root, tearoff=0, bg=t["panel"], fg=t["text"],
                           activebackground=t.get("hover", t["surface"]), activeforeground=t["text"],
                           bd=1, relief="flat")
            if path:
                menu.add_command(label="打开", command=lambda p=path: self._open_path(p))
                menu.add_command(label="打开所在文件夹", command=lambda p=path: self._open_path(os.path.dirname(p) if os.path.isfile(p) else p))
                menu.add_command(label="复制路径", command=lambda p=path: self._copy_file_path(p))
                if os.path.isfile(path):
                    menu.add_command(label="注入到输入框", command=lambda p=path: self._inject_file_to_input(p))
            if iid == "recent":
                menu.add_command(label="查看全部最近产物", command=self.show_recent_outputs)
            menu.add_separator()
            menu.add_command(label="刷新", command=self._refresh_files_panel)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    menu.grab_release()
                except tk.TclError:
                    pass
                self.root.after(3000, lambda: _destroy_menu(menu))
        except tk.TclError:
            pass

    def _copy_file_path(self, path):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(path)
            self._flash_status(f"已复制路径：{path}")
        except tk.TclError:
            pass

    def _inject_file_to_input(self, path):
        """把文件内容读入输入框（供继续提问/润色等）。"""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(30000)
            if len(content) >= 30000:
                content += "\n[文件较大，已截断前 30000 字符]"
            self._clear_placeholder()
            self.input_text.delete("1.0", "end")
            self.input_text.insert("1.0", f"[文件] {os.path.basename(path)}:\n{content}")
            self.input_text.focus_set()
            self._flash_status(f"已注入：{os.path.basename(path)}")
        except Exception as e:
            self._flash_status(f"读取失败：{e}")

    def _mk_button(self, parent, text, command, kind="flat", fsz=9, **kw):
        t = self._theme()
        if kind == "primary":
            opts = dict(
                bg=t["accent"],
                fg=t["accent_text"],
                activebackground=t["accent_hover"],
                activeforeground=t["accent_text"],
                padx=18,
                pady=6,
            )
        elif kind == "danger":
            opts = dict(
                bg=t["page"],
                fg=t["error"],
                activebackground=t.get("hover", t["surface"]),
                activeforeground=t["error"],
                padx=10,
                pady=4,
            )
        else:
            opts = dict(
                bg=t["page"],
                fg=t["text"],
                activebackground=t.get("hover", t["surface"]),
                activeforeground=t["text"],
                padx=10,
                pady=4,
            )
        opts.update(dict(relief="flat", bd=0, highlightthickness=0, cursor="hand2", font=(FONT_FAMILY, fsz)))
        opts.update(kw)
        btn = tk.Button(parent, text=text, command=command, **opts)
        self._restyle.append((btn, f"{kind}_btn"))
        return btn

    def _lbl(self, parent, text, role="label_sec", bg="page", **kw):
        t = self._theme()
        opts = dict(bg=t.get(bg, t["page"]), fg=t["text"], font=(FONT_FAMILY, 9))
        opts.update(kw)
        l = tk.Label(parent, text=text, **opts)
        self._restyle.append((l, ("label", role, bg)))
        return l

    _DIALOG_W_SNAP = (LAYOUT["dialog_s"], LAYOUT["dialog_m"], LAYOUT["dialog_l"])
    _DIALOG_H_SNAP = (LAYOUT["dialog_h_s"], LAYOUT["dialog_h_m"], LAYOUT["dialog_h_l"],
                      LAYOUT["dialog_h_editor"], LAYOUT["dialog_h_editor_l"])

    def _screen_scale(self):
        """应用级自适应系数：以主窗口宽度为参照（应用内一切窗口与主窗口成比例）。

        主窗口 1000 宽 ≈ 1.0；1474（1152p 默认）≈ 1.47；1680 → 1.68；封顶 1.9。
        对话框档位/预览窗随主窗口呼吸，大屏与窄屏统一比例。
        """
        try:
            rw = max(1, self.root.winfo_width())
            return min(1.9, max(1.0, rw / 1000.0))
        except Exception:
            return 1.0

    def _apply_screen_font_default(self):
        """大屏默认字号提升：仅当用户未自定义字号（=默认 10）时按屏幕高度调整。"""
        try:
            if int(self.cfg.get("font_size", 10) or 10) != 10:
                return
            sh = self.root.winfo_screenheight()
            if sh >= 1300:
                self.cfg["font_size"] = 13
            elif sh >= 1080:
                self.cfg["font_size"] = 11
        except Exception:
            pass

    def toggle_fullscreen(self):
        """F11 无边框全屏切换（浏览器式）：全屏 = 上下左右无边框铺满屏幕。"""
        self._fullscreen = not getattr(self, "_fullscreen", False)
        try:
            if self._fullscreen:
                self._fs_prev_geo = self.root.geometry()
                self.root.attributes("-fullscreen", True)
                self._flash_status("已进入全屏（F11 退出）", 2500)
            else:
                self.root.attributes("-fullscreen", False)
                prev = getattr(self, "_fs_prev_geo", None)
                if prev:
                    try:
                        self.root.geometry(prev)
                    except Exception:
                        pass
        except tk.TclError:
            pass

    def _center_geometry(self, w, h):
        """居中几何字符串：相对主窗口中心（视觉重心略高），屏幕内校验。

        浏览器式交互原则：任何窗口/对话框打开即居中，不落在随机位置。
        """
        try:
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw = max(1, self.root.winfo_width())
            rh = max(1, self.root.winfo_height())
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            x = rx + (rw - w) // 2
            y = ry + (rh - h) // 3
            x = max(0, min(x, max(0, sw - w - 8)))
            y = max(0, min(y, max(0, sh - h - 8)))
            return f"{w}x{h}+{x}+{y}"
        except Exception:
            return f"{w}x{h}"

    def _dialog_shell(self, title, width, height, subtitle="", minsize=None):
        """统一对话框骨架：标题头（◉ 标题 + 说明）→ 分隔线 → 内容区 → 底部操作栏。

        返回 (dialog, body, footer)。所有对话框统一使用，保证一致的设计语言。
        尺寸自动吸附到定版档位（窄 420 / 中 520 / 宽 640；高 300/420/460/540/620）——
        消除历史散落的自定义尺寸，未来新增对话框自动对齐。
        档位按屏幕自适应系数放大（_screen_scale：1080p≈1.2、2K+≈1.6）。
        同一对话框在会话内记住上次位置（屏幕内校验）。
        """
        scale = self._screen_scale()
        w_snaps = tuple(int(s * scale) for s in (LAYOUT["dialog_s"], LAYOUT["dialog_m"], LAYOUT["dialog_l"]))
        h_snaps = tuple(
            int(s * scale) for s in (
                LAYOUT["dialog_h_s"], LAYOUT["dialog_h_m"], LAYOUT["dialog_h_l"],
                LAYOUT["dialog_h_editor"], LAYOUT["dialog_h_editor_l"],
            )
        )
        try:
            width = min(w_snaps, key=lambda d: abs(d - int(width)))
            height = min(h_snaps, key=lambda d: abs(d - int(height)))
        except (TypeError, ValueError):
            width, height = w_snaps[1], h_snaps[1]
        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title(title)
        dialog.geometry(self._center_geometry(width, height))  # 打开即居中（浏览器式）
        if minsize:
            dialog.minsize(*minsize)
        dialog.transient(self.root)
        key = "dlg_" + title
        saved = getattr(self, "_dlg_positions", {}).get(key)
        if saved:
            try:
                x, y = saved
                if 0 <= x < self.root.winfo_screenwidth() - 100 and 0 <= y < self.root.winfo_screenheight() - 80:
                    dialog.geometry(f"+{x}+{y}")
            except Exception:
                pass
        else:
            if not hasattr(self, "_dlg_positions"):
                self._dlg_positions = {}

        def _remember_pos(_e=None):
            try:
                self._dlg_positions[key] = (dialog.winfo_x(), dialog.winfo_y())
            except Exception:
                pass

        dialog.bind("<Configure>", _remember_pos)
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        header = tk.Frame(dialog, bg=t["panel"])
        header.pack(fill="x", padx=20, pady=(16, 8))
        self._restyle.append((header, "panel"))
        self._lbl(
            header, "◉ " + title, role="label_accent",
            font=(FONT_FAMILY, 13, "bold"), bg="panel",
        ).pack(anchor="w")
        if subtitle:
            self._lbl(
                header, subtitle, role="label_sec", bg="panel",
                font=(FONT_FAMILY, 9), wraplength=max(240, width - 48),
                justify="left",
            ).pack(anchor="w", pady=(4, 0))
        self._line(dialog)
        body = tk.Frame(dialog, bg=t["panel"])
        body.pack(fill="both", expand=True, padx=14, pady=10)
        self._restyle.append((body, "panel"))
        footer = tk.Frame(dialog, bg=t["panel"])
        footer.pack(fill="x", side="bottom", padx=20, pady=(8, 14))
        self._restyle.append((footer, "panel"))
        return dialog, body, footer

    def _footer_hint(self, footer, text):
        """底部栏左侧说明文字。"""
        self._lbl(
            footer, text, role="label_sec", bg="panel", font=(FONT_FAMILY, 9)
        ).pack(side="left")

    def _footer_btn(self, footer, text, command, primary=False):
        """底部栏右侧按钮（主按钮靠右）。"""
        btn = self._mk_button(
            footer, text, command,
            kind="primary" if primary else "flat", fsz=9,
        )
        btn.pack(side="right", padx=(6, 0))
        return btn

    def _scroll_panel(self, parent):
        """统一滚动容器：Canvas + 滚动条 + 滚轮，返回 (canvas, inner)。"""
        t = self._theme()
        container = tk.Frame(parent, bg=t["panel"])
        container.pack(fill="both", expand=True)
        self._restyle.append((container, "panel"))
        canvas = tk.Canvas(container, bg=t["panel"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=t["panel"])
        self._restyle.append((inner, "panel"))
        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def on_wheel(event):
            try:
                if abs(event.delta) >= 120:
                    # 标准鼠标滚轮：每格滚动 3 行（Tk 默认步长），避免滚轮过于费力
                    canvas.yview_scroll(-3 * int(event.delta / 120), "units")
                else:
                    # 高精度触控板 delta < 120：按像素滚动，避免 int() 截断成死区
                    canvas.yview_scroll(-int(event.delta / 10), "pixels")
            except Exception:
                pass
            return "break"

        # 滚轮绑定改用「面板所有权」模式：Enter 时接管全局滚轮，Leave/销毁时仅
        # 在仍是持有者时释放——避免多面板互相 unbind_all 踩踏、对话框关闭后残留绑定
        def _grab_wheel(_event=None):
            if getattr(self, "_wheel_owner", None) is not canvas:
                canvas.bind_all("<MouseWheel>", on_wheel)
                self._wheel_owner = canvas

        def _release_wheel(_event=None):
            if getattr(self, "_wheel_owner", None) is canvas:
                try:
                    canvas.unbind_all("<MouseWheel>")
                except Exception:
                    pass
                self._wheel_owner = None

        canvas.bind("<Enter>", _grab_wheel)
        canvas.bind("<Leave>", _release_wheel)
        canvas.bind("<Destroy>", _release_wheel)
        return canvas, inner

    def _sep_h(self, parent, side="left"):
        t = self._theme()
        s = tk.Frame(parent, bg=t["border"], width=1, height=24)
        s.pack(side=side, padx=8, pady=4)
        self._restyle.append((s, "border"))
        return s

    def _line(self, parent, side="top"):
        t = self._theme()
        l = tk.Frame(parent, bg=t["border"], height=1)
        l.pack(side=side, fill="x")
        self._restyle.append((l, "border"))
        return l

    def _input_handle(self):
        """聊天区与输入框之间的可拖拽分隔条（类似 PC 微信输入框）。

        按下后由根窗口接管 B1-Motion / ButtonRelease，拖出分隔条仍持续生效；
        像素级平滑调整输入区高度（微信同款交互，双击恢复默认）。
        """
        t = self._theme()
        handle = tk.Frame(self.root, bg=t["surface"], height=8, cursor="sb_v_double_arrow")
        handle.pack(side="bottom", fill="x")
        self._restyle.append((handle, "input_handle"))
        grip = tk.Label(
            handle,
            text="⋮",
            bg=t["surface"],
            fg=t["text_sec"],
            font=(FONT_FAMILY, 5),
            cursor="sb_v_double_arrow",
            pady=0,
            padx=0,
        )
        grip.pack(side="left", padx=12)
        self._restyle.append((grip, "input_handle_grip"))
        for w in (handle, grip):
            w.bind("<Button-1>", self._on_handle_press)
            w.bind("<Double-1>", self._reset_input_height)
            w.bind(
                "<Enter>",
                lambda e: (handle.configure(bg=self.theme_color("accent")),
                           grip.configure(bg=self.theme_color("accent"), fg=self.theme_color("accent_text"))),
            )
            w.bind(
                "<Leave>",
                lambda e: (handle.configure(bg=self.theme_color("surface")),
                           grip.configure(bg=self.theme_color("surface"), fg=self.theme_color("text_sec"))),
            )
        self.root.bind("<B1-Motion>", self._on_any_drag)
        self.root.bind("<ButtonRelease-1>", self._on_any_release)
        self.input_handle = handle
        self._drag_start_y = None
        self._drag_start_px = None
        self._drag_max_px = 400

    def _on_any_drag(self, event):
        """根窗口 B1-Motion 分发：输入区拖拽与聊天区宽度拖拽（各自按状态守卫）。"""
        if self._drag_start_y is not None:
            return self._on_handle_drag(event)
        if getattr(self, "_width_drag_start_x", None) is not None:
            return self._on_width_drag(event)
        return None

    def _on_any_release(self, _event=None):
        dragging = False
        if self._drag_start_y is not None:
            self._on_handle_release(_event)
            dragging = True
        if getattr(self, "_width_drag_start_x", None) is not None:
            self._on_width_release(_event)
            dragging = True
        # 仅在确实拖拽过时 break，防止无条件吞掉 all 标签上的全局释放处理
        return "break" if dragging else None

    def _on_handle_press(self, event):
        if getattr(self, "input_wrap", None) is None:
            return "break"
        self._drag_start_y = event.y_root
        self._drag_start_px = self._input_px
        self._drag_max_px = max(240, int(self.root.winfo_height() * 0.6))
        return "break"

    def _on_handle_drag(self, event):
        if self._drag_start_y is None:
            return
        dy = self._drag_start_y - event.y_root
        self._apply_input_height_px(self._drag_start_px + dy)
        return "break"

    def _on_handle_release(self, _event=None):
        if self._drag_start_y is None:
            return
        self._drag_start_y = None
        self._drag_start_px = None
        try:
            self.cfg["input_height_px"] = self._input_px
            lines = int(round(self._input_px / max(1, self._line_px)))
            self.cfg["input_height"] = max(2, min(14, lines))
            save_config(self.cfg)
        except Exception:
            pass
        return "break"

    def _px_for_lines(self, lines):
        return max(40, int(lines) * self._line_px + 30)

    def _apply_input_height_px(self, px):
        """像素级设置输入区高度（平滑无级，等效微信拖拽）。"""
        try:
            px = int(px)
        except (TypeError, ValueError):
            return
        if getattr(self, "_drag_start_y", None) is not None:
            high = self._drag_max_px
        else:
            win_h = self.root.winfo_height()
            high = max(240, int(win_h * 0.6)) if win_h >= 100 else 1000
        px = max(self._line_px + 44, min(high, px))
        self._input_px = px
        try:
            self.input_wrap.configure(height=px)
        except tk.TclError:
            pass

    def _restore_input_height(self):
        px = self.cfg.get("input_height_px")
        try:
            px = int(px)
        except (TypeError, ValueError):
            px = 0
        if px <= 0:
            try:
                px = self._px_for_lines(int(self.cfg.get("input_height", 4)))
            except (TypeError, ValueError):
                px = self._px_for_lines(4)
        self._apply_input_height_px(px)

    def _step_input_height(self, delta):
        """Ctrl+↑/↓ 调整输入框高度（拖拽之外的键盘兜底，逐行）。"""
        try:
            cur = int(round(self._input_px / max(1, self._line_px)))
        except Exception:
            cur = 4
        self._apply_input_height_px(self._px_for_lines(max(2, min(14, cur + delta))))
        try:
            self.cfg["input_height_px"] = self._input_px
            self.cfg["input_height"] = max(2, min(14, int(round(self._input_px / max(1, self._line_px)))))
            save_config(self.cfg)
        except Exception:
            pass
        return "break"

    def _reset_input_height(self, _event=None):
        self._restore_input_height()
        return "break"

    def _chat_area(self):
        t = self._theme()
        self.chat_frame = tk.Frame(self.root, bg=t["page"])
        self.chat_frame.pack(side="left", fill="both", expand=True)
        self._restyle.append((self.chat_frame, "page"))
        self.chat_frame.bind("<Configure>", self._layout_all)
        self._add_session()
        self._show_session_text(self._current)

    def _layout_all(self, event=None):
        for s in self._sessions:
            if s is not self._current:
                continue
            if event is not None:
                tw, th = event.width, event.height
            else:
                try:
                    tw = self.chat_frame.winfo_width()
                    th = self.chat_frame.winfo_height()
                except tk.TclError:
                    return
            if tw < 200 or th < 50:
                s["col"].place(relx=0.5, rely=0, anchor="n", width=1, height=1)
                self._layout_input(tw)
                return
            # 内容列宽度：理想范围 [content_min, content_max]，但物理容器更窄时
            # 必须让步（上限 = tw-40，防列宽超出聊天区被截断——真实冒烟发现）
            cw = max(LAYOUT["content_min"], min(LAYOUT["content_max"], tw - LAYOUT["content_margin"]))
            cw = min(cw, max(360, tw - 40))
            s["col"].place(relx=0.5, rely=0, anchor="n", width=cw, height=th)
            # 核心对齐：输入区与聊天内容列同宽居中（此前输入框横跨整个聊天区，错位 96px+）
            self._layout_input(tw)
            return

    def _layout_input(self, tw):
        """输入区宽度 = 聊天内容列宽度（同锚点居中），消除输入框与聊天列错位。"""
        try:
            cw = max(LAYOUT["content_min"], min(LAYOUT["content_max"], tw - LAYOUT["content_margin"]))
            cw = min(cw, max(360, tw - 40))
            # padx 下限 4：聊天区贴近 content_min 时输入区仍与列对齐
            # （此前下限 16 会在 tw=574 时产生 18px 错位）
            padx = max(4, (tw - cw) // 2)
            if getattr(self, "input_wrap", None) and self.input_wrap.winfo_manager():
                self.input_wrap.pack_configure(padx=padx)
        except tk.TclError:
            pass
        except Exception:
            pass

    def _input_area(self):
        t = self._theme()
        sizes = self._font_sizes()
        self.input_frame = tk.Frame(self.root, bg=t["page"])
        self.input_frame.pack(side="bottom", fill="x")
        self._restyle.append((self.input_frame, "page"))
        self.input_wrap = wrap = tk.Frame(self.input_frame, bg=t["page"])
        wrap.pack(fill="x", padx=28, pady=(4, 10))
        wrap.pack_propagate(False)
        self._restyle.append((wrap, "page"))
        card = tk.Frame(wrap, bg=t["panel"], highlightthickness=1, bd=0,
                        highlightbackground=t["border"], highlightcolor=t["accent"])
        card.pack(fill="both", expand=True, padx=8, pady=6)
        self._restyle.append((card, "panel"))
        self._restyle.append((card, "input_card"))
        self.input_text = tk.Text(
            card,
            height=4,
            wrap="word",
            font=(FONT_FAMILY, sizes["base"]),
            bg=t["input_bg"],
            fg=t["input_fg"],
            insertbackground=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=14,
            pady=8,
            undo=True,
        )
        self.input_text.pack(side="top", fill="both", expand=True)
        # ---- 产物条：工具生成的最近文件常驻显示（打开/所在文件夹/复制），
        # 默认隐藏，工具产生新产物时自动出现，无需到目录翻找 ----
        self.recent_bar = tk.Frame(card, bg=t["panel"])
        self._restyle.append((self.recent_bar, "panel"))
        self.recent_bar_lbl = self._lbl(
            self.recent_bar, "", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)
        )
        self.recent_bar_lbl.pack(side="left", fill="x", expand=True)
        self.recent_bar_btn = self._mk_button(self.recent_bar, "打开", lambda: self._open_recent_bar(), fsz=9, kind="primary")
        self.recent_bar_btn.pack(side="left", padx=(6, 0))
        self.recent_bar_dir_btn = self._mk_button(self.recent_bar, "所在文件夹", self._open_recent_bar_dir, fsz=9)
        self.recent_bar_dir_btn.pack(side="left", padx=(6, 0))
        self.recent_bar_copy_btn = self._mk_button(self.recent_bar, "复制路径", self._copy_recent_bar, fsz=9)
        self.recent_bar_copy_btn.pack(side="left", padx=(6, 0))
        self.recent_bar_close = self._mk_button(self.recent_bar, "✕", self._hide_recent_bar, fsz=9)
        self.recent_bar_close.pack(side="left", padx=(6, 0))
        # before=input_text：产物条显示在输入框上方（后 pack 默认在下方）
        self.recent_bar.pack(side="top", before=self.input_text, fill="x", padx=14, pady=(8, 0))
        self.recent_bar.pack_forget()  # 默认隐藏，有产物才显示
        self._refresh_line_px()
        self._restore_input_height()
        self.input_text.bind("<Return>", self.on_enter)
        self.input_text.bind("<Shift-Return>", lambda e: self.input_text.insert("insert", "\n") or "break")
        self.input_text.bind("<Control-Return>", lambda e: (self.send(), "break"))
        self.input_text.bind("<Control-Shift-v>", self._paste_as_link)
        self.input_text.bind("<Control-Shift-V>", self._paste_as_link)
        self.input_text.bind("<Alt-Up>", lambda e: self._input_history_nav(-1))
        self.input_text.bind("<Alt-Down>", lambda e: self._input_history_nav(1))
        self.input_text.bind("<Button-3>", self._show_input_menu)
        self.input_text.bind("<Control-f>", lambda e: (self.toggle_search(), "break")[1])
        self.input_text.bind("<Control-F>", lambda e: (self.toggle_search(), "break")[1])
        self.input_text.bind("<Control-b>", lambda e: self._wrap_selection("**", "**", "文本"))
        self.input_text.bind("<Control-B>", lambda e: self._wrap_selection("**", "**", "文本"))
        self.input_text.bind("<Control-i>", lambda e: self._wrap_selection("*", "*", "文本"))
        self.input_text.bind("<Control-I>", lambda e: self._wrap_selection("*", "*", "文本"))
        self.input_text.bind("<Control-k>", self._insert_link)
        self.input_text.bind("<Control-K>", self._insert_link)
        self.input_text.bind("<Control-Up>", lambda e: self._step_input_height(1))
        self.input_text.bind("<Control-Down>", lambda e: self._step_input_height(-1))
        # ---- 编辑器增强：Tab 缩进 / Shift+Tab 反缩进 / 括号自动配对 / Ctrl+Backspace 删词 ----
        self.input_text.bind("<Tab>", self._on_input_tab)
        self.input_text.bind("<Shift-Tab>", self._on_input_shift_tab)
        self.input_text.bind("<BackSpace>", self._on_input_backspace)
        self.input_text.bind("<Control-BackSpace>", self._on_input_delete_word)
        for _ch in ("(", "[", "{", '"', "'"):
            self.input_text.bind(f"<Key-{_ch}>", self._on_input_open_delimiter)
        if DND_AVAILABLE:
            try:
                self.input_text.drop_target_register("DND_Files")
                self.input_text.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                logging.exception("拖拽初始化失败")
        self.input_text.bind("<FocusIn>", lambda e: self._clear_placeholder())
        self.input_text.bind("<FocusOut>", lambda e: self._set_placeholder())
        self.input_text.bind(
            "<KeyRelease>",
            lambda e: (self._schedule_input_tokens(), self._schedule_draft_save()),
        )
        foot = tk.Frame(card, bg=t["panel"])
        foot.pack(side="top", fill="x", padx=(12, 10), pady=(0, 8))
        self._restyle.append((foot, "panel"))
        self.input_hint_lbl = self._lbl(
            foot, "Enter 发送 · Shift+Enter 换行", role="label_sec", bg="panel"
        )
        self.input_hint_lbl.config(font=(FONT_FAMILY, 9))
        self.input_hint_lbl.pack(side="left")
        self.btn_prompts = self._mk_button(foot, "⚡ 指令", self._show_prompt_menu, fsz=9)
        self.btn_prompts.pack(side="left", padx=(10, 0))
        self.btn_dir = self._mk_button(foot, "📁 目录", self.choose_working_dir, fsz=9)
        self.btn_dir.pack(side="left", padx=(6, 0))
        self.btn_stop = self._mk_button(foot, "■ 停止", self.stop_generate, kind="danger", fsz=10)
        self.btn_stop.configure(state="disabled")
        self.btn_stop.pack(side="right")
        self.btn_send = self._mk_button(foot, "发送", self.send, kind="primary", fsz=10)
        self.btn_send.pack(side="right")

    def _search_bar(self):
        t = self._theme()
        self.search_frame = tk.Frame(self.root, bg=t["page"])
        self._restyle.append((self.search_frame, "page"))
        self._line(self.search_frame)
        body = tk.Frame(self.search_frame, bg=t["page"])
        body.pack(fill="x", padx=16, pady=6)
        self._restyle.append((body, "page"))
        self._lbl(body, "查找").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            body,
            textvariable=self.search_var,
            width=26,
            bg=t["input_bg"],
            fg=t["input_fg"],
            insertbackground=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
        )
        self.search_entry.pack(side="left", padx=(8, 8))
        self.search_entry.bind("<Return>", lambda e: self._search_next())
        self.search_entry.bind("<Shift-Return>", lambda e: self._search_prev())
        self.search_entry.bind("<Escape>", lambda e: self.toggle_search())
        self.search_entry.bind("<Control-f>", lambda e: (self.toggle_search(), "break")[1])
        self.search_entry.bind("<Control-F>", lambda e: (self.toggle_search(), "break")[1])
        self.search_var.trace_add("write", lambda *a: self._schedule_search())
        self._mk_button(body, "上一个", self._search_prev).pack(side="left", padx=(0, 4))
        self._mk_button(body, "下一个", self._search_next).pack(side="left", padx=(0, 8))
        self.search_count = self._lbl(body, "0/0", width=6, anchor="e")
        self.search_count.pack(side="left", padx=(0, 8))
        self._mk_button(body, "关闭", self.toggle_search).pack(side="left")

    def _status_bar(self):
        t = self._theme()
        self.status_frame = tk.Frame(self.root, bg=t["page"])
        self.status_frame.pack(side="bottom", fill="x")
        self._restyle.append((self.status_frame, "page"))
        self._line(self.status_frame)
        # 三段式状态栏：左=模式与目录 · 中=用量统计 · 右=模型与上下文
        self.status_label = self._lbl(self.status_frame, "", anchor="w", bg=t["page"])
        self.status_label.pack(side="left", fill="x", expand=True, padx=16, pady=5)
        self.status_right = self._lbl(self.status_frame, "", anchor="e", bg=t["page"])
        self.status_right.pack(side="right", padx=(8, 4), pady=5)
        self.context_label = self._lbl(self.status_frame, "", anchor="e", width=32, bg=t["page"])
        self.context_label.pack(side="right")
        self.context_bar = ttk.Progressbar(
            self.status_frame,
            style="Context.Horizontal.TProgressbar",
            maximum=MAX_CONTEXT_TOKENS,
            value=0,
            length=120,
        )
        self.context_bar.pack(side="right", padx=(4, 2), pady=8)

    def _theme(self):
        return THEMES.get(self.cfg.get("theme", "light"), THEMES["light"])

    def theme_color(self, key):
        return self._theme().get(key, THEMES["light"].get(key, "#000000"))

    def toggle_theme(self):
        self.cfg["theme"] = "dark" if self.cfg.get("theme") == "light" else "light"
        self.apply_theme()
        save_config(self.cfg)

    def apply_theme(self):
        t = self._theme()
        try:
            self.root.configure(bg=t["page"])
        except tk.TclError:
            pass
        try:
            self._apply_ttk_styles(t)
        except tk.TclError:
            pass
        self._apply_menu_theme(t)
        self._apply_dark_titlebar()
        alive = []
        for w, kind in self._restyle:
            # 惰性清理已销毁控件的条目（对话框关闭后残留，避免 _restyle 无界增长）
            try:
                if not w.winfo_exists():
                    continue
            except tk.TclError:
                continue
            try:
                if isinstance(kind, tuple) and kind[0] == "label":
                    _role, role, bgkey = kind
                    fg = {
                        "label_sec": t["text_sec"],
                        "label_error": t["error"],
                        "label_accent": t["accent"],
                    }.get(role, t["text"])
                    w.configure(bg=t.get(bgkey, t["page"]), fg=fg)
                elif kind == "label_text":
                    w.configure(fg=t["text"])
                elif kind == "label_sec":
                    w.configure(fg=t["text_sec"])
                elif kind == "label_error":
                    w.configure(fg=t["error"])
                elif kind == "label_accent":
                    w.configure(fg=t["accent"])
                elif kind == "page":
                    w.configure(bg=t["page"])
                elif kind == "panel":
                    w.configure(bg=t["panel"])
                elif kind == "surface":
                    w.configure(bg=t["surface"])
                elif kind == "border":
                    w.configure(bg=t["border"])
                elif kind == "input_handle":
                    w.configure(bg=t["surface"])
                elif kind == "input_handle_grip":
                    w.configure(bg=t["surface"], fg=t["text_sec"])
                elif kind == "chat_resize_handle":
                    w.configure(bg=t["page"])
                elif kind == "chat_scrollbar":
                    w.configure(
                        bg=t["disabled"],
                        activebackground=t["text_sec"],
                        troughcolor=t["chat_bg"],
                        highlightbackground=t["chat_bg"],
                        highlightcolor=t["chat_bg"],
                    )
                elif kind == "list_scrollbar":
                    w.configure(
                        bg=t["disabled"],
                        activebackground=t["text_sec"],
                        troughcolor=t["panel"],
                        highlightbackground=t["panel"],
                        highlightcolor=t["panel"],
                    )
                elif kind == "listbox":
                    w.configure(
                        bg=t["panel"],
                        fg=t["text"],
                        selectbackground=t["selection"],
                        selectforeground=t["accent_text"],
                        highlightbackground=t["panel"],
                        highlightcolor=t["panel"],
                    )
                elif kind == "chat_bg":
                    w.configure(bg=t["chat_bg"])
                elif kind == "input_card":
                    w.configure(
                        highlightbackground=t["border"], highlightcolor=t["accent"]
                    )
                elif kind == "flat_btn":
                    w.configure(
                        bg=t["page"],
                        fg=t["text"],
                        activebackground=t.get("hover", t["surface"]),
                        activeforeground=t["text"],
                    )
                elif kind == "menu_btn":
                    w.configure(
                        bg=t["panel"],
                        fg=t["text"],
                        activebackground=t.get("hover", t["surface"]),
                        activeforeground=t["text"],
                    )
                elif kind == "danger_btn":
                    w.configure(
                        bg=t["page"],
                        fg=t["error"],
                        activebackground=t.get("hover", t["surface"]),
                        activeforeground=t["error"],
                    )
                elif kind == "primary_btn":
                    w.configure(
                        bg=t["accent"],
                        fg=t["accent_text"],
                        activebackground=t["accent_hover"],
                        activeforeground=t["accent_text"],
                    )
            except tk.TclError:
                pass
            alive.append((w, kind))
        self._restyle = alive
        sizes = self._font_sizes()
        for session in self._sessions:
            text = session["text"]
            text.configure(
                bg=t["chat_bg"],
                fg=t["text"],
                insertbackground=t["text"],
                selectbackground=t["selection"],
                selectforeground=t["accent_text"],
            )
            self._configure_tags(text, t, sizes)
        self.input_text.configure(
            bg=t["input_bg"],
            fg=t["input_fg"],
            insertbackground=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
        )
        self.search_entry.configure(
            bg=t["input_bg"],
            fg=t["input_fg"],
            insertbackground=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
        )
        if self._placeholder_active:
            self.input_text.configure(fg=t["text_sec"])
        self.btn_theme.configure(text="☀️ 浅色" if t is THEMES["dark"] else "🌙 深色")
        if getattr(self, "task_panel", None) is not None:
            try:
                self.task_panel.apply_theme(t)
            except Exception:
                pass
        if getattr(self, "proc_panel", None) is not None:
            try:
                self.proc_panel.apply_theme(t)
            except Exception:
                pass

    def _apply_ttk_styles(self, t):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # Combobox 下拉列表颜色（clam 主题下通过 option database 配置）
        self.root.option_add("*TCombobox*Listbox.background", t["input_bg"])
        self.root.option_add("*TCombobox*Listbox.foreground", t["input_fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", t["selection"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", t["accent_text"])
        self.root.option_add("*TCombobox*Listbox.font", (FONT_FAMILY, 9))
        style.configure(
            "TNotebook",
            background=t["page"],
            borderwidth=0,
            tabmargins=(4, 4, 4, 0),
            lightcolor=t["page"],
            darkcolor=t["page"],
        )
        style.configure(
            "TNotebook.Tab",
            padding=(14, 5),
            background=t["page"],
            foreground=t["text_sec"],
            borderwidth=0,
            focuscolor=t["page"],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", t["chat_bg"])],
            foreground=[("selected", t["accent"])],
        )
        style.configure(
            "TEntry",
            fieldbackground=t["input_bg"],
            foreground=t["input_fg"],
            bordercolor=t["border"],
            lightcolor=t["border"],
            darkcolor=t["border"],
            insertcolor=t["input_fg"],
        )
        style.map(
            "TEntry",
            fieldbackground=[("disabled", t["surface"])],
            foreground=[("disabled", t["disabled"])],
        )
        style.configure(
            "TSpinbox",
            fieldbackground=t["input_bg"],
            foreground=t["input_fg"],
            bordercolor=t["border"],
            lightcolor=t["border"],
            darkcolor=t["border"],
            insertcolor=t["input_fg"],
            arrowsize=12,
            arrowcolor=t["text_sec"],
        )
        style.map(
            "TSpinbox",
            fieldbackground=[("disabled", t["surface"])],
            foreground=[("disabled", t["disabled"])],
            arrowcolor=[("disabled", t["disabled"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=t["input_bg"],
            background=t["input_bg"],
            foreground=t["input_fg"],
            arrowcolor=t["text_sec"],
            bordercolor=t["border"],
            lightcolor=t["border"],
            darkcolor=t["border"],
            insertcolor=t["input_fg"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", t["input_bg"])],
            background=[("readonly", t["input_bg"])],
            foreground=[("readonly", t["input_fg"])],
            selectbackground=[("readonly", t["selection"])],
            selectforeground=[("readonly", t["input_fg"])],
            arrowcolor=[("readonly", t["text_sec"])],
        )
        style.configure("TCheckbutton", background=t["page"], foreground=t["text"])
        style.map(
            "TCheckbutton",
            background=[("active", t["page"])],
            foreground=[("active", t["text"])],
            indicatorbackground=[("!selected", t["input_bg"]), ("selected", t["accent"])],
            indicatorforeground=[("selected", t["accent_text"])],
        )
        # 📂 文件面板树（右侧面板「文件」Tab）
        style.configure(
            "Files.Treeview",
            background=t["panel"],
            fieldbackground=t["panel"],
            foreground=t["text"],
            bordercolor=t["border"],
            lightcolor=t["panel"],
            darkcolor=t["panel"],
            rowheight=24,
        )
        style.map(
            "Files.Treeview",
            background=[("selected", t["selection"])],
            foreground=[("selected", t["accent_text"])],
        )
        style.configure(
            "Files.Treeview.Item",
            background=t["panel"],
            foreground=t["text"],
            padding=(4, 2),
        )
        style.map(
            "Files.Treeview.Item",
            background=[("selected", t["selection"])],
            foreground=[("selected", t["accent_text"])],
        )
        style.configure(
            "Files.Vertical.TScrollbar",
            troughcolor=t["panel"],
            background=t["surface"],
            borderwidth=0,
            arrowcolor=t["text_sec"],
        )
        style.configure(
            "Context.Horizontal.TProgressbar",
            troughcolor=t["surface"],
            background=t["accent"],
            borderwidth=0,
        )
        style.configure(
            "TScrollbar",
            troughcolor=t["page"],
            background=t["surface"],
            borderwidth=0,
            arrowcolor=t["text_sec"],
        )
        style.map(
            "TScrollbar",
            background=[("active", t["accent"])],
        )

    def _set_placeholder(self):
        if self._placeholder_active:
            return
        if not self.input_text.get("1.0", "end-1c"):
            self._placeholder_active = True
            self.input_text.configure(fg=self._theme().get("input_placeholder", self._theme()["text_sec"]))
            self.input_text.insert("1.0", PLACEHOLDER_TEXT)
            self._refresh_input_tokens()

    def _clear_placeholder(self):
        if self._placeholder_active:
            self._placeholder_active = False
            self.input_text.delete("1.0", "end")
            self.input_text.configure(fg=self._theme()["input_fg"])

    def _schedule_input_tokens(self):
        if self._input_token_after is not None:
            try:
                self.root.after_cancel(self._input_token_after)
            except Exception:
                pass
        self._input_token_after = self.root.after(300, self._refresh_input_tokens)

    def _refresh_input_tokens(self):
        self._input_token_after = None
        try:
            if self._placeholder_active:
                n = 0
            else:
                text = self.input_text.get("1.0", "end-1c")
                if getattr(self, "_last_input_len", None) == len(text):
                    return  # 内容未变（粘贴/回退后），跳过全量 tiktoken 重估
                self._last_input_len = len(text)
                # 超长输入（>2 万字符）降级为字符估算：粘贴大文本时避免一次 20-50ms 冻结
                if len(text) <= 20000:
                    n = tokens.estimate_text_tokens(text)
                else:
                    n = int(len(text) / 1.5)
            self.input_hint_lbl.configure(
                text=f"约 {n:,} token · Enter 发送 · Shift+Enter 换行"
            )
        except Exception:
            pass

    def _schedule_draft_save(self, _event=None):
        """输入草稿持久化：停止输入 4 秒后落盘（隐私模式跳过）。"""
        if self.cfg.get("privacy_mode"):
            return
        if self._draft_after is not None:
            try:
                self.root.after_cancel(self._draft_after)
            except Exception:
                pass
        self._draft_after = self.root.after(4000, self._save_draft)

    def _save_draft(self):
        self._draft_after = None
        if self.cfg.get("privacy_mode"):
            return
        try:
            text = "" if self._placeholder_active else self.input_text.get("1.0", "end-1c")
            data = {"text": text, "ts": datetime.now().isoformat(timespec="seconds")}
            # 唯一临时文件：after 定时保存与 on_close 直接保存可能并发，
            # 固定 .tmp 名会互相截断（原子 os.replace 只能保证"单个文件"原子）
            import tempfile

            fd, tmp = tempfile.mkstemp(
                dir=os.path.dirname(DRAFT_PATH) or ".", prefix="draft.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, DRAFT_PATH)  # 原子写：崩溃不损坏草稿
                tmp = None
            finally:
                if tmp is not None:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
        except Exception:
            logging.debug("保存输入草稿失败", exc_info=True)

    def _restore_draft(self):
        if self.cfg.get("privacy_mode"):
            return
        try:
            if not os.path.exists(DRAFT_PATH):
                return
            with open(DRAFT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            text = str(data.get("text") or "")
            if text.strip():
                self._clear_placeholder()
                self.input_text.insert("1.0", text)
                self.input_text.mark_set("insert", "end-1c")
                self._flash_status("已恢复上次未发送的输入草稿", 3000)
        except Exception:
            logging.exception("恢复输入草稿失败")

    def _paste_clipboard_ask(self):
        """Ctrl+Shift+Q：剪贴板内容直接进输入框即问。"""
        try:
            clip = self.root.clipboard_get().strip()
        except tk.TclError:
            clip = ""
        if not clip:
            self._flash_status("剪贴板为空")
            return
        self._clear_placeholder()
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", clip[:20000])
        self.input_text.mark_set("insert", "end-1c")
        self.input_text.focus_set()
        self._flash_status("已粘贴剪贴板内容，按 Enter 发送")

    def _font_sizes(self):
        base = int(self.cfg.get("font_size", 10))
        return {
            "base": base,
            "small": max(8, base - 2),
            "mono": base,
            "mono_small": max(8, base - 1),
        }

    def _refresh_line_px(self):
        """按当前字体重新计算单行像素高，供输入区像素高度换算。"""
        try:
            f = tkfont.Font(font=self.input_text.cget("font"))
            self._line_px = f.metrics("linespace")
        except Exception:
            self._line_px = 21

    def apply_font_size(self):
        sizes = self._font_sizes()
        t = self._theme()
        for session in self._sessions:
            text = session["text"]
            text.configure(font=(FONT_FAMILY, sizes["base"]))
            self._configure_tags(text, t, sizes)
        self.input_text.configure(font=(FONT_FAMILY, sizes["base"]))
        self._refresh_line_px()
        if getattr(self, "_input_px", None):
            self._apply_input_height_px(self._input_px)

    def _configure_tags(self, text, t, sizes):
        text.tag_configure(
            "time",
            foreground=t.get("note", t["thinking"]),
            font=(FONT_FAMILY, sizes["small"]),
            spacing1=6,
            spacing3=4,
        )
        text.tag_configure(
            "user",
            foreground=t["accent"],
            lmargin1=8,
            lmargin2=8,
            rmargin=12,
            spacing1=4,
            spacing3=4,
            justify="right",
        )
        text.tag_configure(
            "user_time",
            foreground=t["text_sec"],
            font=(FONT_FAMILY, sizes["small"]),
            lmargin1=8,
            lmargin2=8,
            rmargin=12,
            spacing1=8,
            spacing3=2,
            justify="right",
        )
        text.tag_configure(
            "assistant",
            foreground=t["text"],
            lmargin1=8,
            lmargin2=8,
            rmargin=8,
            spacing1=2,
            spacing3=2,
        )
        text.tag_configure(
            "thinking",
            foreground=t["thinking"],
            font=(FONT_FAMILY, sizes["small"]),
            lmargin1=8,
            lmargin2=8,
            spacing1=2,
            spacing3=2,
        )
        text.tag_configure(
            "tool",
            foreground=t["tool"],
            font=(MONO_FAMILY, sizes["mono_small"]),
            lmargin1=8,
            lmargin2=8,
            spacing1=2,
            spacing3=2,
        )
        text.tag_configure(
            "thinking_toggle",
            foreground=t["thinking"],
            font=(FONT_FAMILY, sizes["small"], "underline"),
            lmargin1=8,
            lmargin2=8,
            spacing1=4,
            spacing3=0,
        )
        text.tag_configure(
            "tool_toggle",
            foreground=t["tool"],
            font=(MONO_FAMILY, sizes["mono_small"], "underline"),
            lmargin1=8,
            lmargin2=8,
            spacing1=4,
            spacing3=0,
        )
        text.tag_configure("error", foreground=t["error"], lmargin1=12, lmargin2=12)
        text.tag_configure("fold_hidden", elide=True)
        text.tag_configure(
            "continue_hint",
            foreground=t["accent"],
            font=(FONT_FAMILY, sizes["small"], "bold"),
            lmargin1=8,
            lmargin2=8,
            spacing1=8,
            spacing3=8,
        )
        text.tag_configure(
            "fold_hint",
            foreground=t["accent"],
            font=(FONT_FAMILY, sizes["small"], "bold"),
            lmargin1=8,
            lmargin2=8,
            spacing1=8,
            spacing3=8,
        )
        text.tag_configure(
            "code_lang",
            background=t["surface"],
            foreground=t["text_sec"],
            font=(MONO_FAMILY, sizes["mono_small"]),
            lmargin1=14,
            lmargin2=14,
            rmargin=14,
            spacing1=4,
        )
        text.tag_configure(
            "code",
            background=t["code_bg"],
            foreground=t["code_fg"],
            font=(MONO_FAMILY, sizes["mono"]),
            lmargin1=14,
            lmargin2=14,
            rmargin=14,
            spacing1=2,
            spacing3=4,
        )
        text.tag_configure(
            "hr",
            foreground=t["border"],
            spacing1=6,
            spacing3=6,
        )
        text.tag_configure("table_head", font=(FONT_FAMILY, sizes["base"], "bold"))
        text.tag_configure("search_hit", background=t["selection"])
        text.tag_configure(
            "search_cur", background=t["accent"], foreground=t["accent_text"]
        )
        text.tag_configure("link", foreground=t["accent"], underline=True)
        text.tag_configure("filelink", foreground=t["accent"], underline=True)
        text.tag_configure("bold", font=(FONT_FAMILY, sizes["base"], "bold"))
        text.tag_configure("italic", font=(FONT_FAMILY, sizes["base"], "italic"))
        text.tag_configure("strike", overstrike=True, foreground=t["text_sec"])
        text.tag_configure(
            "quote",
            foreground=t["text_sec"],
            background=t.get("quote_bg", t["surface"]),
            lmargin1=18,
            lmargin2=18,
            rmargin=8,
            spacing1=3,
            spacing3=3,
        )
        text.tag_configure("list", foreground=t["text"], lmargin1=14, lmargin2=14)
        text.tag_configure(
            "table",
            foreground=t["text"],
            font=(MONO_FAMILY, sizes["mono_small"]),
            lmargin1=12,
            lmargin2=12,
            rmargin=8,
        )
        for i in range(1, 7):
            size = max(8, sizes["base"] + 4 - i)
            text.tag_configure(
                f"h{i}",
                font=(FONT_FAMILY, size, "bold"),
                foreground=t["text"],
                spacing1=6,
                spacing3=4,
            )

    def _add_session(self):
        sizes = self._font_sizes()
        t = self._theme()
        col = tk.Frame(self.chat_frame, bg=t["chat_bg"])
        self._restyle.append((col, "chat_bg"))

        text = tk.Text(
            col,
            wrap="word",
            state="disabled",
            bg=t["chat_bg"],
            fg=t["text"],
            insertbackground=t["text"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            padx=40,
            pady=22,
            relief="flat",
            font=(FONT_FAMILY, sizes["base"]),
        )
        text.pack(side="left", fill="both", expand=True)
        # 细窄无箭头滚动条：主题随动，默认低调（disabled 灰），悬停/拖动加深。
        # 用 place 叠加而非 pack：text 请求宽度受最长行影响（长 URL/代码/长词），
        # pack 在列宽不足时会先把滚动条压缩到 0 宽导致不可见；place 不参与 pack 计算。
        scrollbar = tk.Scrollbar(
            col,
            orient="vertical",
            command=text.yview,
            relief="flat",
            bd=0,
            highlightthickness=0,
            width=10,
        )
        scrollbar.place(relx=1.0, x=-10, y=0, width=10, relheight=1.0)
        text.configure(yscrollcommand=scrollbar.set)
        self._restyle.append((scrollbar, "chat_scrollbar"))
        # 滚动条拖动 = 手动滚动：拖动中停止跟随，拖回底部自动恢复
        scrollbar.bind("<Button-1>", lambda e: setattr(self, "_follow_bottom", False))
        scrollbar.bind("<B1-Motion>", lambda e: setattr(self, "_follow_bottom", False))
        scrollbar.bind("<ButtonRelease-1>", self._mark_user_scrolled)

        def _on_chat_wheel(event):
            try:
                if abs(event.delta) >= 120:
                    # 标准鼠标滚轮：每格滚动 3 行（Tk 默认步长），避免滚轮过于费力
                    text.yview_scroll(-3 * int(event.delta / 120), "units")
                else:
                    # 高精度触控板 delta < 120：按像素滚动，避免 int() 截断成死区
                    text.yview_scroll(-int(event.delta / 10), "pixels")
            except Exception:
                pass
            # 手动滚动意图：向上滚=离开底部停止跟随；向下滚回底部自动恢复
            try:
                if event.delta > 0:
                    self._follow_bottom = False
                else:
                    self._follow_bottom = text.yview()[1] >= 0.995
            except Exception:
                self._follow_bottom = False
            return "break"

        text.bind("<MouseWheel>", _on_chat_wheel)
        self._configure_tags(text, t, sizes)
        text.bind("<Button-3>", self._on_chat_menu)
        text.tag_bind("link", "<Button-1>", self._on_link_click)
        text.tag_bind("link", "<Enter>", lambda e: text.configure(cursor="hand2"))
        text.tag_bind("link", "<Leave>", lambda e: text.configure(cursor=""))
        text.tag_bind("filelink", "<Button-1>", self._on_filelink_click)
        text.tag_bind("filelink", "<Enter>", lambda e: text.configure(cursor="hand2"))
        text.tag_bind("filelink", "<Leave>", lambda e: text.configure(cursor=""))
        text.tag_bind("thinking_toggle", "<Button-1>", self._on_fold_click)
        text.tag_bind("tool_toggle", "<Button-1>", self._on_fold_click)
        for toggle_tag in ("thinking_toggle", "tool_toggle"):
            text.tag_bind(
                toggle_tag, "<Enter>", lambda e: text.configure(cursor="hand2")
            )
            text.tag_bind(
                toggle_tag, "<Leave>", lambda e: text.configure(cursor="")
            )
        text.tag_bind("fold_hint", "<Button-1>", self._on_fold_early_click)
        text.tag_bind("fold_hint", "<Enter>", lambda e: text.configure(cursor="hand2"))
        text.tag_bind("fold_hint", "<Leave>", lambda e: text.configure(cursor=""))
        text.tag_bind("continue_hint", "<Button-1>", self._on_continue_click)
        text.tag_bind("continue_hint", "<Enter>", lambda e: text.configure(cursor="hand2"))
        text.tag_bind("continue_hint", "<Leave>", lambda e: text.configure(cursor=""))
        self._fold_ranges[text] = []
        self._fold_nums[text] = []
        session = {
            "tab": col,
            "col": col,
            "scrollbar": scrollbar,
            "text": text,
            "name": None,
            "id": None,
            "ephemeral": False,
            "stars": [],
            "tags": [],
            "pinned": [],
            "messages": [{"role": "system", "content": self.cfg["system_prompt"]}],
            "usage_total": {"prompt": 0, "completion": 0, "cache_hit": 0, "cache_miss": 0},
            "last_usage": None,
            "assistant_answered": False,
            "session_start": datetime.now(),
            "blocks": CappedList(),
            "first_user": None,
            "last_code_blocks": [],
            "top": False,
        }
        self._sessions.append(session)
        self._current = session
        self._refresh_session_list()
        return session

    def _session_display_name(self, session):
        prefix = "📌 " if session.get("top") else ""
        name = self._session_base_name(session)
        tags = session.get("tags") or []
        if tags:
            return f"{prefix}[{','.join(tags[:2])}] {name}"
        return prefix + name

    def _session_base_name(self, session):
        """基础名（不含置顶/标签前缀）缓存：避免无名会话时 index() 的 O(n) 查找
        在列表全量重建中反复执行（会话多时整体 O(n²)）。

        仅缓存已命名会话——未命名会话的「会话 N」随列表增删变化，缓存会失效。"""
        name = session.get("name")
        if not name:
            try:
                idx = self._sessions.index(session)
            except ValueError:
                idx = 0
            return f"会话 {idx + 1}"
        cache = self._session_name_cache
        key = id(session)
        if key not in cache:
            cache[key] = name
        return cache[key]

    def _invalidate_session_name(self, session):
        self._session_name_cache.pop(id(session), None)

    def _session_search_hits(self, session, query):
        """检查会话是否匹配搜索词（#tag / 名称 / 首条用户消息）。"""
        q = query.lower()
        if q.startswith("#"):
            tag = q[1:].strip()
            if not tag:
                return False  # 纯 "#" 不应命中所有带标签会话
            return any(tag in (t or "").lower() for t in (session.get("tags") or []))
        if self._session_display_name(session).lower().find(q) >= 0:
            return True
        first = session.get("first_user")
        return bool(first) and q in first.lower()

    def _schedule_session_list_refresh(self):
        if getattr(self, "_list_refresh_after", None) is not None:
            try:
                self.root.after_cancel(self._list_refresh_after)
            except Exception:
                pass
        self._list_refresh_after = self.root.after(200, self._list_refresh_debounced)

    def _list_refresh_debounced(self):
        self._list_refresh_after = None
        try:
            self._refresh_session_list()
        except Exception:
            pass

    def _refresh_session_list(self):
        self._updating_list = True
        try:
            query = self.session_search_var.get().strip().lower()
            visible = [
                s for s in self._sessions
                if not query or self._session_search_hits(s, query)
            ]
            visible.sort(key=lambda s: not bool(s.get("top")))
            # 显示名与排序均未变化时跳过重建（避免每键全量 delete+insert+selection）
            names = [self._session_display_name(s) for s in visible]
            if names == getattr(self, "_list_last_names", None):
                self._list_visible = visible
                return
            self._list_last_names = names
            self._list_visible = visible
            # 记录滚动位置，重建后恢复（浏览列表时不被强制跳回）
            try:
                frac = self.session_list.yview()[0]
            except tk.TclError:
                frac = 0.0
            had_items = self.session_list.size() > 0
            self.session_list.delete(0, "end")
            for session in visible:
                self.session_list.insert("end", self._session_display_name(session))
            if self._current in visible:
                cur = visible.index(self._current)
                self.session_list.selection_set(cur)
                self.session_list.activate(cur)
            if query:
                self.session_count_label.configure(
                    text=f"匹配 {len(visible)}/{len(self._sessions)} 个会话"
                    if visible else "无匹配会话"
                )
            else:
                self.session_count_label.configure(text="")
            if had_items:
                self.session_list.yview_moveto(frac)
            elif visible:
                self.session_list.see(visible.index(self._current) if self._current in visible else 0)
        except Exception:
            pass
        finally:
            self._updating_list = False

    def _show_session_text(self, session):
        for s in self._sessions:
            if s is session:
                s["col"].place(relx=0.5, rely=0, anchor="n", width=760, height=600)
                self._layout_all(None)
                self._follow_bottom = True  # 切换会话后贴底浏览
            else:
                s["col"].place_forget()

    def _maybe_auto_name(self, text):
        session = self._current
        if not session.get("first_user"):
            session["first_user"] = text
        if session.get("name") or not text:
            return
        name = " ".join(text.split())[:18]
        if name:
            session["name"] = name
            self._invalidate_session_name(session)
            self._refresh_session_list()

    def _on_tab_changed(self, _event=None):
        try:
            self._on_tab_changed_impl(_event)
        except Exception:
            logging.exception("切换会话失败")

    def _on_tab_changed_impl(self, _event=None):
        if self._updating_list or not self._sessions or not self._list_visible:
            return
        sel = self.session_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._list_visible):
            return
        if self.busy:
            self.session_list.selection_clear(0, "end")
            if self._current in self._list_visible:
                cur = self._list_visible.index(self._current)
                self.session_list.selection_set(cur)
                self.session_list.activate(cur)
            messagebox.showinfo("提示", "生成中不能切换会话，请先停止。")
            return
        old = self._current
        self._current = self._list_visible[idx]
        if old is not self._current:
            self._show_session_text(self._current)
            self._ctx_counts = None  # 计数是会话维度的，切换后失效（下次 worker 重算）
            self._snapshot_dirty = True
            # 切走前把旧会话完整落盘：否则其他会话只存在于内存，
            # 退出/崩溃后 100% 丢失（此前仅 close_tab 才写盘）
            if not old.get("ephemeral") and not self.cfg.get("privacy_mode"):
                self.save_session_to_file(old)
            self._maybe_save_snapshot()  # 新 current 的快照也尽快落盘
        self.update_status()
        self._update_context_bar()
        if self._search_open:
            self._do_search()

    def toggle_search(self, _event=None):
        if self._search_open:
            self._search_open = False
            self.search_frame.pack_forget()
            self._clear_search_tags()
            self.search_var.set("")
            self.chat_text.focus_set()
        else:
            self._search_open = True
            self.search_frame.pack(side="bottom", fill="x")
            self.search_entry.focus_set()
            self.search_entry.select_range(0, "end")
            self._do_search()

    def _clear_search_tags(self):
        for session in self._sessions:
            session["text"].tag_remove("search_hit", "1.0", "end")
            session["text"].tag_remove("search_cur", "1.0", "end")
        self._search_matches = []
        self._search_index = 0

    def _schedule_search(self):
        if self._search_after_id is not None:
            try:
                self.root.after_cancel(self._search_after_id)
            except Exception:
                pass
        self._search_after_id = self.root.after(SEARCH_DEBOUNCE_MS, self._do_search_debounced)

    def _do_search_debounced(self):
        self._search_after_id = None
        if self._search_open:
            self._do_search()

    def _do_search(self):
        query = self.search_var.get()
        if self._paged_render is not None:
            # 分帧渲染中文本不完整：同步补全是秒级冻结，改为挂起搜索等分帧完成
            self._search_when_ready = True
            self._flash_status("会话渲染中，搜索稍后执行…", 1500)
            return
        text = self.chat_text
        self._set_all_folds_elide(text, False)
        try:
            self._clear_search_tags()
            if not query:
                self.search_count.config(text="0/0")
                return
            text.tag_raise("search_cur", "search_hit")
            # 一次 Tcl 调用返回全部命中（-all 批量），替代逐匹配循环（2000 次 round-trip）
            matches = []
            try:
                raw = text.tk.call(
                    text._w, "search", "-all", "-nocase", "--", query, "1.0", "end"
                )
                matches = text.tk.splitlist(raw)
            except tk.TclError:
                matches = []
            if len(matches) > MAX_SEARCH_MATCHES:
                matches = matches[:MAX_SEARCH_MATCHES]
                self._flash_status(
                    f"匹配过多，仅显示前 {MAX_SEARCH_MATCHES} 个结果"
                )
            self._search_matches = list(matches)
            if matches:
                pairs = []
                for pos in matches:
                    pairs.append(pos)
                    pairs.append(f"{pos}+{len(query)}c")
                try:
                    text.tag_add("search_hit", *pairs)  # 一次调用批量打标
                except tk.TclError:
                    pass
            self._search_index = 0
            self._mark_current()
        finally:
            self._restore_fold_elides(text)

    def _mark_current(self):
        text = self.chat_text
        text.tag_remove("search_cur", "1.0", "end")
        if self._search_matches:
            pos = self._search_matches[self._search_index]
            end = f"{pos}+{len(self.search_var.get())}c"
            text.tag_add("search_cur", pos, end)
            text.see(pos)
            self._follow_bottom = False  # 搜索定位 = 手动浏览，停止自动跟随
        self.search_count.config(
            text=f"{self._search_index + 1}/{len(self._search_matches)}" if self._search_matches else "0/0"
        )

    def _search_next(self):
        if not self._search_matches:
            return
        self._search_index = (self._search_index + 1) % len(self._search_matches)
        self._mark_current()

    def _search_prev(self):
        if not self._search_matches:
            return
        self._search_index = (self._search_index - 1) % len(self._search_matches)
        self._mark_current()

    def add_tab(self, ephemeral=False):
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        session = self._add_session()
        session["ephemeral"] = bool(ephemeral)
        if ephemeral:
            session["name"] = "临时会话"
            self._invalidate_session_name(session)
            self._refresh_session_list()  # _add_session 已按默认名渲染，覆盖名称后补刷新
        self._show_session_text(session)
        self.new_conversation(export_old=False)
        self._on_tab_changed()

    def close_tab(self):
        if len(self._sessions) <= 1:
            messagebox.showinfo("提示", "至少保留一个会话。")
            return
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        idx = self._sessions.index(self._current)
        session = self._current
        self._cancel_paged_render()
        if (
            not session.get("ephemeral")
            and not self.cfg.get("privacy_mode")
        ):
            self.save_session_to_file(session)
        session["text"].destroy()
        session["scrollbar"].destroy()
        session["col"].destroy()
        self._fold_ranges.pop(session["text"], None)
        self._link_ranges.pop(session["text"], None)
        self._filelink_ranges.pop(session["text"], None)
        self._restyle[:] = [
            (w, k)
            for w, k in self._restyle
            if w is not session["col"] and w is not session["scrollbar"]
        ]
        self._sessions.pop(idx)
        new_idx = min(idx, len(self._sessions) - 1)
        self._current = self._sessions[new_idx]
        self._session_name_cache.pop(id(session), None)  # 关闭会话清显示名缓存
        self._show_session_text(self._current)
        self._refresh_session_list()
        self._on_tab_changed()

    def _on_tab_double_click(self, event):
        try:
            idx = self.session_list.nearest(event.y)
        except Exception:
            return
        if 0 <= idx < len(self._list_visible):
            # 过滤态下 idx 是 _list_visible 的索引，须映射回全量 _sessions 再重命名
            target = self._list_visible[idx]
            try:
                self.rename_tab(self._sessions.index(target))
            except ValueError:
                pass

    def _on_session_menu(self, event):
        t = self._theme()
        try:
            idx = self.session_list.nearest(event.y)
        except tk.TclError:
            return
        if 0 <= idx < len(self._list_visible):
            self.session_list.selection_clear(0, "end")
            self.session_list.selection_set(idx)
            self.session_list.activate(idx)
            self._on_tab_changed()
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=t["panel"],
            fg=t["text"],
            activebackground=t.get("hover", t["surface"]),
            activeforeground=t["text"],
            bd=1,
            relief="flat",
        )
        menu.add_command(label="重命名会话", command=self.rename_tab)
        menu.add_command(label="设置标签…", command=self.edit_session_tags)
        top_label = "取消置顶" if self._current.get("top") else "置顶会话"
        menu.add_command(label=top_label, command=self._toggle_session_top)
        menu.add_command(label="新建会话", command=self.add_tab)
        menu.add_command(label="删除会话", command=self.close_tab)
        menu.add_separator()
        menu.add_command(label="导出历史", command=self.export_history)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass
            # 延迟回收：立即 destroy 会让菜单一闪即逝（点击无反应）
            self.root.after(3000, lambda: _destroy_menu(menu))

    def rename_tab(self, idx=None):
        if idx is None:
            idx = self._sessions.index(self._current)
        if not (0 <= idx < len(self._sessions)):
            return
        session = self._sessions[idx]
        current = self._session_display_name(session)
        name = simpledialog.askstring(
            "重命名会话", "输入会话名称:", parent=self.root, initialvalue=current
        )
        if name is not None:
            name = name.strip()
            session["name"] = name or None
            self._invalidate_session_name(session)
            self._refresh_session_list()

    def _toggle_session_top(self):
        session = self._current
        session["top"] = not bool(session.get("top"))
        self._invalidate_session_name(session)  # 置顶前缀影响显示名缓存
        self._refresh_session_list()
        self._flash_status("已置顶会话" if session["top"] else "已取消置顶")

    def on_font_size_change(self, _event=None):
        try:
            size = int(self.font_size_combo.get())
        except ValueError:
            return
        self.cfg["font_size"] = max(8, min(18, size))
        self.apply_font_size()
        save_config(self.cfg)

    def setup_widgets_from_config(self):
        self.model_combo.set(self.cfg["model"])
        self.key_var.set(self.cfg["api_key"])
        self.scenario_combo.set(self.cfg["scenario"])
        thinking = self.cfg["thinking"]
        if thinking not in THINKING_MODES:
            thinking = "high"
        self.thinking_combo.set(THINKING_MODES[thinking])
        self.max_tokens_spin.set(str(self.cfg["max_tokens"]))
        self.seed_var.set(str(self.cfg.get("seed", "")))
        self.tools_var.set(bool(self.cfg.get("tools_enabled", True)))
        self.font_size_combo.set(str(self.cfg.get("font_size", 10)))
        self.md_var.set(bool(self.cfg.get("md_render", True)))
        self.json_var.set(bool(self.cfg.get("json_output", False)))
        self.beta_var.set(bool(self.cfg.get("beta_api", False)))
        self.browser_visible_var.set(not bool(self.cfg.get("browser_headless", True)))
        _dc.BROWSER_HEADLESS = bool(self.cfg.get("browser_headless", True))
        self._sync_mode_var()
        try:
            self.custom_temp_var.set(f"{float(self.cfg.get('custom_temperature', 1.0)):.2f}")
            self.custom_topp_var.set(f"{float(self.cfg.get('custom_top_p', 1.0)):.2f}")
        except Exception:
            pass
        try:
            self._restore_input_height()
        except Exception:
            pass
        self.on_scenario_change()
        self._update_role_label()

    def save_widgets_to_config(self):
        new = {}
        try:
            new["max_tokens"] = int(self.max_tokens_spin.get())
        except ValueError:
            new["max_tokens"] = 16384
        new["api_key"] = self.key_var.get().strip()
        model = self.model_combo.get().strip()
        new["model"] = model or "deepseek-v4-flash"  # 支持自定义模型名（OpenAI 兼容端点）
        new["scenario"] = self.scenario_combo.get()
        for key, label in THINKING_MODES.items():
            if label == self.thinking_combo.get():
                new["thinking"] = key
                break
        new["seed"] = self.seed_var.get().strip()
        new["tools_enabled"] = self.tools_var.get()
        new["json_output"] = self.json_var.get()
        new["beta_api"] = self.beta_var.get()
        mode = self.mode_var.get()
        new["full_auto"] = mode == "full_auto"
        new["pure_chat"] = mode == "pure_chat"
        try:
            new["custom_temperature"] = max(0.0, min(2.0, float(self.custom_temp_var.get())))
        except (TypeError, ValueError):
            pass
        try:
            new["custom_top_p"] = max(0.0, min(1.0, float(self.custom_topp_var.get())))
        except (TypeError, ValueError):
            pass
        try:
            new["font_size"] = max(8, min(18, int(self.font_size_combo.get())))
        except ValueError:
            pass
        changed = False
        for k, v in new.items():
            if self.cfg.get(k) != v:
                self.cfg[k] = v
                changed = True
        if changed:
            save_config(self.cfg)
        return self.cfg

    def on_scenario_change(self, _event=None):
        scenario = self.scenario_combo.get()
        if scenario in SCENARIO_DEFAULT_THINKING:
            self.thinking_combo.set(THINKING_MODES[SCENARIO_DEFAULT_THINKING[scenario]])
        if scenario == "自定义":
            # custom_row 的父容器是折叠的 adv_frame，须先展开高级参数区温度行才可见
            if not self.adv_expanded.get():
                self.adv_expanded.set(True)
                self.adv_frame.pack(fill="x", pady=(2, 0))
            self.custom_row.pack(fill="x", pady=(6, 0))
        else:
            self.custom_row.pack_forget()

    def _on_thinking_changed(self, _event=None):
        label = self.thinking_combo.get()
        if label != "最大思考 (max)":
            return
        try:
            if self._ctx_counts is not None:
                used = tokens.BASE_OVERHEAD + sum(self._ctx_counts)
            else:
                # 无缓存时用字符估算兜底：避免主线程全量 tiktoken 冻结 UI（长会话可卡 1-2s）
                used = int(self._estimate_chars() / 1.5)
            pct = used / MAX_CONTEXT_TOKENS
            if pct > 0.6:
                self._flash_status(
                    f"上下文已占 {pct:.0%}，思考强度 max 建议保留输出余量"
                    "（V4 最大输出 384K）",
                    5000,
                )
        except Exception:
            pass

    # ===== 全新工具分类（v2 能力层）：14 组 =====
    TOOL_GROUPS = [
        ("基础系统", ("get_date", "ask_user", "request_permission", "environment_info")),
        ("信息检索", ("get_weather", "search_web", "fetch_url", "search_local", "knowledge_index", "knowledge_search", "rss_fetch")),
        ("长期记忆", ("read_memory", "write_memory", "query_memory_graph")),
        ("文件管理", (
            "read_file", "write_file", "edit_file", "list_dir", "delete_file",
            "batch_rename", "archive_files", "extract_archive", "verify_files",
            "webdav",
        )),
        ("代码与终端", ("run_python", "run_command", "run_tests", "verify_output", "pip_install")),
        ("数据处理", (
            "database_query", "database_query_mysql", "database_query_postgres",
            "database_execute", "read_csv", "write_csv", "read_excel", "write_excel", "chart_data",
            "kv_store",
        )),
        ("文档创作", ("create_doc", "write_code_project", "publish_draft",
                      "pdf_extract", "pdf_create", "docx_read", "pptx_read",
                      "run_wechat_writer")),
        ("媒体感知", (
            "image_process", "image_understand", "ocr_image", "screen_capture",
            "tts_save", "speech_to_text", "image_generate", "qrcode", "media_ffmpeg",
        )),
        ("浏览器", ("browser_navigate", "web_screenshot")),
        ("通信通知", ("send_email", "read_email", "send_webhook", "notify_desktop", "clipboard_get", "clipboard_set")),
        ("任务调度", ("schedule_task", "list_schedules", "cancel_schedule")),
        ("进程管理", ("start_process", "stop_process", "list_processes")),
        ("协作与进化", ("subagent_run", "project_info", "read_project_file", "create_evolution", "run_workflow")),
        ("洞察与断点", ("usage_report", "task_checkpoint_save", "task_checkpoint_load")),
    ]

    def _tool_group_of(self, name):
        for gname, members in self.TOOL_GROUPS:
            if name in members:
                return gname
        return "其他"

    def edit_tools(self):
        """工具设置（工具中心面板版）。"""
        dialog, body, footer = self._dialog_shell(
            "工具设置", 460, 620,
            subtitle="选择 AI 可自动调用的工具（未选中的不会出现在请求中）",
            minsize=(380, 420),
        )
        save = self._edit_tools_panel(body)
        self._footer_hint(footer, f"共 {len(TOOLS) + len(load_user_tools())} 个工具 · 滚轮或拖动右侧滚动条")
        self._footer_btn(footer, "取消", dialog.destroy)
        self._footer_btn(footer, "保存", lambda: (save(), dialog.destroy()), primary=True)

    def _edit_tools_panel(self, body):
        """工具设置面板（嵌入工具中心）：返回保存回调。"""
        t = self._theme()
        master = tk.BooleanVar(value=self.tools_var.get())
        vars = {}
        enabled = set(self.cfg.get("enabled_tools", []))
        builtin = list(TOOLS)
        customs = list(load_user_tools())
        all_tools = builtin + customs
        for tool in all_tools:
            name = tool["function"]["name"]
            vars[name] = tk.BooleanVar(value=name in enabled)

        head = tk.Frame(body, bg=t["panel"])
        head.pack(fill="x", pady=(0, 6))
        self._restyle.append((head, "panel"))
        if self.cfg.get("full_auto"):
            self._lbl(body, "🤖 完全智能模式生效中：全部工具自动可用，以下勾选仅作为切回标准模式后的默认集。",
                      role="label_accent", bg="panel", font=(FONT_FAMILY, 8, "bold")).pack(anchor="w", pady=(0, 4))
        elif self.cfg.get("pure_chat"):
            self._lbl(body, "💬 纯对话模式生效中：不调用任何工具，以下勾选仅作为切回标准模式后的默认集。",
                      role="label_sec", bg="panel", font=(FONT_FAMILY, 8, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Checkbutton(
            head, text="启用工具（Agent 自动调用）", variable=master,
            command=lambda: [v.set(master.get()) for v in vars.values()],
        ).pack(side="left")
        self._lbl(head, "搜索:", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(
            side="left", padx=(14, 4)
        )
        search_var = tk.StringVar()
        search_entry = ttk.Entry(head, textvariable=search_var, width=20)
        search_entry.pack(side="left")

        canvas, inner = self._scroll_panel(body)

        def render():
            for w in inner.winfo_children():
                w.destroy()
            query = search_var.get().strip().lower()
            shown = 0
            for tool in all_tools:
                name = tool["function"]["name"]
                desc = tool["function"]["description"]
                if query and query not in name.lower() and query not in desc.lower():
                    continue
                is_custom = bool(tool["function"].get("endpoint"))
                gname = "自定义工具" if is_custom else self._tool_group_of(name)
                if not hasattr(render, "_last_group") or render._last_group != gname:
                    if shown > 0:
                        ttk.Separator(inner).pack(fill="x", pady=(6, 2))
                    gl = tk.Label(
                        inner,
                        text=gname,
                        bg=t["panel"],
                        fg=t["text_sec"],
                        font=(FONT_FAMILY, 9, "bold"),
                        anchor="w",
                    )
                    gl.pack(fill="x", padx=4, pady=(4, 0))
                    render._last_group = gname
                row = tk.Frame(inner, bg=t["panel"])
                row.pack(fill="x", padx=4, pady=2)
                cb = ttk.Checkbutton(
                    row, text=name, variable=vars[name]
                )
                cb.pack(anchor="w")
                dl = tk.Label(
                    row,
                    text=desc,
                    font=(FONT_FAMILY, 9),
                    bg=t["panel"],
                    fg=t["text_sec"],
                    wraplength=380,
                    justify="left",
                    anchor="w",
                )
                dl.pack(anchor="w", padx=(24, 4))
                shown += 1
            if shown == 0:
                tk.Label(
                    inner, text="（无匹配工具）", bg=t["panel"], fg=t["text_sec"]
                ).pack(pady=10)
            render._last_group = None

        render._last_group = None
        search_var.trace_add("write", lambda *a: render())
        render()

        def save():
            self.tools_var.set(master.get())
            chosen = [n for n, v in vars.items() if v.get()] if master.get() else []
            self.cfg["enabled_tools"] = chosen
            save_config(self.cfg)
            self._flash_status("工具配置已保存")

        return save

    def manage_user_tools(self):
        t = self._theme()
        items = load_user_tools(USER_TOOLS_PATH)
        dialog, body, footer = self._dialog_shell(
            "自定义工具（Agent SDK）", 620, 540,
            subtitle="注册自己的 HTTP 工具，Agent 在任务中自动调用；保存后可在「工具设置」中启停",
        )

        left = tk.Frame(body, bg=t["panel"])
        left.pack(side="left", fill="y", padx=(4, 8))
        self._restyle.append((left, "panel"))
        listbox = tk.Listbox(
            left,
            width=24,
            bg=t["input_bg"],
            fg=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
            exportselection=False,
        )
        listbox.pack(fill="both", expand=True)
        for it in items:
            listbox.insert("end", it["function"]["name"])

        right = tk.Frame(body, bg=t["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(8, 4))
        self._restyle.append((right, "panel"))

        fields = {}
        rows = [
            ("name", "工具名称（英文标识，Agent 调用时使用）"),
            ("description", "描述（Agent 判断何时调用）"),
            ("endpoint", "HTTP 地址（http://… 或 https://…）"),
            ("method", "请求方法（POST / GET）"),
            ("params", "参数列表，逗号分隔，如：sql, limit"),
        ]
        for key, label in rows:
            self._lbl(right, label, role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w")
            var = tk.StringVar()
            ttk.Entry(right, textvariable=var).pack(fill="x", pady=(2, 6))
            fields[key] = var

        def to_schema():
            name = fields["name"].get().strip()
            endpoint = fields["endpoint"].get().strip()
            if not (name and endpoint):
                messagebox.showinfo("提示", "工具名称与 HTTP 地址必填。")
                return None
            param_names = [p.strip() for p in fields["params"].get().split(",") if p.strip()]
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": fields["description"].get().strip() or "自定义工具",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            p: {"type": "string", "description": p} for p in param_names
                        },
                        "required": param_names,
                    },
                    "endpoint": endpoint,
                    "method": (fields["method"].get().strip() or "POST").upper(),
                },
            }

        def select_item(_e=None):
            sel = listbox.curselection()
            if not sel:
                return
            fn = items[sel[0]]["function"]
            fields["name"].set(fn.get("name", ""))
            fields["description"].set(fn.get("description", ""))
            fields["endpoint"].set(fn.get("endpoint", ""))
            fields["method"].set(fn.get("method", "POST"))
            params = list((fn.get("parameters") or {}).get("properties", {}).keys())
            fields["params"].set(", ".join(params))

        def save_item():
            schema = to_schema()
            if schema is None:
                return
            sel = listbox.curselection()
            if sel:
                items[sel[0]] = schema
            else:
                items.append(schema)
            self._write_user_tools(items)
            listbox.delete(0, "end")
            for it in items:
                listbox.insert("end", it["function"]["name"])
            self._flash_status("自定义工具已保存")

        def delete_item():
            sel = listbox.curselection()
            if not sel:
                return
            items.pop(sel[0])
            self._write_user_tools(items)
            listbox.delete(0, "end")
            for it in items:
                listbox.insert("end", it["function"]["name"])

        def new_item():
            listbox.selection_clear(0, "end")
            for var in fields.values():
                var.set("")

        listbox.bind("<<ListboxSelect>>", select_item)
        hint = self._lbl(
            right,
            "模型传参后，工具向 endpoint 发送 HTTP 请求（POST JSON / GET query）并返回文本结果。",
            role="label_sec",
            bg="panel",
            font=(FONT_FAMILY, 9),
            wraplength=300,
        )
        hint.pack(anchor="w", pady=(0, 6))
        self._footer_hint(footer, "左侧选择已有工具，右侧编辑字段后点「保存」")
        self._footer_btn(footer, "删除", delete_item)
        self._footer_btn(footer, "保存", save_item, primary=True)
        self._footer_btn(footer, "新增", new_item)
        self._footer_btn(footer, "关闭", dialog.destroy)

    def _apply_profile(self, name, profile):
        """切换到指定 Profile：同步 cfg 与设置面板控件，触发客户端重建。"""
        self.cfg["api_key"] = profile.get("api_key", "")
        self.cfg["base_url"] = profile.get("base_url") or DEFAULT_BASE_URL
        self.cfg["model"] = profile.get("model") or "deepseek-v4-flash"
        self.cfg["current_profile"] = name
        self.key_var.set(self.cfg["api_key"])
        self.model_combo.set(self.cfg["model"])
        save_config(self.cfg)
        self.ensure_client()
        self._flash_status(f"已切换到 Profile「{name}」")

    def _record_recent_output(self, text):
        """从工具结果文本提取存在的路径，记录到最近产物列表（前 20 条）。

        进程内缓存 + 标记脏位：只改内存，由 _flush_recent 统一落盘，
        避免每个工具结果在主线程做 2 读 1 写文件 IO（Agent 一轮几十次）。
        """
        try:
            recent = self._recent_cache
            for mm in PATH_RE.finditer(text or ""):
                p = mm.group(0).rstrip("。.,;: \t")
                if not os.path.exists(p):
                    continue
                if p in recent:
                    if recent[0] == p:
                        continue
                    recent.remove(p)
                recent.insert(0, p)
            if len(recent) > 20:
                del recent[20:]
            self._recent_dirty = True
            self._update_recent_bar()
        except Exception:
            pass

    # ---- 产物条：输入框上方常驻显示最近产物（打开/所在文件夹/复制路径）----
    def _update_recent_bar(self):
        """产物条显示最近产物路径（存在性校验；隐藏态不干扰布局）。"""
        try:
            if not getattr(self, "recent_bar", None):
                return
            for p in self._recent_cache:
                if os.path.exists(p):
                    self.recent_bar_lbl.configure(text=f"📦 最近产物：{os.path.basename(p)}")
                    self.recent_bar.pack(side="top", before=self.input_text, fill="x", padx=14, pady=(8, 0))
                    return
            self._hide_recent_bar()
        except tk.TclError:
            pass
        except Exception:
            pass

    def _hide_recent_bar(self):
        try:
            if getattr(self, "recent_bar", None):
                self.recent_bar.pack_forget()
        except tk.TclError:
            pass

    def _recent_bar_path(self):
        for p in self._recent_cache:
            if os.path.exists(p):
                return p
        return None

    def _open_recent_bar(self):
        p = self._recent_bar_path()
        if p:
            self._open_path(p)
        else:
            self._flash_status("没有可打开的产物")

    def _open_recent_bar_dir(self):
        p = self._recent_bar_path()
        if p:
            try:
                os.startfile(os.path.dirname(p))
            except Exception:
                self._flash_status(f"无法打开目录：{os.path.dirname(p)}")
        else:
            self._flash_status("没有可打开的产物")

    def _copy_recent_bar(self):
        p = self._recent_bar_path()
        if p:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(p)
                self._flash_status(f"已复制路径：{p}")
            except tk.TclError:
                pass
        else:
            self._flash_status("没有可复制的产物")

    def _flush_recent(self):
        """把最近产物缓存一次性写盘（_finish/退出时调用）。"""
        if not getattr(self, "_recent_dirty", False):
            return
        try:
            self._save_recent(self._recent_cache)
            self._recent_dirty = False
        except Exception:
            pass

    @staticmethod
    def _load_recent():
        try:
            if os.path.exists(RECENT_PATH):
                with open(RECENT_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [str(x) for x in data if str(x).strip()]
        except Exception:
            logging.exception("读取最近产物失败")
        return []

    @staticmethod
    def _atomic_json_write(path, data, indent=1, compact=False):
        """原子写 JSON（唯一临时文件 + os.replace），失败返回 False。

        compact=True 使用紧凑分隔符（快照/会话等大文件体积减半，读写更快）。
        唯一临时文件（mkstemp）防止并发写同一路径互相截断。
        """
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            import tempfile

            fd, tmp = tempfile.mkstemp(
                dir=os.path.dirname(path) or ".",
                prefix=os.path.basename(path) + ".",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    if compact:
                        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                    else:
                        json.dump(data, f, ensure_ascii=False, indent=indent)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            os.replace(tmp, path)
            return True
        except Exception:
            logging.exception("原子写 JSON 失败: %s", path)
            return False

    @staticmethod
    def _save_recent(recent):
        return AssistantApp._atomic_json_write(RECENT_PATH, recent)

    def _process_output(self, name, line):
        """进程输出回调（reader 线程）→ 队列转主线程。"""
        try:
            self._ui_queue.put(("proc_out", (name, line)))
        except Exception:
            pass

    def _ensure_proc_panel(self):
        if self.proc_panel is None:
            try:
                self.proc_panel = ProcessPanel(
                    self.root, on_stop=self._stop_named_process, theme=self._theme()
                )
            except Exception:
                self.proc_panel = None
        return self.proc_panel

    def _stop_named_process(self, name):
        try:
            from deepseek_client import stop_process

            result = stop_process(name)
            self._flash_status(str(result)[:60])
        except Exception:
            pass

    def _proc_panel_append(self, name, line):
        panel = self._ensure_proc_panel()
        if panel is None:
            return
        try:
            if "── 进程启动" in line:
                panel.process_started(name)
            elif "── 进程已退出" in line:
                import re as _re

                mm = _re.search(r"code=(\d+)", line)
                panel.process_exited(name, mm.group(1) if mm else "?")
            else:
                panel.append_line(name, line)
        except Exception:
            pass

    def show_process_terminal(self):
        panel = self._ensure_proc_panel()
        if panel is not None:
            panel.open()
            panel.reload_processes()

    def _restore_bak(self, path):
        """用 .bak 备份还原文件（AI 改错时一键回滚）。"""
        bak = path + ".bak"
        if not os.path.exists(bak):
            self._flash_status("该文件没有可用备份（.bak）")
            return
        if messagebox.askyesno(
            "还原备份",
            f"将用 {os.path.basename(bak)} 覆盖 {os.path.basename(path)}，确定？",
        ):
            try:
                shutil.copy2(bak, path)
                self._flash_status("已从 .bak 还原")
            except Exception as e:
                messagebox.showerror("还原失败", str(e))

    def show_recent_outputs(self):
        t = self._theme()
        # 用进程内缓存（_record_recent_output 只改内存，_finish 才 flush）：
        # 读磁盘会让刚完成的工具产物看不到
        recent = list(self._recent_cache)
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title(f"最近产物（{len(recent)} 条，AI 创建/修改的文件）")
        dialog.geometry("640x420")
        dialog.transient(self.root)
        listbox = tk.Listbox(
            dialog, width=80, bg=t["input_bg"], fg=t["input_fg"],
            selectbackground=t["selection"], selectforeground=t["accent_text"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], exportselection=False,
        )
        listbox.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        for p in recent:
            listbox.insert("end", p)
        if not recent:
            listbox.insert("end", "（暂无产物，AI 执行写文件类工具后自动记录）")

        def open_selected():
            sel = listbox.curselection()
            if not sel or not recent:
                return
            self._open_path(recent[sel[0]])

        def open_folder():
            sel = listbox.curselection()
            if not sel or not recent:
                return
            p = recent[sel[0]]
            target = os.path.dirname(p) if os.path.isfile(p) else p
            try:
                os.startfile(target)
            except Exception:
                self._flash_status(f"无法打开目录：{target}")

        def copy_path():
            sel = listbox.curselection()
            if not sel or not recent:
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(recent[sel[0]])
            self._flash_status("已复制路径")

        def remove_selected():
            sel = listbox.curselection()
            if not sel or not recent:
                return
            recent.pop(sel[0])
            # 同步内存缓存（对话框基于缓存渲染，直接写盘会与缓存不一致）
            self._recent_cache = recent
            self._save_recent(recent)
            self._recent_dirty = False
            listbox.delete(sel[0])

        def restore_selected():
            sel = listbox.curselection()
            if not sel or not recent:
                return
            self._restore_bak(recent[sel[0]])

        listbox.bind("<Double-1>", lambda e: open_selected())
        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=12, pady=(8, 12))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "打开", open_selected, kind="primary", fsz=9).pack(side="left")
        self._mk_button(bar, "打开所在文件夹", open_folder, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "还原 .bak", restore_selected, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "复制路径", copy_path, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "从列表移除", remove_selected, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

    def choose_working_dir(self):
        """工作目录选择：指定 AI 执行任务的"家"（自动加入权限允许目录）。"""
        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("工作目录（AI 执行任务的默认位置）")
        dialog.geometry("560x420")
        dialog.transient(self.root)
        body = tk.Frame(dialog, bg=t["panel"])
        body.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        self._restyle.append((body, "panel"))
        self._lbl(
            body, f"当前：{self._get_active_dir()}",
            role="label_accent", bg="panel", font=(FONT_FAMILY, 9, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        self._lbl(
            body, "AI 的所有文件操作/项目创建默认在此目录下进行，新任务会自动创建独立子目录。",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(0, 10))

        row = tk.Frame(body, bg=t["panel"])
        row.pack(fill="x", pady=(0, 6))
        self._restyle.append((row, "panel"))
        dir_var = tk.StringVar(value=self._get_active_dir())
        ttk.Entry(row, textvariable=dir_var).pack(side="left", fill="x", expand=True)
        self._mk_button(
            row, "浏览…",
            lambda: dir_var.set(filedialog.askdirectory(initialdir=dir_var.get() or WORKSPACE_DIR) or dir_var.get()),
            fsz=9,
        ).pack(side="left", padx=(6, 0))

        self._lbl(
            body, "常用目录：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(4, 2))
        common = [WORKSPACE_DIR]
        d = permissions.get_data()
        for p in d["filesystem"].get("allowed_dirs", []):
            if os.path.isdir(p) and p not in common:
                common.append(p)
        listbox = tk.Listbox(
            body, height=6, bg=t["input_bg"], fg=t["input_fg"],
            selectbackground=t["selection"], selectforeground=t["accent_text"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], exportselection=False,
        )
        listbox.pack(fill="both", expand=True)
        for p in common:
            listbox.insert("end", p)

        def pick_common(_e=None):
            sel = listbox.curselection()
            if sel:
                dir_var.set(common[sel[0]])

        listbox.bind("<<ListboxSelect>>", pick_common)

        def mk_subdir():
            name = simpledialog.askstring(
                "新建子目录", "输入子目录名（在当前目录下创建）:", parent=dialog
            )
            if not name:
                return
            name = re.sub(r'[\\/:*?"<>|]', "_", name.strip())[:60]
            if not name:
                return
            target = os.path.join(dir_var.get() or WORKSPACE_DIR, name)
            try:
                os.makedirs(target, exist_ok=True)
                dir_var.set(target)
                self._flash_status(f"已创建子目录：{target}")
            except Exception as e:
                messagebox.showerror("创建失败", str(e))

        def apply():
            ok, info = self._set_active_dir(dir_var.get())
            if not ok:
                messagebox.showerror("设置失败", info)
                return
            self._flash_status(f"工作目录已切换：{info}")
            dialog.destroy()

        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=16, pady=(12, 14))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "新建子目录", mk_subdir, fsz=9).pack(side="left")
        self._mk_button(bar, "设为工作目录", apply, kind="primary", fsz=9).pack(side="right")
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right", padx=(0, 8))

    def show_workspace_tree(self):
        """工作区文件树：浏览 AI 产物，双击文件注入输入框，右键复制路径。"""
        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("工作区文件树")
        dialog.geometry("560x480")
        dialog.transient(self.root)
        tree = ttk.Treeview(dialog, columns=("size",), show="tree headings")
        tree.heading("size", text="大小")
        tree.column("size", width=90, anchor="e")
        tree.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        paths = {}

        def add_item(parent, text, full, values=()):
            iid = tree.insert(parent, "end", text=text, values=values)
            paths[iid] = full
            return iid

        populated = set()

        def populate(parent, path):
            if path in populated:
                return
            populated.add(path)
            try:
                entries = sorted(os.listdir(path))
            except Exception:
                return
            # 清除占位符后重建该目录子项
            for cid in tree.get_children(parent):
                tree.delete(cid)
            for e in entries:
                full = os.path.join(path, e)
                if os.path.isdir(full):
                    iid = add_item(parent, e + os.sep, full)
                    try:
                        # 懒加载占位：展开该节点时才遍历子目录（大工作区不冻结 UI）
                        tree.insert(iid, "end", text="…")
                    except Exception:
                        pass
                else:
                    try:
                        size = os.path.getsize(full)
                        size_txt = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
                    except OSError:
                        size_txt = ""
                    add_item(parent, e, full, (size_txt,))

        def on_tree_open(_event):
            # 用 focus() 而非 selection()：点击展开箭头时节点往往尚未被选中，
            # 用选中项判断会导致子目录懒加载失效
            iid = tree.focus()
            if not iid:
                return
            full = paths.get(iid)
            if full and os.path.isdir(full):
                populate(iid, full)

        tree.bind("<<TreeviewOpen>>", on_tree_open)

        root_iid = add_item("", "", WORKSPACE_DIR)
        tree.item(root_iid, text=f"工作区：{WORKSPACE_DIR}", open=True)
        populate(root_iid, WORKSPACE_DIR)  # 只遍历根目录一层，子目录按需懒加载

        def inject_file():
            sel = tree.selection()
            if not sel:
                return
            full = paths.get(sel[0])
            if not full or not os.path.isfile(full):
                return
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(8000)
                if len(content) >= 8000:
                    content += "\n[文件较大，已截断前 8000 字符]"
            except Exception as e:
                content = f"读取失败: {e}"
            self._clear_placeholder()
            self.input_text.delete("1.0", "end")
            self.input_text.insert(
                "1.0", f"[文件] {os.path.basename(full)}:\n{content}\n\n"
            )
            self.input_text.focus_set()
            dialog.destroy()
            self._flash_status(f"已注入 {os.path.basename(full)}")

        def copy_path():
            sel = tree.selection()
            if not sel:
                return
            full = paths.get(sel[0])
            if full:
                self.root.clipboard_clear()
                self.root.clipboard_append(full)
                self._flash_status("已复制路径")

        def open_selected():
            sel = tree.selection()
            if not sel:
                return
            full = paths.get(sel[0])
            if full:
                self._open_path(full)

        def open_folder():
            sel = tree.selection()
            if not sel:
                return
            full = paths.get(sel[0])
            if not full:
                return
            target = os.path.dirname(full) if os.path.isfile(full) else full
            try:
                os.startfile(target)
            except Exception:
                self._flash_status(f"无法打开目录：{target}")

        tree.bind("<Double-1>", lambda e: inject_file())
        tree.bind("<Button-3>", lambda e: copy_path())
        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=12, pady=(8, 12))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "注入选中文件", inject_file, kind="primary", fsz=9).pack(side="left")
        self._mk_button(bar, "打开", open_selected, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "打开所在文件夹", open_folder, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "复制路径", copy_path, fsz=9).pack(side="left", padx=(6, 0))
        hint = self._lbl(
            bar, "双击注入对话 · 右键复制路径", role="label_sec", bg="panel",
            font=(FONT_FAMILY, 9),
        )
        hint.pack(side="right")

    def show_cleanup(self):
        t = self._theme()
        targets = [
            ("历史会话（sessions/）", SESSIONS_DIR, "dir"),
            ("最近会话快照", SNAPSHOT_PATH, "file"),
            ("用量统计", STATS_PATH, "file"),
            ("上下文归档（archives/）", ARCHIVES_DIR, "dir"),
            ("日志（logs/，被占用文件跳过）", LOG_DIR, "dir"),
            ("工作区内容", WORKSPACE_DIR, "dir"),
            ("提示词库自定义", PROMPTS_PATH, "file"),
            ("自定义工具", USER_TOOLS_PATH, "file"),
            ("Profile 配置", PROFILES_PATH, "file"),
            ("权限配置", PERMISSIONS_PATH, "file"),
            ("定时任务", SCHEDULES_PATH, "file"),
            ("长期记忆", MEMORY_PATH, "file"),
            ("失败模式库", FAILURES_PATH, "file"),
        ]
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("数据清理")
        dialog.geometry("420x460")
        dialog.transient(self.root)
        body = tk.Frame(dialog, bg=t["panel"])
        body.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        self._restyle.append((body, "panel"))
        self._lbl(
            body, "勾选要清理的数据（操作不可恢复）：",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        vars_ = {}
        for name, _p, _k in targets:
            v = tk.BooleanVar(value=False)
            vars_[name] = v
            ttk.Checkbutton(body, text=name, variable=v).pack(anchor="w", pady=1)

        def select_all():
            all_on = all(v.get() for v in vars_.values())
            for v in vars_.values():
                v.set(not all_on)

        def run_cleanup():
            chosen = [t for t in targets if vars_[t[0]].get()]
            if not chosen:
                messagebox.showinfo("提示", "请至少选择一项。")
                return
            if not messagebox.askyesno(
                "确认清理", f"将删除 {len(chosen)} 类数据，此操作不可恢复。确定？"
            ):
                return
            done = []
            for name, path, kind in chosen:
                n = _delete_target(path, kind)
                done.append(f"· {name}：删除 {n} 个文件")
            self._flash_status("数据清理完成")
            messagebox.showinfo("清理完成", "\n".join(done))

        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=16, pady=(12, 14))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "全选/取消", select_all, fsz=9).pack(side="left")
        self._mk_button(bar, "清理", run_cleanup, kind="danger", fsz=9).pack(side="left", padx=(8, 0))
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

    EVOLUTION_AUDIT_TASKS = {
        "全面审查": "全面代码审查",
        "性能优化": "性能瓶颈审查",
        "安全加固": "安全边界审查",
        "体验优化": "界面交互与可用性审查",
        "代码质量": "代码结构与可维护性审查",
    }

    def open_review_reports(self):
        """打开审查报告目录（工作区 code-review/，无则创建）。"""
        target = os.path.join(self._get_active_dir(), "code-review")
        try:
            os.makedirs(target, exist_ok=True)
            os.startfile(target)
        except Exception:
            try:
                os.startfile(self._get_active_dir())
            except Exception:
                self._flash_status(f"无法打开目录：{target}")

    def show_feature_suggestions(self):
        """功能建议：鲸语基于对自身的完整理解，提出新功能与升级方向（产出 MD 文档）。"""
        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("功能建议（鲸语基于自我认知提出升级方向）")
        dialog.geometry("460x280")
        dialog.transient(self.root)
        body = tk.Frame(dialog, bg=t["panel"])
        body.pack(fill="both", expand=True, padx=18, pady=(14, 0))
        self._restyle.append((body, "panel"))
        self._lbl(
            body, "🧬 让鲸语基于对自身代码与能力的理解，提出新功能建议",
            role="label_accent", bg="panel", font=(FONT_FAMILY, 10, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        self._lbl(
            body,
            "鲸语将：分析自身架构与现有能力 → 结合用户场景与 DeepSeek 能力特性 "
            "→ 提出 6-10 个功能建议/升级方向（名称/价值/实现思路/复杂度/优先级）"
            "→ 写入工作区 code-review/ 建议文档。",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
            wraplength=400, justify="left",
        ).pack(anchor="w", pady=(0, 12))

        def run():
            prompt = (
                "请基于对鲸语自身的完整理解，提出新的功能添加与升级建议（不是审查 bug）。\n"
                "1. 用 project_info 了解项目全貌。\n"
                "2. 用 read_project_file 阅读关键模块（main.py 可分页、deepseek_client.py、"
                "permissions.py、tokens.py、processpanel.py、taskpanel.py 等），理解现有能力。\n"
                "3. 结合：用户实际使用场景（对话/智能体/办公/创作/自我进化）、"
                "DeepSeek V4 的能力特性（1M 上下文、思考模式、工具调用、缓存计费、峰谷定价）、"
                "以及业界 AI 助手产品趋势。\n"
                "4. 提出 6-10 个功能建议或升级方向，每个必须包含：\n"
                "   - 建议名称与一句话描述\n"
                "   - 价值（解决什么痛点 / 带来什么收益）\n"
                "   - 实现思路（涉及哪些模块、大致怎么做）\n"
                "   - 复杂度（低/中/高）与优先级（高/中/低）\n"
                "5. 用 create_doc 在工作区 code-review/ 生成《鲸语功能建议_日期时间.md》。\n"
                "6. 回复中给出文档路径与 Top 3 建议摘要。"
            )
            self.send(text=prompt)
            dialog.destroy()

        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=18, pady=(12, 14))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "开始分析", run, kind="primary").pack(side="right")
        self._mk_button(bar, "取消", dialog.destroy).pack(side="right", padx=(0, 8))

    def show_evolution_audit(self):
        """管理员主动发起：让鲸语自我审查并提交进化提案。"""
        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("发起自我审查（鲸语分析自身代码并提交改进提案）")
        dialog.geometry("440x320")
        dialog.transient(self.root)
        body = tk.Frame(dialog, bg=t["panel"])
        body.pack(fill="both", expand=True, padx=18, pady=(14, 0))
        self._restyle.append((body, "panel"))
        self._lbl(
            body, "选择审查重点：", role="label_sec", bg="panel",
            font=(FONT_FAMILY, 10, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        focus_var = tk.StringVar(value="全面审查")
        for key in self.EVOLUTION_AUDIT_TASKS:
            ttk.Radiobutton(
                body, text=f"🔍 {key}", value=key, variable=focus_var,
            ).pack(anchor="w", pady=2)
        self._lbl(
            body,
            "鲸语将：分析项目结构 → 阅读关键模块 → 定位具体问题 → "
            "在工作区 code-review/ 生成完整审查报告 MD（现状代码 / 替换代码 / 验证方式），"
            "供开发 AI 实施。",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
            wraplength=380, justify="left",
        ).pack(anchor="w", pady=(10, 0))

        def run():
            focus = focus_var.get()
            label = self.EVOLUTION_AUDIT_TASKS.get(focus, "全面代码审查")
            prompt = (
                f"请对鲸语自身进行一次{label}，产出审查报告文档（不是修改代码）。\n"
                "重要：鲸语项目位于程序安装目录（不是工作区 Documents\\WhaleTalk\\workspace，"
                "工作区是空的）。你必须使用专用工具：\n"
                "1. 用 project_info 了解项目结构与规模。\n"
                "2. 用 read_project_file 阅读关键模块（main.py、deepseek_client.py、"
                "permissions.py、tokens.py、stats.py、crypto.py、processpanel.py、"
                "taskpanel.py、exporters.py 等）。\n"
                "3. 禁止使用 list_dir / read_file / search_local 等工作区工具分析自身代码。\n"
                f"4. 找出{label}方面的具体问题（至少 3 个，按严重度排序，引用具体代码位置与原因）。\n"
                "5. 用 create_doc 在工作区 code-review 子目录生成《鲸语代码审查报告_日期时间.md》，"
                "报告必须包含：\n"
                "   - 问题总览表（严重度 / 文件 / 位置 / 问题 / 是否建议修复）\n"
                "   - 每个核心问题的【现状代码 / 替换代码 / 验证方式】（替换代码必须是完整可直接使用的补丁）\n"
                "   - 低危观察项清单\n"
                "   - 给实施 AI 的步骤清单与风险回滚说明\n"
                "6. 完成后在回复中给出报告完整路径与核心问题摘要。"
            )
            self.send(text=prompt)
            dialog.destroy()

        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=18, pady=(12, 14))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "开始审查", run, kind="primary").pack(side="right")
        self._mk_button(bar, "取消", dialog.destroy).pack(side="right", padx=(0, 8))

    def _last_evolution_time(self):
        """最近一次进化提案的创建时间（秒），无提案返回 None。"""
        if not os.path.isdir(EVOLUTIONS_DIR):
            return None
        times = []
        try:
            for d in os.listdir(EVOLUTIONS_DIR):
                p = os.path.join(EVOLUTIONS_DIR, d)
                if os.path.isdir(p):
                    try:
                        times.append(os.path.getmtime(p))
                    except OSError:
                        pass
        except Exception:
            pass
        return max(times) if times else None

    def _maybe_remind_evolution(self):
        """督促发起：距上次提案超过阈值时提示（启动后调用一次）。"""
        days = int(self.cfg.get("evolution_reminder_days", 7) or 0)
        if days <= 0:
            return
        last = self._last_evolution_time()
        try:
            if last is None:
                self._flash_status(
                    "🧬 鲸语支持自我进化：工具 → 🧬 自我进化 可立即发起自我审查", 10000
                )
                return
            age_days = (time.time() - last) / 86400
            if age_days >= days:
                self._flash_status(
                    f"🧬 距上次自我进化已 {int(age_days)} 天，"
                    "可发起一次自我审查（工具 → 🧬 自我进化）",
                    10000,
                )
        except Exception:
            pass

    def list_evolutions(self):
        """列出未采纳的进化提案（evolutions/ 下的分支目录）。"""
        items = []
        if not os.path.isdir(EVOLUTIONS_DIR):
            return items
        try:
            for d in sorted(os.listdir(EVOLUTIONS_DIR)):
                if d.endswith("_applied"):
                    continue
                full = os.path.join(EVOLUTIONS_DIR, d)
                if os.path.isdir(full):
                    files = [f for f in os.listdir(full) if f != "EVOLUTION.md"]
                    items.append({"name": d, "dir": full, "files": files})
        except Exception:
            logging.exception("扫描进化提案失败")
        return items

    def show_evolutions(self):
        t = self._theme()
        items = self.list_evolutions()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title(f"自我进化提案（{len(items)} 个待处理）")
        dialog.geometry("640x460")
        dialog.transient(self.root)
        left = tk.Frame(dialog, bg=t["panel"])
        left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        self._restyle.append((left, "panel"))
        listbox = tk.Listbox(
            left, width=26, bg=t["input_bg"], fg=t["input_fg"],
            selectbackground=t["selection"], selectforeground=t["accent_text"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], exportselection=False,
        )
        listbox.pack(fill="both", expand=True)
        for it in items:
            listbox.insert("end", it["name"])
        if not items:
            listbox.insert("end", "（暂无提案）")

        right = tk.Frame(dialog, bg=t["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)
        self._restyle.append((right, "panel"))
        viewer = tk.Text(
            right, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], padx=10, pady=8,
        )
        viewer.pack(fill="both", expand=True)

        def show_item(idx):
            it = items[idx]
            md = os.path.join(it["dir"], "EVOLUTION.md")
            text = ""
            if os.path.exists(md):
                try:
                    with open(md, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read(4000)
                except Exception:
                    pass
            text += "\n\n── 修改文件 ──\n" + "\n".join("· " + f for f in it["files"])
            viewer.delete("1.0", "end")
            viewer.insert("1.0", text)

        def select_item(_e=None):
            sel = listbox.curselection()
            if sel and sel[0] < len(items):
                show_item(sel[0])

        listbox.bind("<<ListboxSelect>>", select_item)

        def view_diff():
            sel = listbox.curselection()
            if not sel or sel[0] >= len(items):
                return
            self._show_evolution_diff(items[sel[0]])

        def apply_item():
            sel = listbox.curselection()
            if not sel or sel[0] >= len(items):
                return
            self._apply_evolution(items[sel[0]]["name"])
            dialog.destroy()
            self.show_evolutions()

        def ignore_item():
            sel = listbox.curselection()
            if not sel or sel[0] >= len(items):
                return
            name = items[sel[0]]["name"]
            if messagebox.askyesno("忽略提案", f"确认忽略并删除提案「{name}」？"):
                try:
                    shutil.rmtree(items[sel[0]]["dir"], ignore_errors=True)
                except Exception:
                    pass
                dialog.destroy()
                self.show_evolutions()

        def verify_item():
            """提案自检：对提案目录内的 .py 文件做语法编译检查（不应用，只验证）。"""
            sel = listbox.curselection()
            if not sel or sel[0] >= len(items):
                return
            it = items[sel[0]]
            py_files = [
                os.path.join(it["dir"], rel)
                for rel in it["files"]
                if rel.endswith(".py")
            ]
            if not py_files:
                messagebox.showinfo("提案自检", "该提案没有 Python 文件可检查")
                return
            errors = []
            ok_n = 0
            for f in py_files:
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        source = fh.read()
                    compile(source, f, "exec")
                    ok_n += 1
                except SyntaxError as e:
                    errors.append(f"语法错误 {os.path.basename(f)}：{e}")
                except Exception as e:
                    errors.append(f"读取失败 {os.path.basename(f)}：{e}")
            if errors:
                messagebox.showwarning(
                    "提案自检未通过",
                    f"{len(py_files)} 个文件中 {len(errors)} 个存在问题：\n\n" + "\n".join(errors),
                )
            else:
                messagebox.showinfo(
                    "提案自检通过",
                    f"✅ {ok_n}/{len(py_files)} 个 Python 文件语法检查全部通过，可安全采纳。",
                )

        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=12, pady=(0, 12))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "查看差异", view_diff, fsz=9).pack(side="left")
        self._mk_button(bar, "提案自检", verify_item, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "采纳（应用到项目）", apply_item, kind="primary", fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "忽略", ignore_item, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")
        if items:
            listbox.selection_set(0)
            show_item(0)

    def _show_evolution_diff(self, item):
        import difflib

        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title(f"差异预览：{item['name']}")
        dialog.geometry("760x520")
        dialog.transient(self.root)
        text = tk.Text(
            dialog, wrap="none", bg=t["code_bg"], fg=t["code_fg"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], font=(MONO_FAMILY, 9), padx=10, pady=8,
        )
        text.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        for rel in item["files"]:
            new_path = os.path.join(item["dir"], rel)
            old_path = os.path.join(BASE_DIR, rel)
            try:
                with open(new_path, "r", encoding="utf-8", errors="replace") as f:
                    new_lines = f.readlines()
                if os.path.exists(old_path):
                    with open(old_path, "r", encoding="utf-8", errors="replace") as f:
                        old_lines = f.readlines()
                    diff = list(difflib.unified_diff(
                        old_lines, new_lines, fromfile=f"{rel}（当前）", tofile=f"{rel}（提案）"
                    ))
                    text.insert("end", "".join(diff) if diff else f"{rel}：无差异\n")
                else:
                    text.insert("end", f"--- {rel}（新文件）---\n" + "".join(new_lines))
            except Exception as e:
                text.insert("end", f"{rel}：读取失败 {e}\n")
            text.insert("end", "\n" + "=" * 60 + "\n")
        text.configure(state="disabled")
        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=12, pady=(8, 12))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

    def _apply_evolution(self, name):
        src = os.path.join(EVOLUTIONS_DIR, name)
        if not messagebox.askyesno(
            "采纳进化提案",
            f"将把「{name}」中的文件应用到项目目录（原文件自动备份为 .evobak）。\n"
            "建议先运行 python backup.py 备份整个项目。\n\n确定采纳？",
        ):
            return
        applied = []
        for root, _dirs, files in os.walk(src):
            for f in files:
                if f == "EVOLUTION.md":
                    continue
                rel = os.path.relpath(os.path.join(root, f), src)
                if not rel.endswith((".py", ".md", ".json", ".txt", ".bat", ".html")):
                    continue
                target = os.path.join(BASE_DIR, rel)
                try:
                    if os.path.exists(target):
                        shutil.copy2(target, target + ".evobak")
                    os.makedirs(os.path.dirname(target) or BASE_DIR, exist_ok=True)
                    shutil.copy2(os.path.join(root, f), target)
                    applied.append(rel)
                except Exception as e:
                    messagebox.showerror("采纳失败", f"{rel}: {e}")
        try:
            os.rename(src, src + "_applied")
        except Exception:
            pass
        self._flash_status(
            f"已采纳进化提案「{name}」：{len(applied)} 个文件已应用（原文件备份 .evobak），重启后生效"
        )

    def manage_profiles(self):
        t = self._theme()
        data = load_profiles()
        profiles = data["profiles"]
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("Profile 管理（多账号 / 多端点）")
        dialog.geometry("620x460")
        dialog.transient(self.root)

        left = tk.Frame(dialog, bg=t["panel"])
        left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        self._restyle.append((left, "panel"))
        listbox = tk.Listbox(
            left,
            width=22,
            bg=t["input_bg"],
            fg=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
            exportselection=False,
        )
        listbox.pack(fill="both", expand=True)
        for name in profiles:
            listbox.insert("end", name)

        right = tk.Frame(dialog, bg=t["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)
        self._restyle.append((right, "panel"))
        fields = {}
        rows = [
            ("name", "Profile 名称"),
            ("api_key", "API Key"),
            ("base_url", "Base URL（留空用官方端点）"),
            ("model", "模型（deepseek-v4-flash / deepseek-v4-pro）"),
        ]
        for key, label in rows:
            self._lbl(right, label, role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w")
            var = tk.StringVar()
            ttk.Entry(right, textvariable=var, show="*" if key == "api_key" else None).pack(fill="x", pady=(2, 6))
            fields[key] = var
        current_lbl = self._lbl(
            right,
            f"当前 Profile：{data['current'] or '（config.json 默认）'}",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        )
        current_lbl.pack(anchor="w", pady=(0, 8))

        def select_item(_e=None):
            sel = listbox.curselection()
            if not sel:
                return
            name = listbox.get(sel[0])
            p = profiles.get(name) or {}
            fields["name"].set(name)
            fields["api_key"].set(p.get("api_key", ""))
            fields["base_url"].set(p.get("base_url", ""))
            fields["model"].set(p.get("model", ""))

        def refresh_list():
            listbox.delete(0, "end")
            for name in profiles:
                listbox.insert("end", name)

        def save_item():
            name = fields["name"].get().strip()
            if not name:
                messagebox.showinfo("提示", "Profile 名称必填。")
                return
            profiles[name] = {
                "api_key": fields["api_key"].get().strip(),
                "base_url": fields["base_url"].get().strip(),
                "model": fields["model"].get().strip(),
            }
            save_profiles({"profiles": profiles, "current": data["current"]})
            refresh_list()
            self._flash_status(f"Profile「{name}」已保存")

        def delete_item():
            sel = listbox.curselection()
            if not sel:
                return
            name = listbox.get(sel[0])
            if messagebox.askyesno("删除 Profile", f"确认删除 Profile「{name}」？"):
                profiles.pop(name, None)
                save_profiles({"profiles": profiles, "current": data["current"]})
                refresh_list()
                self._flash_status(f"已删除 Profile「{name}」")

        def switch_to():
            sel = listbox.curselection()
            if not sel:
                return
            name = listbox.get(sel[0])
            p = profiles.get(name)
            if not p:
                return
            if self.busy:
                messagebox.showinfo("提示", "请先停止当前生成。")
                return
            data["current"] = name
            save_profiles({"profiles": profiles, "current": name})
            self._apply_profile(name, p)
            current_lbl.configure(text=f"当前 Profile：{name}")
            self._flash_status(f"已切换到 Profile「{name}」")

        def new_item():
            listbox.selection_clear(0, "end")
            for var in fields.values():
                var.set("")

        listbox.bind("<<ListboxSelect>>", select_item)
        bar = tk.Frame(right, bg=t["panel"])
        bar.pack(fill="x")
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "新增", new_item, fsz=9).pack(side="left")
        self._mk_button(bar, "保存", save_item, kind="primary", fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "删除", delete_item, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "设为当前", switch_to, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

    def _on_adv_toggle(self):
        """设置面板「高级参数」折叠区显隐。"""
        try:
            if self.adv_expanded.get():
                self.adv_frame.pack(fill="x", pady=(2, 0))
            else:
                self.adv_frame.pack_forget()
        except tk.TclError:
            pass

    def _on_browser_mode_change(self):
        """浏览器有头/无头一键切换：有头=弹出真实窗口可实时预览。"""
        visible = bool(self.browser_visible_var.get())
        self.cfg["browser_headless"] = not visible
        _dc.BROWSER_HEADLESS = not visible
        save_config(self.cfg)
        if visible:
            self._flash_status("🖥 浏览器可见模式：AI 操作浏览器时会弹出窗口，可实时观看")
        else:
            self._flash_status("浏览器无头模式：静默后台运行，不弹窗口")

    def _on_mode_change(self):
        """三态模式单选：标准 / 完全智能 / 纯对话（互斥，消除同时开启的语义冲突）。"""
        mode = self.mode_var.get()
        if mode == "full_auto" and not self.cfg.get("full_auto"):
            if not messagebox.askyesno(
                "完全智能模式",
                "开启后，允许目录内的写文件 / 运行命令 / 工具链将全部自动执行，"
                "不再弹出审批确认。\n\n系统目录与阻止列表仍然生效，审计日志继续记录。\n\n确定开启？",
            ):
                self._sync_mode_var()
                return
        if mode == "full_auto":
            self.cfg["full_auto"] = True
            self.cfg["pure_chat"] = False
            self._flash_status(
                "🤖 完全智能已开启——全部工具自动可用，为开发/创作而生"
                "（建议应用「智能体」人格：角色与提示词 → 智能体）"
            )
        elif mode == "pure_chat":
            self.cfg["full_auto"] = False
            self.cfg["pure_chat"] = True
        else:
            self.cfg["full_auto"] = False
            self.cfg["pure_chat"] = False
        permissions.set_full_auto(self.cfg["full_auto"])
        save_config(self.cfg)
        self.update_status()
        if self.cfg["full_auto"]:
            self._flash_status("🤖 完全智能模式已开启——允许目录内全自动，我只要结果")
        elif self.cfg["pure_chat"]:
            self._flash_status("💬 纯对话模式已开启——AI 回归纯粹对话，不注入工具提示词")
        else:
            self._flash_status("已恢复标准模式")

    def _sync_mode_var(self):
        """根据当前 cfg 回填单选值（取消确认时恢复显示）。"""
        try:
            if self.cfg.get("full_auto"):
                self.mode_var.set("full_auto")
            elif self.cfg.get("pure_chat"):
                self.mode_var.set("pure_chat")
            else:
                self.mode_var.set("standard")
        except Exception:
            pass

    def _switch_mode_quick(self, mode):
        """快捷切换自主模式（建议条采纳入口），复用确认与保存逻辑。"""
        try:
            self.mode_var.set(mode)
            self._on_mode_change()
        except Exception:
            pass

    def _plan_gate(self, tool_summary):
        """每轮工具调用前的计划确认（worker 线程调用）。

        permissions.plan_confirm 关闭或完全智能模式时直接放行（保持现状）；
        开启时整轮工具链一次弹窗确认，避免逐个工具弹窗。
        """
        if permissions.is_full_auto():
            return True, ""
        if not permissions.get_data().get("plan_confirm", False):
            return True, ""
        ev = threading.Event()
        box = {"allow": False, "reason": "计划确认超时未响应（自动取消）"}
        self._ui_queue.put(("plan_req", (tool_summary, ev, box)))
        self._wait_stop_aware(ev, permissions.approval_timeout())
        if box["allow"]:
            return True, ""
        return False, box.get("reason", "用户取消了计划")

    def _show_plan_dialog(self, tool_summary, ev, box):
        """主线程：AI 执行计划确认弹窗（显示本轮工具链）。"""
        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("AI 执行计划确认")
        dialog.geometry("560x320")
        dialog.transient(self.root)
        dialog.attributes("-topmost", True)
        self._register_modal(dialog)
        body = tk.Frame(dialog, bg=t["panel"])
        body.pack(fill="both", expand=True, padx=16, pady=(14, 0))
        self._restyle.append((body, "panel"))
        self._lbl(
            body, "AI 计划执行以下工具调用：",
            role="label_accent", bg="panel", font=(FONT_FAMILY, 11, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        for i, (name, args_s) in enumerate(tool_summary, 1):
            self._lbl(
                body, f"{i}. {name}  {args_s[:160]}",
                bg="panel", font=(MONO_FAMILY, 9), wraplength=500, justify="left",
            ).pack(anchor="w", pady=2)
        self._lbl(
            body, "确认后执行；取消则本轮计划终止并告知 AI。",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(8, 0))
        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=16, pady=(12, 14))
        self._restyle.append((bar, "panel"))

        def done(allow, reason):
            box["allow"] = allow
            box["reason"] = reason
            try:
                ev.set()
            except Exception:
                pass
            dialog.destroy()

        self._mk_button(bar, "执行计划", lambda: done(True, "用户已确认"), kind="primary").pack(side="right")
        self._mk_button(bar, "取消", lambda: done(False, "用户取消了计划")).pack(side="right", padx=(0, 8))
        self._watch_dialog_timeout(dialog, ev, permissions.approval_timeout())

    def _flash_taskbar(self):
        """任务栏闪烁（后台任务完成提示）。"""
        try:
            import ctypes

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("hwnd", ctypes.c_void_p),
                    ("dwFlags", ctypes.c_uint),
                    ("uCount", ctypes.c_uint),
                    ("dwTimeout", ctypes.c_uint),
                ]

            FLASHW_ALL = 3
            FLASHW_TIMERNOFG = 12
            hwnd = self.root.winfo_id()
            info = FLASHWINFO(
                ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_ALL | FLASHW_TIMERNOFG, 5, 0
            )
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass

    def _relevant_files_text(self, text):
        """从用户消息提取引用的本地文件（工作目录内优先），读取内容注入上下文。"""
        base = self._get_active_dir()
        hits = []
        for mm in PATH_RE.finditer(text or ""):
            p = mm.group(0).rstrip("。.,;: \t")
            if os.path.isfile(p):
                hits.append(p)
        for mm in re.finditer(
            r"[\w\-\u4e00-\u9fff]+\.(?:md|py|txt|json|html|css|js|ts|yaml|yml|toml|ini|csv)",
            text or "",
        ):
            cand = os.path.join(base, mm.group(0))
            if os.path.isfile(cand) and cand not in hits:
                hits.append(cand)
        if not hits:
            return ""
        parts = ["[任务相关文件] 用户任务中提到的文件内容（供直接参考，无需再读取）："]
        for p in hits[:4]:
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(6000)
                if len(content) >= 6000:
                    content += "\n[内容较长已截断]"
                parts.append(f"--- {os.path.basename(p)}（{p}）---\n{content}")
            except Exception:
                continue
        return "\n\n".join(parts)

    def _patterns_text(self):
        """成功模式记忆：最近成功任务的工具链，提示 AI 复用。"""
        try:
            pats = self._load_patterns()
            if not pats:
                return ""
            lines = ["[历史成功模式] 以下任务此前成功完成过，遇到类似任务可复用其做法："]
            for p in pats[-3:]:
                chain = p.get("chain") or []
                if chain:
                    lines.append(f"- 工具链：{' → '.join(chain)}")
            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception:
            return ""

    @staticmethod
    def _load_patterns():
        try:
            if os.path.exists(PATTERNS_PATH):
                with open(PATTERNS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            logging.exception("读取成功模式失败")
        return []

    @staticmethod
    def _save_patterns(pats):
        return AssistantApp._atomic_json_write(PATTERNS_PATH, pats)

    # ===== 失败模式库：工具失败自动积累，注入上下文供 AI 规避已知坑 =====
    FAILURES_MAX = 50

    @staticmethod
    def _load_failures():
        try:
            if os.path.exists(FAILURES_PATH):
                with open(FAILURES_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            logging.exception("读取失败模式失败")
        return []

    @staticmethod
    def _save_failures(items):
        return AssistantApp._atomic_json_write(FAILURES_PATH, items, indent=1)

    @staticmethod
    def _append_failures(new_items):
        """追加失败记录：同 (工具, 错误摘要前 50 字符) 去重并更新时间戳，上限 50 条。

        纯静态方法（不依赖实例）：_finish 统计循环结束后在主线程调用。
        """
        if not new_items:
            return
        try:
            items = AssistantApp._load_failures()
            for it in new_items:
                key = (str(it.get("tool") or ""), str(it.get("error") or "")[:50])
                replaced = False
                for old in items:
                    if (str(old.get("tool") or ""), str(old.get("error") or "")[:50]) == key:
                        old["ts"] = it["ts"]
                        replaced = True
                        break
                if not replaced:
                    items.append(it)
            if len(items) > AssistantApp.FAILURES_MAX:
                del items[: len(items) - AssistantApp.FAILURES_MAX]
            AssistantApp._save_failures(items)
        except Exception:
            logging.exception("记录失败模式失败")

    @staticmethod
    def _failure_patterns_text():
        """已知失败模式注入（最近 3 条，固定格式缓存友好）。"""
        try:
            items = AssistantApp._load_failures()
            if not items:
                return ""
            lines = ["[已知失败模式] 以下工具调用曾失败（遇到同类情况请规避或改用其他方式）："]
            for it in items[-3:]:
                tool = str(it.get("tool") or "?")
                err = str(it.get("error") or "")[:100]
                if err:
                    lines.append(f"- {tool}：{err}")
            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception:
            return ""

    def _record_success_pattern(self):
        """任务全部成功时记录工具链模式（上限 10 条）。"""
        try:
            chain = []
            for b in self.blocks:
                if b[0] == "tool":
                    res = str(b[1][2] or "")
                    if not res.startswith(_dc.TOOL_RESULT_FAIL_PREFIXES):
                        chain.append(str(b[1][0]))
            if not chain:
                return
            pats = self._load_patterns()
            pats.append({"chain": chain, "ts": datetime.now().isoformat(timespec="seconds")})
            if len(pats) > 10:
                del pats[: len(pats) - 10]
            self._save_patterns(pats)
        except Exception:
            pass

    def _project_context_text(self):
        """工作区概览注入（配置 project_context 开启，60 秒缓存，固定内容缓存友好）。"""
        if not self.cfg.get("project_context"):
            return ""
        if not os.path.isdir(WORKSPACE_DIR):
            return ""
        now = time.monotonic()
        if (
            getattr(self, "_proj_ctx_cache", None)
            and now - getattr(self, "_proj_ctx_time", 0) < 60
        ):
            return self._proj_ctx_cache
        try:
            entries = sorted(os.listdir(WORKSPACE_DIR))
            lines = ["[项目上下文] 当前工作区内容概览："]
            shown = 0
            for e in entries:
                if shown >= 30:
                    break
                full = os.path.join(WORKSPACE_DIR, e)
                if os.path.isdir(full):
                    lines.append(f"- {e}/")
                else:
                    try:
                        size = os.path.getsize(full)
                        size_txt = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
                    except OSError:
                        size_txt = ""
                    lines.append(f"- {e}（{size_txt}）")
                shown += 1
            if not entries:
                # 空工作区也写缓存：否则每次发送都重扫目录，60s 缓存形同虚设
                self._proj_ctx_cache = ""
                self._proj_ctx_time = now
                return ""
            for kf in ("README.md", "README.txt", "package.json", "pyproject.toml", "requirements.txt"):
                kpath = os.path.join(WORKSPACE_DIR, kf)
                if os.path.isfile(kpath):
                    try:
                        with open(kpath, "r", encoding="utf-8", errors="replace") as f:
                            head = f.read(1500)
                        if head.strip():
                            lines.append(f"── {kf} 摘要 ──")
                            lines.append(head[:1500])
                    except Exception:
                        pass
            text = "\n".join(lines)
            self._proj_ctx_cache = text
            self._proj_ctx_time = now
            return text
        except Exception:
            return ""

    def _get_active_dir(self):
        """当前任务工作目录：用户指定或默认工作区。"""
        d = str(self.cfg.get("active_dir") or "").strip()
        if d and os.path.isdir(d):
            return d
        return WORKSPACE_DIR

    def _set_active_dir(self, path, add_perm=True):
        """设置工作目录：校验存在、自动加入权限允许目录、持久化。"""
        try:
            p = os.path.abspath(os.path.expanduser(str(path or "").strip()))
            if not os.path.isdir(p):
                return False, f"目录不存在：{p}"
            os.makedirs(p, exist_ok=True)
            self.cfg["active_dir"] = p
            save_config(self.cfg)
            _dc.WORKING_DIR = p  # 命令执行类工具的 cwd 跟随工作目录
            if add_perm:
                d = permissions.get_data()
                allowed = [str(x) for x in d["filesystem"].get("allowed_dirs", [])]
                if p not in allowed:
                    allowed.append(p)
                    d["filesystem"]["allowed_dirs"] = allowed
                    permissions.set_data(d)
                    permissions.save()
            self.update_status()
            return True, p
        except Exception as e:
            return False, str(e)

    def _working_dir_prompt(self):
        """工作目录提示注入（与记忆/项目上下文同通道，固定内容缓存友好）。"""
        d = self._get_active_dir()
        return (
            f"[当前工作目录] 所有文件操作、项目创建默认在此目录下进行：{d}\n"
            "新任务请在该目录下创建独立子目录（按任务名命名），不要把文件散落在根目录。"
        )

    def _suggest(self):
        """对话结束后的主动建议（纯启发式规则，无额外 API 成本）。"""
        try:
            if not self.cfg.get("suggestions_enabled", True):
                return
            last = ""
            for b in reversed(self.blocks):
                if b[0] == "content" and isinstance(b[1], str):
                    last = b[1]
                    break
            if not last:
                return
            suggestions = []
            if "```" in last and self.mode_var.get() != "full_auto":
                suggestions.append(("💡 检测到代码输出，建议切换「完全智能」模式（自动调用全部工具）",
                                    self._switch_mode_quick, "full_auto"))
            if any(w in last for w in ("翻译", "润色", "改写", "周报")):
                suggestions.append(("💡 翻译/润色/周报可用「⚡ 指令」模板一键套用", self._show_prompt_menu, None))
            if PATH_RE.search(last):
                suggestions.append(("💡 检测到文件路径，可一键切换工作目录", self.choose_working_dir, None))
            if suggestions:
                self._show_suggestion(suggestions[0])
        except Exception:
            pass

    def _show_suggestion(self, suggestion):
        """固定建议区展示（菜单栏右侧停靠，不弹窗不遮挡；60 秒后自动隐藏）。"""
        try:
            text, action, arg = suggestion
            self._suggestion_state = (action, arg)
            short = text if len(text) <= 60 else text[:58] + "…"
            self.suggestion_lbl.configure(text=short)
            if not self.suggestion_frame.winfo_manager():
                self.suggestion_frame.pack(side="right", padx=8)
            if self._suggestion_after is not None:
                try:
                    self.root.after_cancel(self._suggestion_after)
                except Exception:
                    pass
            self._suggestion_after = self.root.after(60000, self._hide_suggestion)
        except Exception:
            pass

    def _hide_suggestion(self):
        self._suggestion_after = None
        self._suggestion_state = None
        try:
            self.suggestion_frame.pack_forget()
        except Exception:
            pass

    def _suggestion_apply(self):
        state = self._suggestion_state
        self._hide_suggestion()
        if state:
            action, arg = state
            try:
                if arg is not None:
                    action(arg)
                else:
                    action()
            except Exception:
                pass

    def _tasklog_path(self):
        return os.path.join(self._get_active_dir(), ".whaletalk", "tasklog.json")

    def _load_tasklog(self):
        try:
            p = self._tasklog_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("tasks", [])
                    return data
        except Exception:
            logging.exception("读取项目任务记录失败")
        return {"tasks": []}

    def _save_tasklog(self, data):
        try:
            p = self._tasklog_path()
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            self._atomic_json_write(p, data, indent=2)
        except Exception:
            logging.exception("保存项目任务记录失败")

    def _record_tasklog(self):
        """任务完成时把摘要追加到当前工作目录的 tasklog（上限 20 条，跨会话交接）。"""
        try:
            chain = []
            artifacts = []
            for b in self.blocks:
                if b[0] == "tool":
                    res = str(b[1][2] or "")
                    chain.append(str(b[1][0]))
                    for mm in PATH_RE.finditer(res):
                        p = mm.group(0).rstrip("。.,;: \t")
                        if os.path.exists(p) and p not in artifacts:
                            artifacts.append(p)
            if not chain:
                return
            title = ""
            for mm in reversed(self.messages):
                if mm.get("role") == "user" and mm.get("content"):
                    title = " ".join(str(mm["content"]).split())[:40]
                    break
            data = self._load_tasklog()
            data.setdefault("tasks", []).append(
                {
                    "title": title or "任务",
                    "chain": chain,
                    "artifacts": artifacts,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                }
            )
            if len(data["tasks"]) > 20:
                del data["tasks"][: len(data["tasks"]) - 20]
            self._save_tasklog(data)
        except Exception:
            pass

    def _tasklog_prompt(self):
        """项目任务记忆注入（最近 3 条，固定格式缓存友好）。"""
        try:
            data = self._load_tasklog()
            tasks = data.get("tasks") or []
            if not tasks:
                return ""
            lines = ["[项目任务记录] 当前工作目录的历史任务（跨会话交接参考）："]
            for t in tasks[-3:]:
                chain = " → ".join(t.get("chain", [])[:4])
                arts = "、".join(os.path.basename(a) for a in (t.get("artifacts") or [])[:3])
                extra = f"，产物：{arts}" if arts else ""
                lines.append(f"- {t.get('title', '任务')}（{chain}{extra}）")
            return "\n".join(lines)
        except Exception:
            return ""

    def _record_reflection(self, ok_n, fail_n, fail_items):
        """自动经验复盘：任务有失败时把结构化经验沉淀到长期记忆。

        内容：最近任务主题 + 成功/失败统计 + 失败工具与错误首行（去重）。
        记忆随每次请求注入，AI 跨会话遇到同类任务能主动规避已知坑——
        这是「越用越聪明」的经验沉淀（成功模式已由 patterns.json 覆盖，
        此处只记失败，避免记忆膨胀；隐私模式跳过）。
        """
        try:
            if self.cfg.get("privacy_mode"):
                return
            import deepseek_client as _dc_ref

            topic = ""
            for mm in reversed(self.messages):
                if mm.get("role") == "user" and mm.get("content"):
                    topic = " ".join(str(mm["content"]).split())[:60]
                    break
            lines = [f"任务复盘：{topic or '未命名任务'}"]
            lines.append(f"- 工具 {ok_n} 成功 / {fail_n} 失败")
            seen = set()
            for fi in (fail_items or [])[:6]:
                key = str(fi.get("tool") or "?")
                if key in seen:
                    continue
                seen.add(key)
                err = str(fi.get("error") or "")[:90]
                lines.append(f"- ⚠ {key}: {err}")
            lines.append(f"- 时间：{datetime.now():%Y-%m-%d %H:%M}")
            _dc_ref.write_memory("\n".join(lines), "经验复盘")
        except Exception:
            logging.exception("自动经验复盘写入失败")

    def show_tasklog(self):
        t = self._theme()
        data = self._load_tasklog()
        tasks = data.get("tasks") or []
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title(f"项目任务记录（{os.path.basename(self._get_active_dir())}，{len(tasks)} 条）")
        dialog.geometry("600x420")
        dialog.transient(self.root)
        text = tk.Text(
            dialog, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], padx=12, pady=10,
        )
        text.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        for task in reversed(tasks):
            chain = " → ".join(task.get("chain", []))
            arts = "、".join(task.get("artifacts", []))
            text.insert(
                "end",
                f"【{task.get('ts', '')}】{task.get('title', '任务')}\n"
                f"工具链：{chain}\n"
                + (f"产物：{arts}\n" if arts else "")
                + "\n",
            )
        if not tasks:
            text.insert("1.0", "（暂无任务记录，AI 在本目录执行过任务后自动记录）")
        text.configure(state="disabled")
        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=12, pady=(8, 12))
        self._restyle.append((bar, "panel"))

        def clear():
            if messagebox.askyesno("清空任务记录", "确认清空当前项目的任务记录？"):
                self._save_tasklog({"tasks": []})
                dialog.destroy()
                self.show_tasklog()

        self._mk_button(bar, "清空", clear, fsz=9).pack(side="left")
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

    @staticmethod
    def _format_memory_fact(f):
        """格式化一条记忆（含类型/实体/关系图谱元数据）。"""
        k = str(f.get("key") or "").strip()
        v = str(f.get("value") or "").strip()
        if not (k and v):
            return None
        meta = []
        if f.get("type"):
            meta.append(f"类型:{str(f['type'])[:16]}")
        ents = f.get("entities") or []
        if ents:
            meta.append(f"实体:{','.join(str(e)[:20] for e in ents[:5])}")
        rels = f.get("relations") or []
        if rels:
            meta.append(
                f"关系:{';'.join(str(r.get('rel'))[:12] + '→' + str(r.get('to'))[:20] for r in rels[:5])}"
            )
        suffix = f"（{'，'.join(meta)}）" if meta else ""
        return f"- {k}: {v}{suffix}"

    def _memory_prompt_text(self):
        """注入通道：任务模式全量；纯对话模式仅 记忆 + 工作目录（不注入工具提示词）。

        任务模式 = 长期记忆 + 项目上下文 + 工作目录 + 行为指令 + 成功模式 + 任务记录 + 相关文件
        纯对话模式 = 长期记忆 + 工作目录（保持对话纯粹，AI 不被工具语义引导）
        """
        if self.cfg.get("pure_chat"):
            parts = []
            try:
                data = self._load_memory()
                if data.get("enabled"):
                    lines = []
                    for f in data.get("facts") or []:
                        line = self._format_memory_fact(f)
                        if line:
                            lines.append(line)
                    if lines:
                        parts.append(
                            "请记住以下关于用户的背景信息，在相关回答中自然参考：\n"
                            + "\n".join(lines)
                        )
            except Exception:
                pass
            # 纯对话 = 记忆 + 工作目录（只给中性上下文，不注入任务指令/工具语义）
            try:
                d = self._get_active_dir()
                if d and os.path.isdir(d):
                    parts.append(f"（用户当前工作目录：{d}）")
            except Exception:
                pass
            return "\n\n".join(parts) if parts else ""
        return self._memory_prompt_stable()

    def _memory_prompt_stable(self):
        """稳定注入（作为 system 消息，配合缓存友好布局保持前缀稳定）：
        长期记忆 + 工作目录 + 行为指令。

        变化频繁的注入（项目上下文/检查点/成功模式/任务记录/失败模式/相关文件）
        走 _memory_prompt_dynamic 追加到本轮 user 消息尾部——记忆刷新
        不会破坏稳定前缀，最大化官方硬盘缓存命中（前缀完整匹配才命中）。
        """
        parts = []
        try:
            data = self._load_memory()
            if data.get("enabled"):
                lines = []
                for f in data.get("facts") or []:
                    line = self._format_memory_fact(f)
                    if line:
                        lines.append(line)
                if lines:
                    parts.append(
                        "以下为你的长期记忆（用户手动维护，含类型/实体/关系，请在相关回答中始终参考）：\n"
                        + "\n".join(lines)
                    )
        except Exception:
            pass
        parts.append(self._working_dir_prompt())
        parts.append(TASK_QUALITY_GUIDE)
        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _plugin_skills_hint():
        """已安装插件技能清单（注入 AI：让 AI 知晓用户可用的技能模板，并在相关任务中建议或直接完成）。"""
        try:
            items = prompts.load_prompts(PROMPTS_PATH)
            plugin_skills = [p for p in items if str(p.get("_source") or "").startswith("plugin:")]
            if not plugin_skills:
                return ""
            names = "、".join(str(p["name"]) for p in plugin_skills[:8])
            return (
                f"[已安装插件技能] 用户装有技能模板：{names}（输入框「⚡ 指令」可一键插入）。"
                "相关任务请直接完成，或建议用户使用对应模板。"
            )
        except Exception:
            return ""

    def _memory_prompt_dynamic(self):
        """动态注入（追加到本轮 user 消息尾部，不破坏稳定前缀缓存）：
        项目上下文 / 任务检查点 / 成功模式 / 任务记录 / 失败模式 / 相关文件。
        """
        parts = []
        proj = self._project_context_text()
        if proj:
            parts.append(proj)
        cp = self._checkpoint_prompt_text()
        if cp:
            parts.append(cp)
        pat = self._patterns_text()
        if pat:
            parts.append(pat)
        tl = self._tasklog_prompt()
        if tl:
            parts.append(tl)
        fp = self._failure_patterns_text()
        if fp:
            parts.append(fp)
        ps = self._plugin_skills_hint()
        if ps:
            parts.append(ps)
        inject = self._current_inject_text
        if inject:
            parts.append(inject)
        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _load_memory():
        try:
            if os.path.exists(MEMORY_PATH):
                with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("enabled", False)
                    data.setdefault("facts", [])
                    return data
        except Exception:
            logging.exception("读取长期记忆失败")
        return {"enabled": False, "facts": []}

    @staticmethod
    def _save_memory(data):
        return AssistantApp._atomic_json_write(MEMORY_PATH, data, indent=2)

    @staticmethod
    def _defer_until(now):
        """高峰错峰顺延目标时刻：最近空闲时段开始（12:00 / 18:00；已过则次日 0:00）。

        仅在高峰时段调用：9-12 高峰 → 12:00；14-18 高峰 → 18:00；
        若对应空闲开始时刻已过（极端情况）→ 次日 0:00。
        """
        h = now.hour
        defer_h = 12 if h < 14 else 18
        ts = now.replace(hour=defer_h, minute=0, second=0, microsecond=0).timestamp()
        if ts <= now.timestamp():
            ts = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return ts

    @staticmethod
    def _budget_thinking(budget, cost, thinking):
        """预算感知思考降档：接近月度预算 80% 时，auto/max 档自动降为 high。

        返回 (effective_thinking, near_budget)。用户显式选择的 low/medium/high
        档位不干预（尊重手动选择）；仅对智能路由与最高档做降级。
        """
        if budget > 0 and cost >= budget * 0.8:
            if thinking in ("auto", "max"):
                return "high", True
            return thinking, True
        return thinking, False

    def _budget_thinking_hint(self, near_budget):
        """预算降档提示（worker 线程经 UI 队列投递）。"""
        if near_budget:
            self._ui_queue.put(
                ("info", "💡 已接近月度预算，本轮思考档位自动降为 high（auto/max → high），节省费用。")
            )

    def _scheduler_loop(self):
        """定时任务线程：每 30 秒检查一次，支持三种调度模式。

        - time "HH:MM"：每日固定时刻执行一次（旧格式兼容）
        - cron "分 时 日 月 周"：标准 5 字段 cron 表达式（支持 * / 逗号 / 连字符 / 步进）
        - every N：每 N 分钟周期执行
        动作：message（发送指令）/ backup（项目备份）/ notify（状态栏+webhook 推送）
              / workflow（运行流程）。
        off_peak：触发时刻处于高峰时段（9-12 / 14-18）时顺延到最近空闲时段
        （官方峰谷定价：空闲价格仅为高峰一半）。
        所有文件读写经 _schedules_lock 串行化，避免与主线程编辑对话框并发覆盖。
        """
        catchup_done = False
        while True:
            try:
                now = datetime.now()
                changed = False
                with self._schedules_lock:
                    schedules = self._load_schedules()
                    if not catchup_done:
                        # 启动补跑：程序未运行时错过的任务补执行一次。
                        # - time 型：今天时刻已过且未标记 → 补跑（只补最近一次，不积压）
                        # - cron 型：找到今天已过去且匹配的最近一个触发分钟 → 补跑一次
                        # - every 型：周期检查在启动后 30s 内自然触发，无需补跑
                        catchup_done = True
                        for s in schedules:
                            if not s.get("enabled"):
                                continue
                            action = str(s.get("action") or "message")
                            today = now.strftime("%Y-%m-%d")
                            if s.get("cron"):
                                last_match = None
                                for hh in range(now.hour + 1):
                                    for mm in range(60):
                                        if hh == now.hour and mm > now.minute:
                                            break
                                        cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                                        if cron_match(str(s["cron"]), cand):
                                            last_match = cand
                                if last_match is not None:
                                    stamp = last_match.strftime("%Y-%m-%d %H:%M")
                                    if s.get("last") != stamp and stamp[:10] == today:
                                        s["last"] = stamp
                                        changed = True
                                        self._dispatch_schedule(s, action)
                            elif not s.get("every"):
                                key = now.strftime("%H:%M")
                                t = str(s.get("time", ""))
                                if t and t < key and s.get("last") != today:
                                    s["last"] = today
                                    changed = True
                                    self._dispatch_schedule(s, action)
                    now_ts = now.timestamp()
                    for s in schedules:
                        if not s.get("enabled"):
                            continue
                        action = str(s.get("action") or "message")
                        # 高峰错峰顺延到期 → 立即触发一次（time/cron 语义：当天一次）
                        defer = float(s.get("defer_until") or 0)
                        if defer and now_ts >= defer:
                            s.pop("defer_until", None)
                            s["last"] = now.strftime("%Y-%m-%d %H:%M")
                            if s.get("every"):
                                s["last_run"] = now_ts
                            changed = True
                            self._dispatch_schedule(s, action)
                            continue
                        fired = False
                        if s.get("cron"):
                            stamp = now.strftime("%Y-%m-%d %H:%M")
                            if cron_match(str(s["cron"]), now) and s.get("last") != stamp:
                                s["last"] = stamp
                                fired = True
                        elif s.get("every"):
                            try:
                                every = int(s.get("every") or 0)
                            except (TypeError, ValueError):
                                every = 0
                            last_run = float(s.get("last_run") or 0)
                            if every > 0 and time.time() - last_run >= every * 60:
                                s["last_run"] = time.time()
                                s["last"] = now.strftime("%Y-%m-%d %H:%M")
                                fired = True
                        else:
                            key = now.strftime("%H:%M")
                            today = now.strftime("%Y-%m-%d")
                            if str(s.get("time", "")) == key and s.get("last") != today:
                                s["last"] = today
                                fired = True
                        if fired:
                            if s.get("off_peak") and is_peak_hour(now):
                                # 错峰任务在高峰命中：顺延到最近空闲时段（不标记已执行）
                                if not s.get("defer_until"):
                                    s["defer_until"] = self._defer_until(now)
                                    changed = True
                                continue
                            s.pop("defer_until", None)
                            changed = True
                            self._dispatch_schedule(s, action)
                    if changed:
                        self._save_schedules(schedules)
            except Exception:
                logging.exception("定时任务检查异常")
            time.sleep(30)

    # ===== 系统托盘 + 开机自启 =====
    def _start_tray(self):
        """启动系统托盘图标（pystray 可选依赖，失败静默降级）。

        托盘菜单回调运行在 pystray 的线程中，禁止直接碰 Tk——
        全部经 _ui_queue 投递到主线程处理。
        """
        if not TRAY_AVAILABLE or getattr(self, "_tray_icon", None) is not None:
            return
        try:
            img = None
            try:
                img = _TrayImage.open(os.path.join(BASE_DIR, "app.ico"))
            except Exception:
                img = _TrayImage.new("RGBA", (64, 64), (52, 120, 246, 255))
            menu = pystray.Menu(
                pystray.MenuItem("🪟 显示主窗口", lambda: self._tray_cmd("show")),
                pystray.MenuItem("🙈 隐藏窗口", lambda: self._tray_cmd("hide")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出鲸语", lambda: self._tray_cmd("quit")),
            )
            icon = pystray.Icon("WhaleTalk", img, "鲸语 WhaleTalk", menu)
            self._tray_icon = icon
            self._tray_thread = threading.Thread(target=icon.run, daemon=True)
            self._tray_thread.start()
            logging.info("系统托盘已启动（pystray）")
            self.after(600, lambda: self._flash_status("🪟 已驻留系统托盘（右键托盘图标：显示/隐藏/退出）", 4000))
        except Exception:
            logging.warning("系统托盘启动失败（不影响使用）", exc_info=True)
            self._tray_icon = None
            self._tray_thread = None

    def _tray_alive(self):
        """托盘是否可用：图标存在且运行线程存活（线程崩溃后不再拦截关闭，防窗口锁死）。"""
        if getattr(self, "_tray_icon", None) is None:
            return False
        th = getattr(self, "_tray_thread", None)
        if th is not None:
            return th.is_alive()
        return False

    def _tray_cmd(self, cmd):
        """托盘菜单动作（pystray 线程）→ 主线程队列。"""
        try:
            self._ui_queue.put(("tray", cmd))
        except Exception:
            pass

    def _stop_tray(self):
        icon = getattr(self, "_tray_icon", None)
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
            self._tray_icon = None
            self._tray_thread = None

    @staticmethod
    def _autostart_command():
        """开机自启命令：打包 exe 用 exe 本体；源码运行用 pythonw + main.py。"""
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        pyw = os.path.join(BASE_DIR, ".venv", "Scripts", "pythonw.exe")
        if not os.path.exists(pyw):
            alt = os.path.join(os.path.dirname(sys.executable) or ".", "pythonw.exe")
            pyw = alt if os.path.exists(alt) else "pythonw.exe"
        return f'"{pyw}" "{os.path.join(BASE_DIR, "main.py")}"'

    @staticmethod
    def _autostart_enabled():
        """读取 HKCU Run 键，判断是否已注册开机自启。"""
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
            ) as k:
                val, _ = winreg.QueryValueEx(k, "WhaleTalk")
            return bool(val)
        except FileNotFoundError:
            return False
        except Exception:
            return False

    @staticmethod
    def _set_autostart(enabled):
        """注册/取消开机自启（HKCU Run 键）。返回是否成功。"""
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            )
            try:
                if enabled:
                    winreg.SetValueEx(
                        key, "WhaleTalk", 0, winreg.REG_SZ, AssistantApp._autostart_command()
                    )
                else:
                    try:
                        winreg.DeleteValue(key, "WhaleTalk")
                    except FileNotFoundError:
                        pass
            finally:
                winreg.CloseKey(key)
            return True
        except Exception:
            logging.exception("设置开机自启失败")
            return False

    def _start_inbound_server(self):
        """Webhook 接收端：本地监听 HTTP POST（token 鉴权），外部可远程下达任务。

        config.json：inbound_port（端口，0=关闭）+ inbound_token（鉴权 token）。
        请求体 JSON：{"token": "<token>", "text": "要执行的任务指令"}。
        """
        port = int(self.cfg.get("inbound_port", 0) or 0)
        token = str(self.cfg.get("inbound_token", "") or "")
        if port <= 0 or not token:
            return
        try:
            from http.server import BaseHTTPRequestHandler, HTTPServer

            app = self

            class _Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    try:
                        length = int(self.headers.get("Content-Length", 0) or 0)
                        if length > 1_000_000:  # 请求体上限：防本地恶意大包占内存
                            self.send_response(413)
                            self.end_headers()
                            return
                        body = self.rfile.read(length).decode("utf-8", errors="replace")
                        try:
                            data = json.loads(body)
                        except Exception:
                            data = {}
                        import hmac

                        if (
                            not hmac.compare_digest(
                                str(data.get("token") or ""), token
                            )
                            or not str(data.get("text") or "").strip()
                        ):
                            self.send_response(403)
                            self.end_headers()
                            self.wfile.write(b"forbidden")
                            return
                        app._ui_queue.put(("timer_task", str(data["text"]).strip()))
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b"ok")
                    except Exception:
                        try:
                            self.send_response(500)
                            self.end_headers()
                        except Exception:
                            pass

                def log_message(self, *a):
                    pass

            self._inbound_server = HTTPServer(("127.0.0.1", port), _Handler)
            threading.Thread(target=self._inbound_server.serve_forever, daemon=True).start()
            logging.info("Webhook 接收端已启动：http://127.0.0.1:%s（token 鉴权）", port)
        except Exception:
            logging.exception("Webhook 接收端启动失败")

    def _checkpoint_prompt_text(self):
        """未完成任务提示：存在检查点且未完成时注入任务上下文（断点续跑）。"""
        try:
            if _dc.CHECKPOINT_FILE and os.path.exists(_dc.CHECKPOINT_FILE):
                with open(_dc.CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and str(data.get("status") or "") not in ("已完成", "done", "完成"):
                    pend = "；".join(str(p) for p in (data.get("pending") or [])[:5])
                    return (
                        f"[未完成任务] 存在上次未完成的任务「{data.get('name', '')}」"
                        f"（状态：{data.get('status', '')}），待办：{pend or '无'}。"
                        "若用户要求继续，先调用 task_checkpoint_load 恢复上下文。"
                    )
        except Exception:
            pass
        return ""

    def _dispatch_schedule(self, s, action):
        """按动作类型分发定时任务（scheduler 线程调用，UI 操作走队列）。"""
        text = str(s.get("text") or "").strip()
        try:
            if action == "backup":
                if self.cfg.get("privacy_mode"):
                    return  # 隐私模式：不生成含数据的备份包
                def do_backup():
                    try:
                        import backup
                        bpath = backup.make_backup()
                        self._ui_queue.put(("info", f"[定时备份] 已完成：{os.path.basename(bpath)}"))
                    except Exception:
                        logging.exception("定时备份失败")

                threading.Thread(target=do_backup, daemon=True).start()
            elif action == "notify":
                if text:
                    self._ui_queue.put(("schedule_notify", text))
            elif action == "workflow":
                # 到点自动运行流程（run_workflow 内部异步执行，不阻塞 scheduler 线程）
                if text:
                    try:
                        _dc.run_workflow(text)
                    except Exception:
                        logging.exception("定时流程执行失败")
            else:
                if text:
                    self._ui_queue.put(("timer_task", text))
        except Exception:
            logging.exception("定时任务分发失败")

    def _load_schedules(self):
        try:
            if os.path.exists(SCHEDULES_PATH):
                with open(SCHEDULES_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            logging.exception("读取定时任务失败")
        return []

    def _save_schedules(self, schedules):
        return self._atomic_json_write(SCHEDULES_PATH, schedules, indent=2)

    def import_session_file(self):
        """导入会话（JSON 数组 / JSONL / 本程序导出的 JSON），创建为新会话。"""
        path = filedialog.askopenfilename(
            parent=self.root,
            initialdir=HISTORY_DIR,
            title="导入会话",
            filetypes=[("会话文件", "*.json *.jsonl"), ("JSON", "*.json"), ("JSONL", "*.jsonl")],
        )
        if not path:
            return
        msgs = self._load_session_file(path)
        if not msgs:
            return
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        session = self._add_session()
        session["messages"] = msgs
        session["first_user"] = next(
            (mm.get("content") for mm in msgs if mm.get("role") == "user"), None
        )
        if session["first_user"] and not session.get("name"):
            session["name"] = " ".join(session["first_user"].split())[:18]
        self._invalidate_session_name(session)
        self._show_session_text(session)
        self.rebuild_view_from_messages()
        self._ctx_counts = None
        self._snapshot_dirty = True
        self._refresh_session_list()
        self.update_status()
        self._update_context_bar()
        self._maybe_save_snapshot()
        note = f"[导入] 已导入会话（{len(msgs)} 条消息）。\n"
        self._append(note, "time")
        self.blocks.append(("note", note))
        self._append("\n")
        self.blocks.append(("plain", "\n"))
        self._flash_status(f"已导入 {len(msgs)} 条消息")

    @staticmethod
    def _load_session_file(path):
        """解析会话文件（JSON 数组 / 含 messages 的对象 / JSONL 逐行消息）。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            if str(path).lower().endswith(".jsonl"):
                msgs = [json.loads(line) for line in raw.splitlines() if line.strip()]
            else:
                data = json.loads(raw)
                if isinstance(data, list):
                    msgs = data
                elif isinstance(data, dict) and isinstance(data.get("messages"), list):
                    msgs = data["messages"]
                else:
                    msgs = None
            if not msgs or not isinstance(msgs, list):
                return None
            cleaned = []
            for m in msgs[:2000]:
                if not isinstance(m, dict) or not m.get("role") in ("user", "assistant", "system", "tool"):
                    continue
                cleaned.append(
                    {
                        "role": str(m["role"]),
                        "content": str(m.get("content") or ""),
                        **({"reasoning_content": str(m["reasoning_content"])} if m.get("reasoning_content") else {}),
                    }
                )
            if not cleaned:
                return None
            if cleaned[0].get("role") != "system":
                cleaned.insert(0, {"role": "system", "content": ""})
            return cleaned
        except Exception as e:
            logging.exception("导入会话失败")
            messagebox.showerror("导入失败", f"无法解析会话文件：{e}")
            return None

    def manage_schedules(self):
        t = self._theme()
        with self._schedules_lock:
            schedules = self._load_schedules()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("定时任务（cron 周期调度）")
        dialog.geometry("620x500")
        dialog.transient(self.root)
        body = tk.Frame(dialog, bg=t["panel"])
        body.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        self._restyle.append((body, "panel"))
        self._lbl(
            body, "三种调度：每日 HH:MM 一次 / cron 表达式（分 时 日 月 周）/ 每 N 分钟；"
            "动作：发送指令 / 运行流程 / 项目备份 / 状态提醒；「错峰」= 高峰时段自动顺延到空闲时段执行（省一半费用）",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9), wraplength=560, justify="left",
        ).pack(anchor="w", pady=(0, 6))
        listbox = tk.Listbox(
            body, height=9, bg=t["input_bg"], fg=t["input_fg"],
            selectbackground=t["selection"], selectforeground=t["accent_text"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], exportselection=False,
        )
        listbox.pack(fill="both", expand=True)

        def sched_label(s):
            tag = "✓" if s.get("enabled") else "✗"
            act = {"message": "发指令", "backup": "备份", "notify": "提醒", "workflow": "流程"}.get(
                str(s.get("action") or "message"), "发指令"
            )
            peak = " 🌙错峰" if s.get("off_peak") else ""
            if s.get("cron"):
                when = f"cron:{s['cron']}"
            elif s.get("every"):
                when = f"每{s['every']}分钟"
            else:
                when = s.get("time", "")
            name = str(s.get("name") or "").strip()
            shown = f"{name}：{s.get('text', '')}" if name else str(s.get("text", ""))
            return f"{tag} [{act}{peak}] {when}  {shown}"

        def refresh():
            listbox.delete(0, "end")
            for s in schedules:
                listbox.insert("end", sched_label(s))

        refresh()
        row = tk.Frame(body, bg=t["panel"])
        row.pack(fill="x", pady=(8, 0))
        self._restyle.append((row, "panel"))
        mode_var = tk.StringVar(value="time")
        ttk.Combobox(
            row, textvariable=mode_var, values=["time", "cron", "every"],
            state="readonly", width=6,
        ).pack(side="left")
        value_var = tk.StringVar(value="09:00")
        ttk.Entry(row, textvariable=value_var, width=12).pack(side="left", padx=(4, 0))
        action_var = tk.StringVar(value="message")
        ttk.Combobox(
            row, textvariable=action_var, values=["message", "workflow", "backup", "notify"],
            state="readonly", width=7,
        ).pack(side="left", padx=(6, 0))
        text_var = tk.StringVar()
        ttk.Entry(row, textvariable=text_var).pack(side="left", fill="x", expand=True, padx=(6, 0))
        en_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="启用", variable=en_var).pack(side="left", padx=(6, 0))
        off_peak_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="错峰", variable=off_peak_var).pack(side="left", padx=(2, 0))

        def add_schedule():
            mode = mode_var.get()
            val = value_var.get().strip()
            txt = text_var.get().strip()
            if not val:
                return
            s = {"enabled": bool(en_var.get()), "action": action_var.get(), "last": "", "last_run": 0}
            if off_peak_var.get():
                s["off_peak"] = True
            if mode == "cron":
                fields = val.split()
                if len(fields) != 5:
                    messagebox.showinfo("提示", "cron 表达式需为 5 个字段：分 时 日 月 周（如 30 9 * * 1）")
                    return
                if not all(cron_field_ok(f, i) for i, f in enumerate(fields)):
                    messagebox.showinfo(
                        "提示",
                        "cron 字段非法（值域：分 0-59，时 0-23，日 1-31，月 1-12，周 1-7；仅支持数字、*、,、-、/）",
                    )
                    return
                s["cron"] = val
            elif mode == "every":
                try:
                    n = int(val)
                except ValueError:
                    n = 0
                if n <= 0:
                    messagebox.showinfo("提示", "间隔必须为正整数（分钟）。")
                    return
                s["every"] = n
            else:
                if ":" not in val:
                    messagebox.showinfo("提示", "时刻格式应为 HH:MM。")
                    return
                s["time"] = val
            if s.get("action") == "workflow":
                # 流程动作：内容为流程名（校验存在性，提示可用的流程）
                flows = {}
                try:
                    if os.path.exists(_dc.WORKFLOWS_FILE):
                        with open(_dc.WORKFLOWS_FILE, "r", encoding="utf-8") as f:
                            flows = json.load(f)
                except Exception:
                    flows = {}
                if txt not in flows:
                    messagebox.showinfo(
                        "提示",
                        f"流程「{txt}」不存在。可用流程：{list(flows) or '（无，请先在 自动化 → 流程管理 创建）'}",
                    )
                    return
            elif s.get("action") != "backup" and not txt:
                messagebox.showinfo("提示", "发送指令/提醒需要填写内容。")
                return
            if txt:
                s["text"] = txt
            schedules.append(s)
            refresh()

        def del_schedule():
            sel = listbox.curselection()
            if not sel:
                return
            schedules.pop(sel[0])
            refresh()

        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=16, pady=(12, 14))
        self._restyle.append((bar, "panel"))

        def save_schedules():
            with self._schedules_lock:
                self._save_schedules(schedules)
            self._flash_status("定时任务已保存")
            dialog.destroy()

        self._mk_button(bar, "添加", add_schedule, fsz=9).pack(side="left")
        self._mk_button(bar, "删除选中", del_schedule, fsz=9).pack(side="left", padx=(8, 0))
        self._mk_button(bar, "保存", save_schedules, kind="primary", fsz=9).pack(side="right")
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right", padx=(0, 8))

    def show_external_config(self):
        """外部服务配置：Webhook / 数据库 / 邮件（SMTP+IMAP）/ 接收端 / 图片生成。"""
        t = self._theme()
        dialog, body, footer = self._dialog_shell(
            "外部服务配置", 620, 540,
            subtitle="Webhook 推送、数据库连接、邮件收发、远程接收端与图片生成的统一配置",
            minsize=(480, 420),
        )
        nb = ttk.Notebook(body)
        nb.pack(fill="both", expand=True)

        # ---- Webhook 页 ----
        wh_frame = tk.Frame(nb, bg=t["panel"])
        nb.add(wh_frame, text="Webhook 推送")
        wh = {}
        try:
            wh_data = _dc._load_webhooks() if hasattr(_dc, "_load_webhooks") else {}
        except Exception:
            wh_data = {}
        hints = {
            "dingtalk": "钉钉机器人：https://oapi.dingtalk.com/robot/send?access_token=xxx",
            "serverchan": "Server酱：https://sctapi.ftqq.com/KEY.send",
            "slack": "Slack Incoming Webhook URL",
            "generic": "通用 Webhook（POST JSON {title, text}）",
        }
        for name, hint in hints.items():
            self._lbl(wh_frame, f"{name}：{hint}", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(6, 0))
            var = tk.StringVar(value=str(wh_data.get(name) or ""))
            ttk.Entry(wh_frame, textvariable=var).pack(fill="x", pady=(2, 0))
            wh[name] = var

        # ---- 数据库页 ----
        db_frame = tk.Frame(nb, bg=t["panel"])
        nb.add(db_frame, text="数据库连接")
        try:
            db_data = json.load(open(_dc.DB_CONFIG_FILE, encoding="utf-8")) if os.path.exists(_dc.DB_CONFIG_FILE) else {}
        except Exception:
            db_data = {}
        dbs = {}
        for kind, label, default_port in (("mysql", "MySQL", "3306"), ("postgres", "PostgreSQL", "5432")):
            self._lbl(db_frame, f"── {label} ──", role="label_sec", bg="panel", font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(8, 0))
            sub = tk.Frame(db_frame, bg=t["panel"])
            sub.pack(fill="x", pady=(2, 0))
            self._restyle.append((sub, "panel"))
            conn_var = tk.StringVar(value="default")
            ttk.Entry(sub, textvariable=conn_var, width=10).pack(side="left")
            fields = {}
            for key, ph in (("host", "主机"), ("port", "端口"), ("user", "用户"), ("password", "密码"), ("database", "库名")):
                var = tk.StringVar(value=str((db_data.get(kind) or {}).get("default", {}).get(key, "") if key != "port" else str((db_data.get(kind) or {}).get("default", {}).get("port", default_port))))
                f = ttk.Entry(sub, textvariable=var, width=14, show="*" if key == "password" else None)
                f.pack(side="left", padx=(2, 0))
                fields[key] = var
            dbs[kind] = (conn_var, fields)

        # ---- 邮件页（SMTP 发送 + IMAP 收取）----
        mail_frame = tk.Frame(nb, bg=t["panel"])
        nb.add(mail_frame, text="邮件收发")
        try:
            mail_data = json.load(open(_dc.EMAIL_CONFIG_FILE, encoding="utf-8")) if os.path.exists(_dc.EMAIL_CONFIG_FILE) else {}
        except Exception:
            mail_data = {}
        if not isinstance(mail_data, dict):
            mail_data = {}
        mail_vars = {}
        self._lbl(mail_frame, "── 发送（SMTP，send_email 使用）──", role="label_sec", bg="panel", font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(6, 0))
        for key, ph in (("smtp_host", "SMTP 服务器"), ("smtp_port", "端口(465/587)"), ("user", "邮箱账号"), ("password", "密码/授权码"), ("from", "发件人(默认=账号)")):
            row = tk.Frame(mail_frame, bg=t["panel"])
            row.pack(fill="x", pady=2)
            self._restyle.append((row, "panel"))
            self._lbl(row, ph + "：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="left")
            var = tk.StringVar(value=str(mail_data.get(key, "") or ""))
            ttk.Entry(row, textvariable=var, show="*" if "password" in key else None).pack(side="left", fill="x", expand=True)
            mail_vars[key] = var
        self._lbl(mail_frame, "── 收取（IMAP，read_email 使用）──", role="label_sec", bg="panel", font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(8, 0))
        imap_cfg = mail_data.get("imap") if isinstance(mail_data.get("imap"), dict) else {}
        imap_vars = {}
        for key, ph in (("host", "IMAP 服务器"), ("port", "端口(993)"), ("user", "账号"), ("password", "密码/授权码"), ("ssl", "SSL(1/0)")):
            row = tk.Frame(mail_frame, bg=t["panel"])
            row.pack(fill="x", pady=2)
            self._restyle.append((row, "panel"))
            self._lbl(row, ph + "：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="left")
            var = tk.StringVar(value=str(imap_cfg.get(key, mail_data.get("imap_" + key, "") if key != "ssl" else "1")))
            ttk.Entry(row, textvariable=var, show="*" if "password" in key else None).pack(side="left", fill="x", expand=True)
            imap_vars[key] = var

        # ---- 接收端页（Webhook 远程下达任务）----
        in_frame = tk.Frame(nb, bg=t["panel"])
        nb.add(in_frame, text="远程接收端")
        self._lbl(in_frame, "本地监听 HTTP POST，外部（手机/脚本）可远程下达任务：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(4, 0))
        self._lbl(in_frame, 'POST http://127.0.0.1:<端口>  body: {"token": "…", "text": "任务指令"}', role="label_sec", bg="panel", font=(MONO_FAMILY, 8)).pack(anchor="w")
        row = tk.Frame(in_frame, bg=t["panel"])
        row.pack(fill="x", pady=(8, 2))
        self._restyle.append((row, "panel"))
        self._lbl(row, "端口（0=关闭）：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="left")
        port_var = tk.StringVar(value=str(int(self.cfg.get("inbound_port", 0) or 0)))
        ttk.Entry(row, textvariable=port_var, width=8).pack(side="left")
        self._lbl(row, "  鉴权 token：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="left")
        tok_var = tk.StringVar(value=str(self.cfg.get("inbound_token", "") or ""))
        ttk.Entry(row, textvariable=tok_var, width=24).pack(side="left", fill="x", expand=True)
        self._lbl(in_frame, "提示：端口与 token 修改后需重启程序生效。", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(4, 0))

        # ---- 图片生成页 ----
        img_frame = tk.Frame(nb, bg=t["panel"])
        nb.add(img_frame, text="图片生成")
        self._lbl(img_frame, "image_generate 使用 OpenAI 兼容 images API：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(4, 0))
        img_vars = {}
        for key, ph, hint in (
            ("image_base_url", "端点（默认=当前 base_url）", "如 https://api.openai.com/v1"),
            ("image_api_key", "API Key（默认=当前 API Key）", ""),
            ("image_model", "模型", "如 gpt-image-1 / dall-e-3 / 其它兼容模型"),
        ):
            self._lbl(img_frame, f"{ph}：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(6, 0))
            var = tk.StringVar(value=str(self.cfg.get(key, "") or ""))
            ttk.Entry(img_frame, textvariable=var, show="*" if "key" in key else None).pack(fill="x", pady=(2, 0))
            if hint:
                self._lbl(img_frame, hint, role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w")
            img_vars[key] = var

        def save():
            try:
                wh_out = {}
                for name, var in wh.items():
                    url = var.get().strip()
                    if url:
                        wh_out[name] = url
                self._atomic_json_write(_dc.WEBHOOK_CONFIG_FILE, wh_out)
                db_out = {}
                for kind, (conn_var, fields) in dbs.items():
                    conns = {}
                    cfg = {k: v.get().strip() for k, v in fields.items()}
                    if cfg.get("host") and cfg.get("user"):
                        conns[conn_var.get().strip() or "default"] = cfg
                    if conns:
                        db_out[kind] = conns
                self._atomic_json_write(_dc.DB_CONFIG_FILE, db_out)
                mail_out = {}
                for key, var in mail_vars.items():
                    v = var.get().strip()
                    if v:
                        mail_out[key] = v
                imap_out = {}
                for key, var in imap_vars.items():
                    v = var.get().strip()
                    if v:
                        imap_out[key] = v
                if imap_out:
                    mail_out["imap"] = imap_out
                self._atomic_json_write(_dc.EMAIL_CONFIG_FILE, mail_out)
                try:
                    self.cfg["inbound_port"] = max(0, min(65535, int(port_var.get() or 0)))
                except (TypeError, ValueError):
                    self.cfg["inbound_port"] = 0
                self.cfg["inbound_token"] = tok_var.get().strip()
                self.cfg["image_api_key"] = img_vars["image_api_key"].get().strip()
                self.cfg["image_base_url"] = img_vars["image_base_url"].get().strip()
                self.cfg["image_model"] = img_vars["image_model"].get().strip() or "gpt-image-1"
                save_config(self.cfg)
                self._capture_client_params()
                self._flash_status("外部服务配置已保存（接收端端口需重启生效）")
                dialog.destroy()
            except Exception as e:
                messagebox.showerror("保存失败", str(e))

        self._footer_hint(footer, "Webhook/数据库/邮件供工具使用；接收端与图片生成保存到 config.json")
        self._footer_btn(footer, "关闭", dialog.destroy)
        self._footer_btn(footer, "保存", save, primary=True)

    def manage_workflows(self):
        """流程管理：创建/查看/删除 run_workflow 使用的流程模板（workflows.json）。"""
        t = self._theme()
        try:
            wf = json.load(open(_dc.WORKFLOWS_FILE, encoding="utf-8")) if os.path.exists(_dc.WORKFLOWS_FILE) else {}
        except Exception:
            wf = {}
        if not isinstance(wf, dict):
            wf = {}
        dialog, body, footer = self._dialog_shell(
            "流程管理（AI 自动化）", 600, 480,
            subtitle="流程 = 依次自动执行的指令序列，可被 run_workflow 一键触发或接入定时任务",
            minsize=(460, 360),
        )
        left = tk.Frame(body, bg=t["panel"])
        left.pack(side="left", fill="y")
        self._restyle.append((left, "panel"))
        listbox = tk.Listbox(
            left, width=22, bg=t["input_bg"], fg=t["input_fg"],
            selectbackground=t["selection"], selectforeground=t["accent_text"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], exportselection=False,
        )
        listbox.pack(fill="both", expand=True)
        for name in wf:
            listbox.insert("end", name)

        right = tk.Frame(body, bg=t["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self._restyle.append((right, "panel"))
        self._lbl(right, "流程名称：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w")
        name_var = tk.StringVar()
        name_entry = ttk.Entry(right, textvariable=name_var)
        name_entry.pack(fill="x", pady=(0, 6))
        self._lbl(right, "步骤（每行一条指令，按顺序执行）：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w")
        steps_text = tk.Text(
            right, height=9, bg=t["input_bg"], fg=t["input_fg"], relief="flat",
            highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], font=(FONT_FAMILY, 9), padx=6, pady=4,
        )
        steps_text.pack(fill="both", expand=True)

        def show_item():
            sel = listbox.curselection()
            if not sel or sel[0] >= len(list(wf)):
                return
            name = list(wf)[sel[0]]
            name_var.set(name)
            steps_text.delete("1.0", "end")
            for st in wf[name].get("steps") or []:
                text = str(st.get("text") if isinstance(st, dict) else st or "").strip()
                if text:
                    steps_text.insert("end", text + "\n")

        def save_flow():
            name = name_var.get().strip()
            if not name:
                messagebox.showinfo("提示", "流程名称不能为空")
                return
            steps = [ln.strip() for ln in steps_text.get("1.0", "end").splitlines() if ln.strip()]
            if not steps:
                messagebox.showinfo("提示", "至少需要一个步骤")
                return
            wf[name] = {"steps": [{"text": s} for s in steps]}
            if not self._atomic_json_write(_dc.WORKFLOWS_FILE, wf):
                messagebox.showerror("保存失败", f"写入失败：{_dc.WORKFLOWS_FILE}")
                return
            if listbox.get(0, "end").count(name) == 0:
                listbox.insert("end", name)
            self._flash_status(f"流程「{name}」已保存（{len(steps)} 步）")

        def delete_flow():
            sel = listbox.curselection()
            if not sel or sel[0] >= len(list(wf)):
                return
            name = list(wf)[sel[0]]
            if not messagebox.askyesno("删除流程", f"确认删除流程「{name}」？"):
                return
            wf.pop(name, None)
            if not self._atomic_json_write(_dc.WORKFLOWS_FILE, wf):
                messagebox.showerror("删除失败", f"写入失败：{_dc.WORKFLOWS_FILE}")
                return
            listbox.delete(sel[0])
            name_var.set("")
            steps_text.delete("1.0", "end")

        def run_flow():
            sel = listbox.curselection()
            if not sel or sel[0] >= len(list(wf)):
                return
            name = list(wf)[sel[0]]
            steps = wf[name].get("steps") or []
            if not messagebox.askyesno(
                "运行流程",
                f"确认立即运行流程「{name}」？（{len(steps)} 步，将按顺序自动执行）",
            ):
                return
            dialog.destroy()
            self._flash_status(f"🚀 正在运行流程「{name}」…")
            try:
                _dc.run_workflow(name)
            except Exception as e:
                self._flash_status(f"流程启动失败：{e}")

        listbox.bind("<<ListboxSelect>>", lambda e: show_item())
        bar = tk.Frame(body, bg=t["panel"])
        bar.pack(fill="x", pady=(10, 0))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "运行选中", run_flow, fsz=9).pack(side="left")
        self._mk_button(bar, "删除流程", delete_flow, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "保存流程", save_flow, kind="primary", fsz=9).pack(side="right")

        self._footer_hint(footer, "保存后 AI 可用 run_workflow 一键执行；也可配合 schedule_task 定时触发")
        self._footer_btn(footer, "关闭", dialog.destroy)

    def manage_knowledge(self):
        """知识库管理：索引状态查看 / 一键重建 / 测试检索 / 清除索引。"""
        t = self._theme()
        dialog, body, footer = self._dialog_shell(
            "知识库管理（语义检索）", 560, 420,
            subtitle="对工作区文本建索引后，AI 可用 knowledge_search 语义检索（措辞不同也能命中）",
            minsize=(440, 320),
        )
        status = self._lbl(body, "加载中…", role="label_sec", bg="panel", font=(FONT_FAMILY, 9))
        status.pack(anchor="w", pady=(0, 8))

        def refresh_status():
            try:
                if _dc.KNOWLEDGE_INDEX_FILE and os.path.exists(_dc.KNOWLEDGE_INDEX_FILE):
                    with open(_dc.KNOWLEDGE_INDEX_FILE, "r", encoding="utf-8") as f:
                        idx = json.load(f)
                    n = idx.get("count", 0)
                    root = idx.get("root", "")
                    mtime = datetime.fromtimestamp(os.path.getmtime(_dc.KNOWLEDGE_INDEX_FILE)).strftime("%Y-%m-%d %H:%M")
                    status.configure(
                        text=f"✅ 已索引 {n} 个文档（{root}）\n索引时间：{mtime}",
                        fg=t["success"],
                    )
                else:
                    status.configure(
                        text="⛔ 尚未建立索引（点击「重建索引」对工作区文本建索引）",
                        fg=t["warning"],
                    )
            except Exception:
                status.configure(text="索引文件读取失败", fg=t["error"])

        refresh_status()
        self._lbl(body, "测试检索（输入语义描述，如「预算相关文档」）：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w")
        row = tk.Frame(body, bg=t["panel"])
        row.pack(fill="x", pady=(4, 8))
        self._restyle.append((row, "panel"))
        q_var = tk.StringVar()
        ttk.Entry(row, textvariable=q_var).pack(side="left", fill="x", expand=True)
        result = tk.Text(
            body, height=8, bg=t["input_bg"], fg=t["input_fg"], relief="flat",
            highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], font=(MONO_FAMILY, 9), padx=6, pady=4,
        )
        result.pack(fill="both", expand=True)

        def do_search():
            q = q_var.get().strip()
            if not q:
                return
            result.delete("1.0", "end")
            result.insert("1.0", "检索中…")
            try:
                out = _dc.knowledge_search(q)
            except Exception as e:
                out = f"错误：{e}"
            result.delete("1.0", "end")
            result.insert("1.0", out)

        def rebuild():
            status.configure(text="正在建索引…（后台执行，大目录需稍等）", fg=t["warning"])
            result.delete("1.0", "end")
            result.insert("1.0", "正在重建索引…")
            self._kb_status_lbl = status
            self._kb_result_txt = result

            def worker():
                try:
                    out = _dc.knowledge_index("", force=True)
                except Exception as e:
                    out = f"错误：{e}"
                self._ui_queue.put(("knowledge_done", out))

            threading.Thread(target=worker, daemon=True).start()

        def clear_index():
            if not messagebox.askyesno("清除索引", "确认删除知识库索引？删除后需重新建索引。"):
                return
            try:
                if _dc.KNOWLEDGE_INDEX_FILE and os.path.exists(_dc.KNOWLEDGE_INDEX_FILE):
                    os.remove(_dc.KNOWLEDGE_INDEX_FILE)
            except Exception:
                pass
            refresh_status()
            result.delete("1.0", "end")

        bar = tk.Frame(body, bg=t["panel"])
        bar.pack(fill="x", pady=(8, 0))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "测试检索", do_search, fsz=9).pack(side="left")
        self._mk_button(bar, "重建索引", rebuild, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "清除索引", clear_index, fsz=9).pack(side="left", padx=(6, 0))
        self._footer_btn(footer, "关闭", dialog.destroy)

    def show_checkpoint(self):
        """任务检查点：查看未完成任务进度 / 清除。"""
        t = self._theme()
        dialog, body, footer = self._dialog_shell(
            "任务检查点（断点续跑）", 560, 360,
            subtitle="长任务进度持久化：崩溃/重启后可从断点继续",
            minsize=(440, 260),
        )
        viewer = tk.Text(
            body, height=10, bg=t["input_bg"], fg=t["input_fg"], relief="flat",
            highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], font=(MONO_FAMILY, 9), padx=8, pady=6,
        )
        viewer.pack(fill="both", expand=True)
        try:
            out = _dc.task_checkpoint_load()
        except Exception:
            out = "错误：读取检查点失败"
        viewer.insert("1.0", out)

        def clear_cp():
            if not messagebox.askyesno("清除检查点", "确认清除任务检查点？"):
                return
            try:
                if _dc.CHECKPOINT_FILE and os.path.exists(_dc.CHECKPOINT_FILE):
                    os.remove(_dc.CHECKPOINT_FILE)
                viewer.delete("1.0", "end")
                viewer.insert("1.0", "检查点已清除")
                self._flash_status("任务检查点已清除")
            except Exception as e:
                messagebox.showerror("清除失败", str(e))

        self._footer_btn(footer, "关闭", dialog.destroy)
        self._footer_btn(footer, "清除检查点", clear_cp)

    def _on_knowledge_done(self, out):
        """知识库重建完成（主线程）：更新管理面板状态与结果。"""
        st = getattr(self, "_kb_status_lbl", None)
        rt = getattr(self, "_kb_result_txt", None)
        t = self._theme()
        try:
            if st is not None and st.winfo_exists():
                ok = str(out).startswith("已索引")
                st.configure(
                    text="✅ " + out if ok else out,
                    fg=t["success"] if ok else t["error"],
                )
        except tk.TclError:
            pass
        try:
            if rt is not None and rt.winfo_exists():
                rt.delete("1.0", "end")
                rt.insert("1.0", str(out))
        except tk.TclError:
            pass

    def _ocr_clipboard(self):
        """从剪贴板图片提取文字（Windows.Media.Ocr，后台线程）。"""
        self._flash_status("正在识别剪贴板图片文字…")

        def worker():
            text = ""
            img_path = None
            ps_path = None
            try:
                try:
                    from PIL import ImageGrab
                except ImportError:
                    text = "OCR 需要 Pillow：pip install pillow"
                else:
                    img = ImageGrab.grabclipboard()
                    if img is None:
                        text = "剪贴板中没有图片"
                    else:
                        img_path = os.path.join(DATA_DIR, "_ocr_tmp.png")
                        img.save(img_path)
                        ps_path = os.path.join(DATA_DIR, "_ocr.ps1")
                        script = OCR_IMAGE_PS.replace(
                            "@PATH@", "'" + img_path.replace("'", "''") + "'"
                        )
                        with open(ps_path, "w", encoding="utf-8-sig") as f:
                            f.write(script)
                        proc = subprocess.run(
                            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_path],
                            capture_output=True, text=True, timeout=60,
                            encoding="utf-8", errors="replace",
                        )
                        text = (proc.stdout or "").strip() or "未能识别出文字"
            except Exception:
                logging.exception("OCR 识别失败")
                text = "OCR 识别失败"
            finally:
                for p in (img_path, ps_path):
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
            self._ui_queue.put(("ocr", text))

        threading.Thread(target=worker, daemon=True).start()

    def _ocr_result(self, text):
        """OCR 识别结果：插入输入框（替代原走语音队列的历史 bug 路径）。"""
        if text:
            self._clear_placeholder()
            self.input_text.insert("insert", str(text).strip() + " ")
            self.input_text.focus_set()
            self._flash_status("OCR 识别完成，可编辑后发送")
        else:
            self._flash_status("OCR 未能识别出文字")

    def _valid_tool_names(self):
        try:
            return {t["function"]["name"] for t in TOOLS} | {
                t["function"]["name"] for t in load_user_tools()
            }
        except Exception:
            return {t["function"]["name"] for t in TOOLS}

    def _run_playground(self, name):
        prompt = PLAYGROUND_TASKS.get(name)
        if not prompt:
            return
        if not self.cfg.get("api_key"):
            messagebox.showwarning("未配置 API Key", "请先填写 DeepSeek API Key 再体验。")
            return
        if messagebox.askyesno("试玩任务", f"将执行「{name}」。\n需要对应权限（写文件等）已开启，"
                               "可在 🛠 工具中心 → 权限 查看。\n开始？"):
            self.send(text=prompt)

    def _mode_tools_for_request(self, cfg):
        """自主模式 = 任务能力：按当前模式解析本次请求的工具集（不污染配置）。

        完全智能 → 全部工具（为开发/创作而生）；纯对话 → 无工具；标准 → 按工具中心配置。
        返回 (enabled_tools, tools_enabled)。
        """
        if cfg.get("full_auto"):
            names = [t["function"]["name"] for t in TOOLS] + [
                t["function"]["name"] for t in load_user_tools(USER_TOOLS_PATH)
            ]
            return names, True
        if cfg.get("pure_chat"):
            return [], False
        return list(cfg.get("enabled_tools") or []), bool(cfg.get("tools_enabled", True))

    @staticmethod
    def _dependency_status():
        """可选依赖检测：返回 [(显示名, 已装, 影响功能, 安装命令)]（打包 exe 排查利器）。"""
        import importlib.util

        rows = []
        for mod, name, use, hint in OPTIONAL_DEPS:
            try:
                ok = importlib.util.find_spec(mod) is not None
            except (ImportError, ValueError):
                ok = False
            rows.append((name, ok, use, hint))
        return rows

    def _refresh_user_tools_cache(self):
        """自定义工具缓存失效（插件安装/卸载/停用后调用，mtime 兜底外的主动清除）。"""
        try:
            _USER_TOOLS_CACHE.clear()
        except Exception:
            pass

    def _plugin_paths(self):
        """插件应用/卸载所需的数据文件路径（供 plugins 模块使用）。"""
        return {
            "plugins_dir": PLUGINS_DIR,
            "user_tools": USER_TOOLS_PATH,
            "prompts": PROMPTS_PATH,
            "workflows": _dc.WORKFLOWS_FILE,
        }

    def _plugin_summary(self, plugin):
        """插件能力摘要（用于列表/导入预览）。"""
        c = plugin.get("contents") or {}
        parts = []
        if c.get("tools"):
            parts.append(f"{len(c['tools'])} 个工具")
        if c.get("skills"):
            parts.append(f"{len(c['skills'])} 个技能")
        wf = c.get("workflows")
        if wf:
            parts.append(f"{len(wf)} 个流程")
        if c.get("scenario"):
            parts.append("1 个场景配置")
        return " · ".join(parts) or "（空）"

    @staticmethod
    def _plugin_detail_text(plugin):
        """插件详情全文（名称/作者/说明 + 能力明细 + 使用方式）。"""
        meta = plugin.get("meta") or {}
        c = plugin.get("contents") or {}
        lines = [
            f"🧩 {meta.get('icon', '🧩')} {meta.get('name', '')} v{meta.get('version', '?')}",
            f"作者：{meta.get('author', '未知')} · 状态：{'已启用' if plugin.get('enabled', True) else '已停用'}",
            "",
        ]
        if meta.get("description"):
            lines.append(f"说明：{meta.get('description', '')}\n")
        tools = c.get("tools") or []
        skills = c.get("skills") or []
        wf = c.get("workflows") or {}
        sc = c.get("scenario")
        for t in tools:
            fn = t.get("function") or {}
            lines.append(f"🔧 工具：{fn.get('name', '?')} — {str(fn.get('description', ''))[:60]}")
        for s in skills:
            lines.append(f"⚡ 技能：{s.get('name', '?')} — {str(s.get('text', ''))[:40]}")
        for wname, wdef in wf.items():
            steps = wdef.get("steps") if isinstance(wdef, dict) else []
            lines.append(f"🔁 流程：{wname}（{len(steps)} 步）")
        if sc:
            lines.append(f"🎭 场景：{sc.get('name', '')}")
        missing = plugins_mod.missing_requires(plugin)
        if missing:
            lines.append(f"\n⚠ 缺失依赖：{'、'.join(missing)}（pip install …）")
        lines.append("\n使用方式：工具=AI 自动调用 · 技能=⚡指令 · 流程=AI/手动运行 · 场景=应用配置")
        return "\n".join(lines)

    def _show_plugin_guide(self, plugin):
        """安装后引导：使用方式 + 快速试用（让用户 30 秒感知插件价值）。"""
        meta = plugin.get("meta") or {}
        c = plugin.get("contents") or {}
        tools = c.get("tools") or []
        skills = c.get("skills") or []
        wf = c.get("workflows") or {}
        sc = c.get("scenario")
        dialog, body, footer = self._dialog_shell(
            f"✅ 插件已安装：{meta.get('icon', '🧩')} {meta.get('name', '')}",
            540, 300,
            subtitle="使用方式与快速试用",
        )
        lines = []
        if tools:
            lines.append(f"🔧 {len(tools)} 个工具：AI 会按需自动调用（与内置工具一起参与任务）")
        if skills:
            lines.append(f"⚡ {len(skills)} 个技能：输入框「⚡ 指令」菜单选择，或点下方按钮直接试用")
        if wf:
            lines.append(f"🔁 {len(wf)} 个流程：AI 自动 / 定时任务 / 「流程管理」手动运行")
        if sc:
            lines.append("🎭 含场景配置：可一键应用思考档/提示词/推荐工具")
        if not lines:
            lines.append("（该插件不含可交互能力）")
        for ln in lines:
            self._lbl(body, ln, bg="panel", font=(FONT_FAMILY, 9), wraplength=480,
                      justify="left").pack(anchor="w", pady=2)
        self._lbl(body, "插件管理：🛠 工具菜单 → 🧩 插件中心 → 我的插件（停用/卸载/导出分享）",
                  role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(8, 0))
        if skills:
            s0 = skills[0]
            self._footer_btn(footer, "关闭", dialog.destroy)
            self._footer_btn(
                footer, f"试用技能：{s0.get('name', '')}",
                lambda s=s0: (self._insert_plugin_skill(s), dialog.destroy()),
                primary=True,
            )
        elif wf:
            w0 = next(iter(wf))
            self._footer_btn(footer, "关闭", dialog.destroy)
            self._footer_btn(
                footer, f"运行流程：{w0}",
                lambda n=w0: (dialog.destroy(), self._flash_status(f"🚀 正在运行流程「{n}」…"), _dc.run_workflow(n)),
                primary=True,
            )
        elif sc:
            self._footer_btn(footer, "关闭", dialog.destroy)
            self._footer_btn(
                footer, "应用场景配置",
                lambda: (self._apply_plugin_scenario(sc), dialog.destroy()),
                primary=True,
            )
        else:
            self._footer_btn(footer, "关闭", dialog.destroy)

    def _insert_plugin_skill(self, skill):
        """把技能模板插入输入框（试用入口）。"""
        self._clear_placeholder()
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", str(skill.get("text") or ""))
        self.input_text.focus_set()
        self._flash_status(f"已插入技能「{skill.get('name', '')}」，补充内容后按 Enter 发送")

    def _install_plugin_file(self, path):
        """共用安装流程：解析 → 重复检测（覆盖更新）→ 确认 → 安装 → 场景询问 → 引导。

        返回 True/False（filedialog 与拖拽导入共用）。
        """
        plugin, err = plugins_mod.parse_plugin_file(path)
        if err:
            messagebox.showerror("插件无效", err)
            return False
        meta = plugin.get("meta") or {}
        name = str(meta.get("name") or "")
        installed = plugins_mod.list_plugins(PLUGINS_DIR)
        exist = next((p for p in installed if (p.get("meta") or {}).get("name") == name), None)
        if exist:
            old_v = (exist.get("meta") or {}).get("version", "?")
            new_v = meta.get("version", "?")
            if not messagebox.askyesno(
                "插件已安装",
                f"已安装插件「{name}」v{old_v}。\n导入版本：v{new_v}\n\n覆盖更新？（旧条目将先移除再安装新版）",
            ):
                return False
            plugins_mod.unapply_plugin(exist, self._plugin_paths())
        missing = plugins_mod.missing_requires(plugin)
        dep_note = ""
        if missing:
            dep_note = "\n\n⚠ 缺失依赖：" + "、".join(missing) + "（相关功能可能不可用，可在「依赖状态」查看安装命令）"
        if not messagebox.askyesno(
            "导入插件",
            f"插件：{name} v{meta.get('version', '?')}（{meta.get('author', '未知')}）\n"
            f"能力：{self._plugin_summary(plugin)}\n"
            f"说明：{meta.get('description', '')}{dep_note}\n\n确认安装？",
        ):
            return False
        res = plugins_mod.apply_plugin(plugin, self._plugin_paths())
        if not res.get("ok"):
            messagebox.showerror("安装失败", str(res.get("error") or "未知错误"))
            return False
        sc = (plugin.get("contents") or {}).get("scenario")
        if isinstance(sc, dict) and sc.get("name"):
            if messagebox.askyesno(
                "应用场景",
                f"插件包含场景配置「{sc.get('name')}」（推荐工具/思考档，可选建议人格）。\n立即应用任务能力？",
            ):
                self._apply_plugin_scenario(sc)
        self._refresh_user_tools_cache()
        self._flash_status(f"✅ 插件「{name}」已安装")
        self._show_plugin_guide(plugin)
        return True

    def _on_plugin_drop(self, event):
        """拖拽 .wtplugin 文件到插件管理对话框 → 直接走安装流程。"""
        paths = re.findall(r"\{(.*?)\}", getattr(event, "data", "") or "")
        if not paths:
            paths = [p.strip() for p in str(getattr(event, "data", "") or "").split()]
        for p in paths:
            if str(p).lower().endswith(plugins_mod.PLUGIN_EXT) and os.path.isfile(p):
                self._install_plugin_file(p)
                return

    def _hub_size(self, w_ratio=0.92, h_ratio=0.94):
        """中心窗口几何：以应用窗口为参照（浏览器新标签页式）。

        非全屏 = 主窗口的 92% 宽 × 94% 高（接近充满，元素全部可见）；
        全屏（F11）= 屏幕的 85% × 85%（四周留边，视觉舒适）。
        屏幕内校验兜底。
        """
        try:
            full = False
            try:
                full = bool(self.root.attributes("-fullscreen"))
            except Exception:
                pass
            if full:
                sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                w, h = int(sw * 0.85), int(sh * 0.85)
            else:
                rw = max(1, self.root.winfo_width())
                rh = max(1, self.root.winfo_height())
                w, h = int(rw * w_ratio), int(rh * h_ratio)
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            w = max(560, min(w, max(560, sw - 40)))
            h = max(420, min(h, max(420, sh - 60)))
            return f"{w}x{h}"
        except Exception:
            return "720x560"

    def show_plugin_hub(self):
        """插件中心：正式页面（我的插件 / 画廊 / 工坊 三页签）。"""
        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("🧩 插件中心")
        w, h = self._hub_size(0.90, 0.93).split("x")
        dialog.geometry(self._center_geometry(int(w), int(h)))
        dialog.minsize(560, 420)
        dialog.transient(self.root)
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        header = tk.Frame(dialog, bg=t["panel"])
        header.pack(fill="x", padx=20, pady=(14, 6))
        self._restyle.append((header, "panel"))
        self._lbl(header, "🧩 插件中心", role="label_accent", bg="panel",
                  font=(FONT_FAMILY, 14, "bold")).pack(side="left")
        self._lbl(header, "零代码能力扩展 · 说需求 AI 造插件 · 插件 = 工具/技能/流程/场景的组合包",
                  role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="left", padx=(14, 0))
        nb = ttk.Notebook(dialog)
        nb.pack(fill="both", expand=True, padx=14, pady=(4, 10))
        self._hub_installed_tab(nb)
        self._hub_gallery_tab(nb)
        self._hub_workshop_tab(nb)
        self._hub = dialog
        return dialog

    def _hub_installed_tab(self, nb):
        """我的插件：列表 + 详情 + 操作（导入/导出/场景/启停/卸载/DND）。"""
        t = self._theme()
        body = tk.Frame(nb, bg=t["panel"])
        nb.add(body, text="我的插件")
        plugins = plugins_mod.list_plugins(PLUGINS_DIR)

        left = tk.Frame(body, bg=t["panel"])
        left.pack(side="left", fill="y", padx=(8, 8))
        self._restyle.append((left, "panel"))
        left_wrap = tk.Frame(left, bg=t["panel"])
        left_wrap.pack(fill="both", expand=True)
        self._restyle.append((left_wrap, "panel"))
        listbox = tk.Listbox(
            left_wrap, width=30, bg=t["input_bg"], fg=t["input_fg"],
            selectbackground=t["selection"], selectforeground=t["accent_text"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], exportselection=False,
        )
        lb_sb = tk.Scrollbar(left_wrap, orient="vertical", command=listbox.yview,
                             relief="flat", bd=0)
        listbox.configure(yscrollcommand=lb_sb.set)
        lb_sb.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)
        for p in plugins:
            meta = p.get("meta") or {}
            mark = "✅" if p.get("enabled", True) else "⏸"
            listbox.insert("end", f"{mark} {meta.get('icon', '🧩')} {meta.get('name', '')}")

        right = tk.Frame(body, bg=t["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(8, 8))
        self._restyle.append((right, "panel"))
        viewer = tk.Text(
            right, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], padx=12, pady=10,
        )
        view_sb = tk.Scrollbar(right, orient="vertical", command=viewer.yview,
                               relief="flat", bd=0)
        viewer.configure(yscrollcommand=view_sb.set)
        view_sb.pack(side="right", fill="y")
        viewer.pack(side="left", fill="both", expand=True)
        if not plugins:
            viewer.insert("1.0", "（暂无已安装插件）\n\n导入 .wtplugin 文件（或拖拽到本页），"
                                 "也可从「画廊」一键安装，或让「工坊」的 AI 按需求生成。")

        def show_item(idx):
            viewer.configure(state="normal")
            viewer.delete("1.0", "end")
            viewer.insert("1.0", self._plugin_detail_text(plugins[idx]))
            viewer.configure(state="disabled")

        listbox.bind("<<ListboxSelect>>",
                     lambda e: (show_item(listbox.curselection()[0])
                                if listbox.curselection() else None))
        if plugins:
            listbox.selection_set(0)
            show_item(0)

        bar = tk.Frame(body, bg=t["panel"])
        bar.pack(fill="x", side="bottom", padx=8, pady=(6, 8))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "导入插件…", self._hub_import_plugin, fsz=9).pack(side="left")
        self._mk_button(bar, "导出分享", self._hub_export_plugin, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "应用场景配置", self._hub_apply_scenario, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "停用/启用", self._hub_toggle_plugin, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "卸载", self._hub_uninstall_plugin, fsz=9).pack(side="left", padx=(6, 0))
        self._lbl(bar, "拖拽 .wtplugin 到列表即可导入",
                  role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="right")
        self._hub_installed_list = (listbox, viewer, plugins)
        if DND_AVAILABLE:
            try:
                body.drop_target_register("DND_Files")
                body.dnd_bind("<<Drop>>", self._hub_plugin_drop)
            except Exception:
                pass

    def _hub_refresh_installed(self):
        """我的插件页刷新（安装/卸载/启停后：销毁重建中心，状态即时更新）。"""
        hub = getattr(self, "_hub", None)
        if hub is None:
            return
        try:
            hub.destroy()
        except Exception:
            pass
        self._hub = None
        self.show_plugin_hub()

    def _hub_selected_plugin(self):
        if not hasattr(self, "_hub_installed_list"):
            return None
        listbox, _v, plugins = self._hub_installed_list
        try:
            sel = listbox.curselection()
        except tk.TclError:
            return None
        if not sel or sel[0] >= len(plugins):
            return None
        return plugins[sel[0]]

    def _hub_import_plugin(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="导入插件",
            filetypes=[("鲸语插件", f"*{plugins_mod.PLUGIN_EXT}"), ("所有文件", "*.*")],
        )
        if not path:
            return
        if self._install_plugin_file(path):
            self._hub_refresh_installed()

    def _hub_plugin_drop(self, event):
        self._on_plugin_drop(event)
        self._hub_refresh_installed()

    def _hub_export_plugin(self):
        p = self._hub_selected_plugin()
        if not p:
            messagebox.showinfo("提示", "请先在列表选择插件。")
            return
        meta = p.get("meta") or {}
        out_path = filedialog.asksaveasfilename(
            parent=self.root,
            initialdir=os.path.expanduser("~"),
            defaultextension=plugins_mod.PLUGIN_EXT,
            filetypes=[("鲸语插件", f"*{plugins_mod.PLUGIN_EXT}")],
            initialfile=f"{plugins_mod._slug(meta.get('name', 'plugin'))}{plugins_mod.PLUGIN_EXT}",
        )
        if not out_path:
            return
        try:
            export = {k: p.get(k) for k in ("format", "version", "meta", "requires", "contents")}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(export, f, ensure_ascii=False, indent=2)
            self._flash_status(f"已导出插件：{out_path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _hub_apply_scenario(self):
        p = self._hub_selected_plugin()
        if not p:
            messagebox.showinfo("提示", "请先在列表选择插件。")
            return
        sc = (p.get("contents") or {}).get("scenario")
        if not isinstance(sc, dict) or not sc.get("name"):
            messagebox.showinfo("提示", "该插件不包含场景配置。")
            return
        if messagebox.askyesno(
            "应用场景",
            f"应用插件场景「{sc.get('name')}」？（推荐工具 / 思考档；建议人格将单独确认，不静默覆盖）",
        ):
            self._apply_plugin_scenario(sc)
            self._flash_status(f"🎭 已应用场景「{sc.get('name')}」（任务能力已配置）")

    def _hub_toggle_plugin(self):
        p = self._hub_selected_plugin()
        if not p:
            messagebox.showinfo("提示", "请先在列表选择插件。")
            return
        want = not p.get("enabled", True)
        if want:
            res = plugins_mod.apply_plugin(p, self._plugin_paths())
            if not res.get("ok"):
                messagebox.showerror("启用失败", str(res.get("error") or "未知错误"))
                return
        else:
            res = plugins_mod.unapply_plugin(p, self._plugin_paths())
            if not res.get("ok"):
                messagebox.showerror("停用失败", str(res.get("error") or "未知错误"))
                return
            try:
                with open(p["_file"], "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["enabled"] = False
                plugins_mod.save_plugin_file(data, PLUGINS_DIR)
            except Exception:
                pass
        self._refresh_user_tools_cache()
        self._hub_refresh_installed()

    def _hub_uninstall_plugin(self):
        p = self._hub_selected_plugin()
        if not p:
            messagebox.showinfo("提示", "请先在列表选择插件。")
            return
        name = (p.get("meta") or {}).get("name", "")
        applied = p.get("applied") or {}
        impact = []
        if applied.get("tools"):
            impact.append(f"{len(applied['tools'])} 个工具")
        if applied.get("skills"):
            impact.append(f"{len(applied['skills'])} 个技能")
        if applied.get("workflows"):
            impact.append(f"{len(applied['workflows'])} 个流程")
        if not messagebox.askyesno(
            "卸载插件",
            f"确认卸载插件「{name}」？\n将移除：{'、'.join(impact) or '无条目'}。\n（不影响你手动添加的同名能力）",
        ):
            return
        plugins_mod.unapply_plugin(p, self._plugin_paths())
        try:
            os.remove(p["_file"])
        except OSError:
            pass
        self._refresh_user_tools_cache()
        self._flash_status(f"已卸载插件「{name}」")
        self._hub_refresh_installed()

    def _hub_gallery_tab(self, nb):
        """插件画廊：示例插件 + 详情 + 一键安装。"""
        t = self._theme()
        body = tk.Frame(nb, bg=t["panel"])
        nb.add(body, text="画廊")
        samples = []
        if os.path.isdir(SAMPLE_PLUGINS_DIR):
            for fn in sorted(os.listdir(SAMPLE_PLUGINS_DIR)):
                if not fn.endswith(plugins_mod.PLUGIN_EXT):
                    continue
                p, err = plugins_mod.parse_plugin_file(os.path.join(SAMPLE_PLUGINS_DIR, fn))
                if p is not None:
                    samples.append(p)
        installed_names = {p["meta"]["name"] for p in plugins_mod.list_plugins(PLUGINS_DIR)}

        left = tk.Frame(body, bg=t["panel"])
        left.pack(side="left", fill="y", padx=(8, 8))
        self._restyle.append((left, "panel"))
        left_wrap = tk.Frame(left, bg=t["panel"])
        left_wrap.pack(fill="both", expand=True)
        self._restyle.append((left_wrap, "panel"))
        listbox = tk.Listbox(
            left_wrap, width=30, bg=t["input_bg"], fg=t["input_fg"],
            selectbackground=t["selection"], selectforeground=t["accent_text"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], exportselection=False,
        )
        lb_sb = tk.Scrollbar(left_wrap, orient="vertical", command=listbox.yview,
                             relief="flat", bd=0)
        listbox.configure(yscrollcommand=lb_sb.set)
        lb_sb.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)
        for p in samples:
            meta = p.get("meta") or {}
            mark = "✅" if meta.get("name") in installed_names else "⬇"
            listbox.insert("end", f"{mark} {meta.get('icon', '🧩')} {meta.get('name', '')}（{self._plugin_summary(p)}）")
        if not samples:
            listbox.insert("end", "（无内置示例插件）")

        right = tk.Frame(body, bg=t["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(8, 8))
        self._restyle.append((right, "panel"))
        viewer = tk.Text(
            right, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], padx=12, pady=10,
        )
        view_sb = tk.Scrollbar(right, orient="vertical", command=viewer.yview,
                               relief="flat", bd=0)
        viewer.configure(yscrollcommand=view_sb.set)
        view_sb.pack(side="right", fill="y")
        viewer.pack(side="left", fill="both", expand=True)

        def show_item(idx):
            viewer.configure(state="normal")
            viewer.delete("1.0", "end")
            viewer.insert("1.0", self._plugin_detail_text(samples[idx]))
            viewer.configure(state="disabled")

        listbox.bind("<<ListboxSelect>>",
                     lambda e: (show_item(listbox.curselection()[0])
                                if listbox.curselection() else None))
        if samples:
            listbox.selection_set(0)
            show_item(0)

        def install_selected():
            sel = listbox.curselection()
            if not sel or sel[0] >= len(samples):
                return
            p = samples[sel[0]]
            name = p["meta"]["name"]
            if name in installed_names:
                messagebox.showinfo("已安装", f"插件「{name}」已安装，可在「我的插件」中查看/停用/卸载。")
                return
            if not messagebox.askyesno("安装示例插件", f"确认安装「{name}」？\n{self._plugin_summary(p)}"):
                return
            res = plugins_mod.apply_plugin(p, self._plugin_paths())
            if not res.get("ok"):
                messagebox.showerror("安装失败", str(res.get("error") or "未知错误"))
                return
            self._refresh_user_tools_cache()
            self._flash_status(f"✅ 示例插件「{name}」已安装")
            self._hub_refresh_installed()
            self._show_plugin_guide(p)

        listbox.bind("<Double-1>", lambda e: install_selected())
        bar = tk.Frame(body, bg=t["panel"])
        bar.pack(fill="x", side="bottom", padx=8, pady=(6, 8))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "安装选中", install_selected, kind="primary", fsz=9).pack(side="left")
        self._lbl(bar, "示例插件：理解插件能力的最佳起点 · 双击安装",
                  role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="right")

    def _hub_workshop_tab(self, nb):
        """插件工坊：说需求，AI 造插件。"""
        t = self._theme()
        body = tk.Frame(nb, bg=t["panel"])
        nb.add(body, text="工坊")
        self._lbl(body, "描述你想要的能力，AI 将生成并安装插件（工具=AI 自动调用 · 技能=⚡指令 · 流程=AI/手动运行）：",
                  role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(8, 4))
        for ex in (
            "· 我想要一个小红书文案工具，根据主题生成爆款标题和正文",
            "· 帮我创建一个『每日数据巡检』流程：读数据库 → 生成报表 → 推送通知",
            "· 添加一个『会议纪要模板』技能，输入会议内容输出结构化纪要",
        ):
            self._lbl(body, ex, role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
                      wraplength=560, justify="left").pack(anchor="w", pady=1)
        text = tk.Text(
            body, height=5, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
            insertbackground=t["input_fg"], relief="flat", highlightthickness=1,
            highlightbackground=t["border"], highlightcolor=t["accent"],
            font=(FONT_FAMILY, 10), padx=10, pady=8,
        )
        text.pack(fill="both", expand=True, pady=(8, 0))
        text.focus_set()

        def run():
            need = text.get("1.0", "end").strip()
            if not need:
                messagebox.showinfo("提示", "请先描述你想要的能力。")
                return
            hub = getattr(self, "_hub", None)
            if hub is not None:
                try:
                    hub.destroy()
                except Exception:
                    pass
                self._hub = None
            self.send(
                text=(
                    f"请使用 create_plugin 工具帮我生成并安装一个插件。\n"
                    f"需求：{need}\n"
                    "生成前先确认需求可实现（名称/包含哪些能力），生成后总结插件内容与使用方式。"
                )
            )

        bar = tk.Frame(body, bg=t["panel"])
        bar.pack(fill="x", side="bottom", pady=(8, 8))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "生成插件", run, kind="primary", fsz=10).pack(side="right")

    def show_tool_hub(self):
        """工具中心：正式页面（概览 / 工具设置 / 权限 三页签）。"""
        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("🛠 工具中心")
        w, h = self._hub_size(0.92, 0.94).split("x")
        dialog.geometry(self._center_geometry(int(w), int(h)))
        dialog.minsize(560, 420)
        dialog.transient(self.root)
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        header = tk.Frame(dialog, bg=t["panel"])
        header.pack(fill="x", padx=20, pady=(14, 6))
        self._restyle.append((header, "panel"))
        self._lbl(header, "🛠 工具中心", role="label_accent", bg="panel",
                  font=(FONT_FAMILY, 14, "bold")).pack(side="left")
        self._lbl(header, "工具/权限/环境的统一管理 · 未选中的工具不会出现在请求中",
                  role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="left", padx=(14, 0))
        nb = ttk.Notebook(dialog)
        nb.pack(fill="both", expand=True, padx=14, pady=(4, 4))
        self._hub_saves = []
        self._hub_tools_overview(nb)
        self._hub_tools_settings(nb)
        self._hub_tools_permissions(nb)
        # 底部保存栏：工具设置与权限面板的保存回调在此统一触发（面板化后保存入口不丢失）
        foot = tk.Frame(dialog, bg=t["panel"])
        foot.pack(fill="x", padx=14, pady=(0, 10))
        self._restyle.append((foot, "panel"))
        self._lbl(foot, "修改工具/权限后点击保存生效", role="label_sec", bg="panel",
                  font=(FONT_FAMILY, 9)).pack(side="left")
        self._mk_button(foot, "💾 保存更改", lambda: self._hub_save_all(), kind="primary", fsz=10).pack(side="right")
        self._mk_button(foot, "关闭", dialog.destroy, fsz=9).pack(side="right", padx=(0, 8))
        self._hub = dialog
        return dialog

    def _hub_save_all(self):
        """工具中心统一保存：触发工具设置与权限面板的保存回调。"""
        saved = 0
        for fn in getattr(self, "_hub_saves", []) or []:
            try:
                fn()
                saved += 1
            except Exception:
                pass
        self._hub_saves = []
        self._flash_status(f"✅ 工具中心更改已保存（{saved} 个面板）")

    def _hub_tools_overview(self, nb):
        """工具中心-概览：环境/模型/用量/依赖摘要 + 高频快捷入口。"""
        t = self._theme()
        body = tk.Frame(nb, bg=t["panel"])
        nb.add(body, text="概览")
        # 快捷入口行
        quick = tk.Frame(body, bg=t["panel"])
        quick.pack(fill="x", padx=8, pady=(8, 4))
        self._restyle.append((quick, "panel"))
        self._lbl(quick, "快捷操作：", role="label_sec", bg="panel",
                  font=(FONT_FAMILY, 9, "bold")).pack(side="left", padx=(0, 8))
        for label, fn in (
            ("💰 查余额", self.check_balance),
            ("📊 用量统计", self.show_stats),
            ("🎯 预算设置", self.edit_budget),
            ("⚖ 模型对比", self.compare_models),
            ("📐 上下文详情", self.show_context_details),
        ):
            self._mk_button(quick, label, fn, fsz=9).pack(side="left", padx=(0, 6))

        nav = tk.Frame(body, bg=t["panel"])
        nav.pack(fill="x", padx=8, pady=(2, 4))
        self._restyle.append((nav, "panel"))
        self._lbl(nav, "导航：", role="label_sec", bg="panel",
                  font=(FONT_FAMILY, 9, "bold")).pack(side="left", padx=(0, 8))
        for label, fn in (
            ("⏰ 定时任务", self.manage_schedules),
            ("🔁 流程管理", self.manage_workflows),
            ("📚 知识库", self.manage_knowledge),
            ("📍 检查点", self.show_checkpoint),
            ("📋 任务记录", self.show_tasklog),
            ("📦 最近产物", self.show_recent_outputs),
            ("📁 工作目录", self.choose_working_dir),
            ("🔧 失败模式", self.show_failures),
            ("🧩 插件中心", self.show_plugin_hub),
        ):
            self._mk_button(nav, label, fn, fsz=9).pack(side="left", padx=(0, 6))

        scroll_wrap = tk.Frame(body, bg=t["panel"])
        scroll_wrap.pack(fill="both", expand=True, padx=8, pady=4)
        self._restyle.append((scroll_wrap, "panel"))
        canvas, inner = self._scroll_panel(scroll_wrap)

        def card(title, lines, color=None):
            self._lbl(inner, title, role="label_accent", bg="panel",
                      font=(FONT_FAMILY, 10, "bold")).pack(anchor="w", pady=(8, 2))
            for ln in lines:
                self._lbl(inner, "· " + ln, bg="panel", font=(FONT_FAMILY, 9),
                          fg=color if color else None, wraplength=600,
                          justify="left").pack(anchor="w", padx=(12, 0), pady=1)

        # 模型与环境
        model = self.model_combo.get().strip() or "deepseek-v4-flash"
        m_info = MODELS.get(model, {})
        card("模型", [f"{model}（{m_info.get('version', '?')}） · 上下文 1M / 输出上限 384K"])
        # 人格（当前生效角色）
        role_name = self._current_role_name(self.cfg.get("system_prompt", ""))
        card("人格", [f"🎭 当前角色：{role_name}（设置面板或「角色与提示词」切换）"])
        # 用量（今日）
        try:
            data = stats.load_stats(STATS_PATH)
            today = stats.day_total(data, date.today().isoformat())
            today_cost = sum(
                stats.estimate_cost(usage, mdl)
                for mdl, usage in (data.get(date.today().isoformat()) or {}).items()
            )
            card("用量", [
                f"今日：输入 {today['prompt']:,} / 输出 {today['completion']:,} token"
                f"（缓存命中 {today['cache_hit']:,}）≈ ¥{stats.format_cost(today_cost)}",
            ])
        except Exception:
            card("用量", ["（统计数据暂不可用）"])
        # 依赖
        deps = self._dependency_status()
        n_ok = sum(1 for _, ok, _, _ in deps if ok)
        missing = [name for name, ok, _u, _h in deps if not ok]
        dep_line = f"可选能力 {n_ok}/{len(deps)} 已就绪"
        if missing:
            dep_line += f" · 缺失 {len(missing)} 项（{', '.join(missing[:5])}）"
        card("依赖", [dep_line])
        # 工作目录
        card("工作目录", [self._get_active_dir()])
        # 任务能力（自主模式语义：完全智能=全部工具 / 纯对话=无工具 / 标准=按工具设置）
        if self.cfg.get("full_auto"):
            cap_line = "🤖 完全智能：全部工具自动可用（为开发/创作而生，权限闸门跳过）"
        elif self.cfg.get("pure_chat"):
            cap_line = "💬 纯对话：不调用任何工具"
        else:
            cap_line = "标准：按下方「工具设置」勾选"
        card("任务能力", [f"自主模式 → {cap_line}"])
        # 安全
        d = permissions.get_data()
        mode = d.get("approval_mode", "auto")
        auto = "🤖 完全智能（允许目录内全自动）" if permissions.is_full_auto() else f"审批模式：{mode}"
        card("安全", [auto, f"写文件：{'开' if d['filesystem'].get('allow_write') else '关'} · "
                           f"命令执行：{'开' if d['shell'].get('allow_run_command') else '关'}"])

    def _hub_tools_settings(self, nb):
        """工具中心-工具设置（可启停全部工具）。"""
        t = self._theme()
        body = tk.Frame(nb, bg=t["panel"])
        nb.add(body, text="工具设置")
        save = self._edit_tools_panel(body)
        if save:
            self._hub_saves.append(save)

    def _hub_tools_permissions(self, nb):
        """工具中心-权限（行动能力闸门）。"""
        t = self._theme()
        body = tk.Frame(nb, bg=t["panel"])
        nb.add(body, text="权限")
        save = self._edit_permissions_panel(body)
        if save:
            self._hub_saves.append(save)

    def show_plugins(self):
        """插件管理（已并入插件中心，兼容入口）。"""
        self.show_plugin_hub()

    def _apply_plugin_scenario(self, sc):
        """应用插件场景配置：推荐工具 + 思考档（任务能力）；建议人格提示词单独询问。

        与自主模式/人格分层一致：不静默覆盖当前人格。
        """
        name = str(sc.get("name") or "插件场景")
        if sc.get("thinking") in THINKING_MODES:
            self.cfg["thinking"] = sc["thinking"]
        sp = str(sc.get("system_prompt") or "").strip()
        if sp:
            # 建议人格单独确认：避免插件静默覆盖用户人格
            if messagebox.askyesno(
                "应用建议人格",
                f"插件「{name}」建议人格提示词：\n{sp[:120]}{'…' if len(sp) > 120 else ''}\n\n"
                "是否一并应用？（将覆盖当前角色/系统提示词；选否则保持当前人格）",
            ):
                self.cfg["system_prompt"] = sp
                if self.messages:
                    self.messages[0]["content"] = sp
                self._update_role_label()
        tools = sc.get("enabled_tools")
        if isinstance(tools, list):
            valid = self._valid_tool_names()
            self.cfg["enabled_tools"] = [t for t in tools if t in valid]
        save_config(self.cfg)
        self._flash_status(f"🎭 已应用插件场景「{name}」（任务能力已配置）")

    def show_plugin_gallery(self):
        """插件画廊（已并入插件中心，兼容入口）。"""
        self.show_plugin_hub()

    def show_plugin_workshop(self):
        """插件工坊（已并入插件中心，兼容入口）。"""
        self.show_plugin_hub()

    def show_dependencies(self):
        """依赖状态：可选能力清单与安装指引。"""
        t = self._theme()
        rows = self._dependency_status()
        n_ok = sum(1 for _, ok, _, _ in rows if ok)
        dialog, body, footer = self._dialog_shell(
            "依赖状态", 580, 500,
            subtitle=f"可选能力：{n_ok}/{len(rows)} 已安装（缺失项给出安装命令；所有缺失功能均自动降级提示）",
        )
        canvas, inner = self._scroll_panel(body)
        for name, ok, use, hint in rows:
            row = tk.Frame(inner, bg=t["panel"])
            row.pack(fill="x", pady=2)
            self._restyle.append((row, "panel"))
            mark = "✅" if ok else "⚠ "
            fg = t["success"] if ok else t["warning"]
            self._lbl(row, f"{mark} {name}", bg="panel", font=(FONT_FAMILY, 9, "bold"),
                      fg=fg, width=17, anchor="w").pack(side="left")
            self._lbl(row, use, role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
                      anchor="w").pack(side="left", padx=(6, 0))
            if not ok:
                self._lbl(row, hint, role="label_sec", bg="panel", font=(MONO_FAMILY, 8),
                          anchor="e").pack(side="right")
        self._footer_btn(footer, "关闭", dialog.destroy)

    def show_failures(self):
        """失败模式库：查看 AI 工具曾失败的原因（供分析/规避）。"""
        t = self._theme()
        items = self._load_failures()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title(f"失败模式库（{len(items)} 条 · 自动积累工具失败原因）")
        dialog.geometry("560x400")
        dialog.transient(self.root)
        text = tk.Text(
            dialog, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], padx=12, pady=10,
        )
        text.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        for r in reversed(items[-200:]):
            text.insert(
                "end",
                f"🔧 {r.get('tool', '?')} · [{r.get('ts', '')}]\n"
                f"参数: {r.get('args', '')}\n错误: {r.get('error', '')}\n\n",
            )
        if not items:
            text.insert("1.0", "（暂无失败记录，AI 工具执行失败时自动积累）")
        text.configure(state="disabled")
        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=12, pady=(8, 12))
        self._restyle.append((bar, "panel"))

        def clear():
            if messagebox.askyesno("清空失败模式", "确认清空全部失败记录？"):
                self._save_failures([])
                dialog.destroy()
                self.show_failures()

        self._mk_button(bar, "清空", clear, fsz=9).pack(side="left")
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

    def copy_share_text(self):
        """把当前对话复制为可分享的 Markdown 文本。"""
        if len(self.messages) <= 1:
            messagebox.showinfo("提示", "暂无对话可分享。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.build_markdown())
        self._flash_status("已复制对话分享文本（Markdown）")

    def manage_memory(self):
        t = self._theme()
        data = self._load_memory()
        dialog, body, footer = self._dialog_shell(
            "长期记忆", 560, 480,
            subtitle="用户手动维护的背景信息，随每次请求注入；Agent 也可通过 write_memory 自动写入",
        )
        enabled_var = tk.BooleanVar(value=bool(data.get("enabled", False)))
        ttk.Checkbutton(
            body, text="启用长期记忆（注入到每次请求，记忆内容请保持精炼）",
            variable=enabled_var,
        ).pack(anchor="w", pady=(0, 8))
        listbox = tk.Listbox(
            body,
            height=12,
            bg=t["input_bg"],
            fg=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
            exportselection=False,
        )
        listbox.pack(fill="both", expand=True)
        facts = data["facts"]

        def refresh():
            listbox.delete(0, "end")
            for f in facts:
                listbox.insert("end", f"{f.get('key')}: {f.get('value')}")

        refresh()
        row = tk.Frame(body, bg=t["panel"])
        row.pack(fill="x", pady=(8, 0))
        self._restyle.append((row, "panel"))
        key_var = tk.StringVar()
        val_var = tk.StringVar()
        ttk.Entry(row, textvariable=key_var, width=18).pack(side="left")
        ttk.Entry(row, textvariable=val_var).pack(side="left", fill="x", expand=True, padx=(6, 0))

        def add_fact():
            k = key_var.get().strip()
            v = val_var.get().strip()
            if not k or not v:
                return
            facts.append({"key": k, "value": v, "ts": datetime.now().isoformat(timespec="seconds")})
            key_var.set("")
            val_var.set("")
            refresh()

        def del_fact():
            sel = listbox.curselection()
            if not sel:
                return
            facts.pop(sel[0])
            refresh()

        def save_all():
            data["enabled"] = bool(enabled_var.get())
            if self._save_memory(data):
                self._flash_status("长期记忆已保存")
            dialog.destroy()

        bar = tk.Frame(body, bg=t["panel"])
        bar.pack(fill="x", pady=(12, 0))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "添加", add_fact, fsz=9).pack(side="left")
        self._mk_button(bar, "删除选中", del_fact, fsz=9).pack(side="left", padx=(8, 0))
        self._footer_btn(footer, "关闭", dialog.destroy)
        self._footer_btn(footer, "保存", save_all, primary=True)

    def _tool_approval(self, name, raw_args):
        """工具执行前审批闸门（worker 线程调用，禁碰 Tk，只走队列）。"""
        if name not in permissions.ACTION_TOOLS:
            return True, ""
        try:
            args = json.loads(raw_args) if raw_args else {}
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            args = {}
        return permissions.request_approval(name, args)

    def _ask_user_callback(self, prompt):
        """ask_user 工具回调（worker 线程调用）：排队弹窗并阻塞等待用户回答。

        0.5s 切片轮询 stop_event：用户点「停止」后立即返回，不等满 180s。"""
        ev = threading.Event()
        box = {"answer": None}
        self._ui_queue.put(("ask", (str(prompt), ev, box)))
        deadline = time.monotonic() + 180
        try:
            while not ev.wait(timeout=0.5):
                if self.stop_event and self.stop_event.is_set():
                    return "（用户停止了生成）"
                if time.monotonic() >= deadline:
                    break
        except Exception:
            pass
        answer = box.get("answer")
        if answer is None:
            return "（用户未在限时内回答，请简化问题或改用其他方式）"
        return str(answer)

    def _show_ask_dialog(self, prompt, ev, box):
        """主线程：Agent 向用户提问的对话框。"""
        dialog, body, footer = self._dialog_shell(
            "Agent 询问", 480, 240, subtitle="助手需要向你确认："
        )
        dialog.grab_set()
        self._register_modal(dialog)
        msg = self._lbl(
            body, prompt, bg="panel", font=(FONT_FAMILY, 10), wraplength=420, justify="left"
        )
        msg.pack(anchor="w", pady=(4, 12))
        var = tk.StringVar()
        entry = ttk.Entry(body, textvariable=var)
        entry.pack(fill="x")
        entry.focus_set()

        def submit():
            box["answer"] = var.get().strip()
            ev.set()
            dialog.destroy()

        def cancel():
            box["answer"] = None
            ev.set()
            dialog.destroy()

        entry.bind("<Return>", lambda e: submit())
        dialog.bind("<Escape>", lambda e: cancel())
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        self._footer_btn(footer, "提交", submit, primary=True)
        self._footer_btn(footer, "跳过", cancel)
        self._watch_dialog_timeout(dialog, ev, 180)

    WHITELIST_TYPE_LABELS = {
        "dir": ("加入允许目录", "以后 AI 可以读取/操作该目录内的文件"),
        "command": ("加入命令白名单", "以后 AI 可以执行该命令"),
        "write": ("开启文件写权限", "以后 AI 可以在允许目录内创建/修改文件"),
    }

    def _wait_stop_aware(self, ev, timeout):
        """0.5s 切片轮询 Event：用户点「停止」后立即返回 False，不等满 timeout。"""
        deadline = time.monotonic() + timeout
        try:
            while not ev.wait(timeout=0.5):
                if self.stop_event and self.stop_event.is_set():
                    return False
                if time.monotonic() >= deadline:
                    return False
        except Exception:
            return False
        return True

    def _watch_dialog_timeout(self, dialog, ev, timeout):
        """弹窗生命周期跟随：worker 侧超时/停止返回后，未获用户响应的
        弹窗自动销毁，杜绝审批/询问弹窗残留（此前 worker 超时返回后
        Toplevel 仍一直挂着，直到用户手动关闭）。"""
        deadline = time.monotonic() + timeout

        def _check():
            try:
                if ev.is_set():
                    return
                if time.monotonic() >= deadline or (
                    self.stop_event and self.stop_event.is_set()
                ):
                    dialog.destroy()
                    return
            except tk.TclError:
                return
            try:
                dialog.after(500, _check)
            except tk.TclError:
                pass

        try:
            dialog.after(500, _check)
        except tk.TclError:
            pass

    def _request_whitelist_callback(self, action_type, value):
        """request_permission 工具回调（worker 线程调用）：弹窗确认后一键加入白名单。"""
        ev = threading.Event()
        box = {"allow": False}
        self._ui_queue.put(("whitelist_req", (str(action_type), str(value), ev, box)))
        self._wait_stop_aware(ev, permissions.approval_timeout())
        if not box.get("allow"):
            return False, "用户拒绝添加白名单"
        ok, msg = permissions.add_to_whitelist(str(action_type), str(value))
        if ok:
            permissions.audit(
                "whitelist_add", str(action_type), str(value)[:200], result="approved"
            )
        return ok, msg

    def _show_whitelist_dialog(self, action_type, value, ev, box):
        """主线程：AI 请求添加白名单的确认弹窗。"""
        t = self._theme()
        labels = self.WHITELIST_TYPE_LABELS.get(
            str(action_type).lower(), (f"白名单（{action_type}）", "加入白名单后 AI 可执行该操作")
        )
        dialog, body, footer = self._dialog_shell(
            "AI 白名单请求", 520, 280,
            subtitle="🔒 AI 请求加入白名单 — " + labels[0],
        )
        dialog.grab_set()
        self._register_modal(dialog)
        self._lbl(
            body, labels[1], role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w")
        if str(value):
            val_label = tk.Label(
                body, text=str(value), bg=t["surface"], fg=t["text"],
                font=(FONT_FAMILY, 9), anchor="w", padx=8, pady=4, wraplength=460,
            )
            val_label.pack(fill="x", pady=(10, 4))
        note = self._lbl(
            body, "同意后立即生效（可随时在「权限设置」中撤销）",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        )
        note.pack(anchor="w")

        def allow():
            box["allow"] = True
            ev.set()
            dialog.destroy()

        def deny():
            box["allow"] = False
            ev.set()
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", deny)
        dialog.bind("<Escape>", lambda e: deny())
        dialog.bind("<Return>", lambda e: allow())
        self._footer_btn(footer, "拒绝", deny)
        self._footer_btn(footer, "✓ 同意添加", allow, primary=True)
        self._watch_dialog_timeout(dialog, ev, permissions.approval_timeout())

    def _request_approval_dialog(self, name, args):
        """confirm 模式下请求用户确认（worker 线程调用）：排队弹窗并阻塞等待。"""
        ev = threading.Event()
        box = {"allow": False, "reason": "审批超时未响应（自动拒绝）"}
        self._ui_queue.put(("approval_req", (name, args, ev, box)))
        self._wait_stop_aware(ev, permissions.approval_timeout())
        if box["allow"]:
            return True, ""
        return False, box.get("reason", "用户拒绝")

    def _show_approval_dialog(self, name, args, ev, box):
        """主线程：AI 权限请求弹窗（允许/拒绝）。"""
        dialog, body, footer = self._dialog_shell(
            "AI 权限请求", 520, 260,
            subtitle=f"AI 请求调用工具「{name}」",
        )
        dialog.attributes("-topmost", True)
        self._register_modal(dialog)
        try:
            args_str = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            args_str = str(args)
        self._lbl(
            body, f"参数：{args_str[:300]}", bg="panel", font=(FONT_FAMILY, 9),
            wraplength=470, justify="left",
        ).pack(anchor="w", pady=(4, 8))
        self._lbl(
            body, "该操作在权限范围内，确认后执行（不确认将自动超时拒绝）。",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w")

        def done(allow, reason):
            box["allow"] = allow
            box["reason"] = reason
            try:
                ev.set()
            except Exception:
                pass
            dialog.destroy()

        self._footer_btn(footer, "拒绝", lambda: done(False, "用户拒绝"))
        self._footer_btn(footer, "允许", lambda: done(True, "用户允许"), primary=True)
        dialog.bind("<Escape>", lambda e: done(False, "用户拒绝"))
        dialog.bind("<Return>", lambda e: done(True, "用户允许"))
        self._watch_dialog_timeout(dialog, ev, permissions.approval_timeout())

    def edit_permissions(self):
        """权限设置（工具中心面板版）。"""
        dialog, body, footer = self._dialog_shell(
            "权限设置", 540, 540,
            subtitle="⚠ 所有行动能力默认关闭；开启前请确认信任范围。",
        )
        save = self._edit_permissions_panel(body)
        self._footer_hint(footer, "配置文件：permissions.json")
        self._footer_btn(footer, "关闭", dialog.destroy)
        self._footer_btn(footer, "保存", lambda: (save(), dialog.destroy()), primary=True)

    def _edit_permissions_panel(self, body):
        """权限设置面板（嵌入工具中心）：返回保存回调。"""
        data = permissions.get_data()
        # 滚动容器：内容超出面板高度时可滚动，杜绝底部元素被截断
        canvas, inner = self._scroll_panel(body)
        body = inner
        self._lbl(
            body, "⚠ 所有行动能力默认关闭；开启前请确认信任范围。",
            role="label_error", bg="panel", font=(FONT_FAMILY, 9, "bold"),
        ).pack(anchor="w", pady=(0, 10))
        if permissions.is_full_auto():
            self._lbl(
                body, "🤖 完全智能模式已开启：允许目录内全自动，本页开关被跳过（系统阻止列表仍生效）。",
                role="label_accent", bg="panel", font=(FONT_FAMILY, 8, "bold"),
            ).pack(anchor="w", pady=(0, 8))

        write_var = tk.BooleanVar(value=bool(data["filesystem"].get("allow_write", False)))
        ttk.Checkbutton(
            body, text="允许 AI 写文件（write_file / edit_file / create_doc）",
            variable=write_var,
        ).pack(anchor="w")
        self._lbl(
            body, "允许目录（逗号分隔，~ 表示用户目录；工作区已默认允许）：",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(8, 2))
        dirs_var = tk.StringVar(value=", ".join(data["filesystem"].get("allowed_dirs", [])))
        ttk.Entry(body, textvariable=dirs_var).pack(fill="x")

        shell_var = tk.BooleanVar(value=bool(data["shell"].get("allow_run_command", False)))
        ttk.Checkbutton(
            body, text="允许执行白名单命令（run_command）",
            variable=shell_var,
        ).pack(anchor="w", pady=(10, 0))
        self._lbl(
            body, "命令白名单（逗号分隔，如 python, pip, pytest, git）：",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(8, 2))
        wl_var = tk.StringVar(value=", ".join(data["shell"].get("whitelist", [])))
        ttk.Entry(body, textvariable=wl_var).pack(fill="x")

        self._lbl(
            body, "审批模式：", role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(8, 2))
        mode_var = tk.StringVar(value=data.get("approval_mode", "auto"))
        ttk.Combobox(
            body, textvariable=mode_var, values=["auto", "confirm", "deny"],
            state="readonly",
        ).pack(fill="x")
        self._lbl(
            body, "auto：白名单内自动执行；confirm：每次调用弹窗确认；deny：全部拒绝。",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
            wraplength=480, justify="left",
        ).pack(anchor="w", pady=(4, 0))
        plan_var = tk.BooleanVar(value=bool(data.get("plan_confirm", False)))
        ttk.Checkbutton(
            body, text="计划确认：每轮工具调用前先确认整轮计划（推荐开启）",
            variable=plan_var,
        ).pack(anchor="w", pady=(10, 0))

        # SSRF 信任白名单：回环默认放行（本地开发服务器验证），内网/保留网段
        # 需在此显式信任后模型才能访问（云元数据 169.254.169.254 永远不可豁免）
        self._lbl(
            body, "SSRF 信任主机（内网访问需显式信任；回环 localhost 已默认放行）：",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(10, 2))
        ssrf_var = tk.StringVar(value=", ".join(self.cfg.get("ssrf_trusted") or []))
        ttk.Entry(body, textvariable=ssrf_var).pack(fill="x")
        self._lbl(
            body, "支持 IP / 主机名 / CIDR 网段（如 192.168.1.0/24）；可访问内网服务（NAS、公司系统等）。",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 8),
            wraplength=480, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        def save_perms():
            data["filesystem"]["allow_write"] = bool(write_var.get())
            data["filesystem"]["allowed_dirs"] = [
                d.strip() for d in dirs_var.get().split(",") if d.strip()
            ]
            data["shell"]["allow_run_command"] = bool(shell_var.get())
            data["shell"]["whitelist"] = [
                w.strip() for w in wl_var.get().split(",") if w.strip()
            ]
            data["approval_mode"] = str(mode_var.get()) or "auto"
            data["plan_confirm"] = bool(plan_var.get())
            permissions.set_data(data)
            if permissions.save():
                self._flash_status("权限设置已保存")
            # SSRF 信任白名单保存（回环默认放行；内网需显式信任）
            trusted = [h.strip() for h in ssrf_var.get().split(",") if h.strip()]
            self.cfg["ssrf_trusted"] = trusted
            _dc.set_ssrf_trusted(trusted)
            save_config(self.cfg)

        return save_perms

    def show_fim_dialog(self):
        cfg = self.save_widgets_to_config()
        if not cfg["api_key"]:
            messagebox.showwarning("未配置 API Key", "请先填写 DeepSeek API Key。")
            return
        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title("FIM 代码补全（Beta）")
        dialog.geometry("700x540")
        dialog.transient(self.root)
        body = tk.Frame(dialog, bg=t["panel"])
        body.pack(fill="both", expand=True, padx=14, pady=(12, 0))
        self._restyle.append((body, "panel"))
        self._lbl(
            body, "前缀（已有代码，模型从此继续补全中间内容）",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w")
        prefix_text = tk.Text(
            body, height=8, wrap="none", bg=t["input_bg"], fg=t["input_fg"],
            insertbackground=t["input_fg"], relief="flat", highlightthickness=1,
            highlightbackground=t["border"], highlightcolor=t["accent"],
            font=(MONO_FAMILY, 9), padx=8, pady=6,
        )
        prefix_text.pack(fill="x", pady=(2, 6))
        self._lbl(
            body, "后缀（可选，补全内容位于前缀与后缀之间）",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w")
        suffix_text = tk.Text(
            body, height=4, wrap="none", bg=t["input_bg"], fg=t["input_fg"],
            insertbackground=t["input_fg"], relief="flat", highlightthickness=1,
            highlightbackground=t["border"], highlightcolor=t["accent"],
            font=(MONO_FAMILY, 9), padx=8, pady=6,
        )
        suffix_text.pack(fill="x", pady=(2, 6))
        self._lbl(
            body, "补全结果", role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        ).pack(anchor="w")
        result_text = tk.Text(
            body, height=9, wrap="word", bg=t["input_bg"], fg=t["input_fg"],
            insertbackground=t["input_fg"], relief="flat", highlightthickness=1,
            highlightbackground=t["border"], highlightcolor=t["accent"],
            font=(MONO_FAMILY, 9), padx=8, pady=6,
        )
        result_text.pack(fill="both", expand=True, pady=(2, 6))
        status_lbl = self._lbl(
            body, "FIM 需要 Beta 端点，未开启时自动使用 /beta", role="label_sec",
            bg="panel", font=(FONT_FAMILY, 9),
        )
        status_lbl.pack(anchor="w")
        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=14, pady=(8, 12))
        self._restyle.append((bar, "panel"))

        def run_fim():
            prefix = prefix_text.get("1.0", "end").strip()
            if not prefix:
                messagebox.showinfo("提示", "请输入前缀。")
                return
            suffix = suffix_text.get("1.0", "end").strip()
            status_lbl.configure(text="正在调用 FIM 补全…")
            result_text.delete("1.0", "end")

            # worker 线程禁止直接碰 Tk（after/configure 都不是线程安全的），
            # 结果经 UI 队列回主线程 _drain_ui_queue 处理
            self._fim_widgets = (result_text, status_lbl, t)
            self._capture_client_params()

            def worker():
                try:
                    client = self.ensure_client()
                    result = client.fim_complete(prefix, suffix)
                    self._ui_queue.put(("fim_done", (True, result)))
                except Exception as e:
                    self._ui_queue.put(("fim_done", (False, str(e))))

            threading.Thread(target=worker, daemon=True).start()

        dialog.bind(
            "<Destroy>",
            lambda e: setattr(self, "_fim_widgets", None)
            if e.widget is dialog
            else None,
        )

        def insert_result():
            result = result_text.get("1.0", "end").strip()
            if not result:
                return
            self._clear_placeholder()
            self.input_text.delete("1.0", "end")
            self.input_text.insert("1.0", result)
            self.input_text.focus_set()
            dialog.destroy()
            self._flash_status("已插入补全结果")

        self._mk_button(bar, "补全", run_fim, kind="primary", fsz=9).pack(side="left")
        self._mk_button(bar, "插入输入框", insert_result, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

    def _write_user_tools(self, items):
        try:
            # 原子写：自定义工具编辑中断不损坏配置文件
            self._atomic_json_write(USER_TOOLS_PATH, items)
        except Exception:
            logging.exception("保存自定义工具失败")
            messagebox.showerror("保存失败", "无法写入自定义工具配置文件。")

    def _capture_client_params(self):
        """主线程入口捕获客户端参数：后台线程不读 Tk 变量（Tkinter 非线程安全）。

        在 send / 继续生成 / FIM / 纪要等线程启动前调用，worker 线程内的
        ensure_client 一律使用这里捕获的快照，避免偶发 TclError/崩溃。
        """
        self._client_key = self.key_var.get().strip()
        self._client_model = self.model_combo.get().strip() or "deepseek-v4-flash"
        # v2 能力层：图片生成配置（工具线程读取）
        _dc.IMAGE_GEN_KEY = self.cfg.get("image_api_key", "") or self._client_key
        _dc.IMAGE_GEN_BASE = self.cfg.get("image_base_url", "") or self.cfg.get("base_url", "")
        _dc.IMAGE_GEN_MODEL = self.cfg.get("image_model", "gpt-image-1")
        # run_workflow 回调（线程安全：只投递 UI 队列）
        _dc.set_send_callback(lambda text: self._ui_queue.put(("timer_task", text)))
        _dc.set_busy_provider(lambda: self.busy)

    def ensure_client(self):
        key = getattr(self, "_client_key", None)
        if key is None:
            key = self.key_var.get().strip()
        base_url = self.cfg["base_url"].strip() or DEFAULT_BASE_URL
        if self.cfg.get("beta_api") and not base_url.endswith("/beta"):
            base_url = base_url.rstrip("/") + "/beta"
        model = getattr(self, "_client_model", None)
        if model is None:
            model = self.model_combo.get().strip() or "deepseek-v4-flash"
        timeout = self.cfg.get("timeout", 120)
        if (
            self.client is None
            or self.client.api_key != key
            or self.client.base_url != base_url
            or self.client.model != model
            or self.client.timeout != timeout
        ):
            self.client = DeepSeekClient(
                api_key=key, base_url=base_url, model=model, timeout=timeout
            )
            _dc.set_active_client(self.client)
        return self.client

    def _ensure_follow(self):
        """智能跟随滚动：_follow_bottom 为 True（用户未手动滚动/已滚回底部）
        时贴底显示最新内容；用户手动滚动后置 False 保持位置，滚回底部自动恢复。

        跟随意图用显式状态跟踪（滚动事件置位），不用插入后的 yview 即时值——
        Tk 布局滞后会让"明明在底部"误判为不在底部，导致输出不自动滚动。
        所有内容插入路径（文本/思考增量/工具卡片/分帧完成/重渲染）都必须调用。
        """
        try:
            if self._follow_bottom:
                self.chat_text.see("end")
        except tk.TclError:
            pass

    def _mark_user_scrolled(self, event=None):
        """用户手动滚动：离开底部停止跟随；滚动回底部自动恢复。"""
        try:
            self._follow_bottom = self.chat_text.yview()[1] >= 0.995
        except tk.TclError:
            self._follow_bottom = False

    def _append(self, text, tag=None):
        if self._paged_render is not None:
            # 分帧进行中：挂起追加，分帧完成后补插（同步补全渲染会冻结 UI 0.5-数秒）
            self._pending_appends.append((text, tag))
            return
        self.chat_text.configure(state="normal")
        self.chat_text.insert("end", text, tag)
        self.chat_text.configure(state="disabled")
        self._ensure_follow()

    def _append_message_block(self, header, body, tag):
        # 用户消息插入 = 新一轮交互，强制回到底部（先看到自己的消息与 AI 回复）
        self._follow_bottom = True
        now = datetime.now().strftime("%H:%M:%S")
        header_tag = "user_time" if tag == "user" else "time"
        header_line = f"[{now}] {header}\n"
        self._append(header_line, header_tag)
        self.blocks.append(("note", header_line, header_tag))
        if body:
            body_line = body.rstrip() + "\n"
            self._append(body_line, tag)
            self.blocks.append((tag, body_line))
        self._append("\n")
        self.blocks.append(("plain", "\n"))

    def _snapshot_assets(self):
        """取主线程 Tcl 值并浅拷贝快照数据（纯 Python，供后台线程序列化写盘）。

        快照必须携带 id/name/tags/pinned：恢复时完整还原会话身份与元数据，
        否则恢复后 id 变化（旧会话文件残留）、标签与固定消息丢失。
        """
        sid = self._session_id(self._current)
        with self._messages_lock:
            # 快照拷贝与 worker 的裁剪/压缩互斥：防拷贝到删除中间态
            msgs_snapshot = list(self.messages)
            usage_snapshot = dict(self.usage_total)
            stars_snapshot = list(self._current.get("stars") or [])
            tags_snapshot = list(self._current.get("tags") or [])
            pinned_snapshot = list(self._current.get("pinned") or [])
        data = {
            "id": sid,
            "name": self._current.get("name"),
            "messages": msgs_snapshot,
            "usage_total": usage_snapshot,
            "stars": stars_snapshot,
            "tags": tags_snapshot,
            "pinned": pinned_snapshot,
            "top": bool(self._current.get("top")),
            "model": self.model_combo.get(),
            "scenario": self.scenario_combo.get(),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        return sid, data

    def _write_snapshot_files(self, sid, data, name, tags, pinned, done_cb=None):
        """会话文件 + 快照文件一次写盘（可在后台线程执行，纯 Python 无 Tcl）。"""
        sess = {
            "id": sid,
            "name": name,
            "messages": data["messages"],
            "usage_total": data["usage_total"],
            "stars": data["stars"],
            "tags": tags,
            "pinned": pinned,
            "top": data["top"],
            "model": data["model"],
            "scenario": data["scenario"],
            "ephemeral": False,
            "saved_at": data["saved_at"],
        }
        self._atomic_json_write(
            os.path.join(SESSIONS_DIR, f"{sid}.json"), sess, compact=True
        )
        self._atomic_json_write(SNAPSHOT_PATH, data, compact=True)
        try:
            if done_cb:
                done_cb()  # 后台线程复位 _snapshot_writing（纯 Python 布尔赋值，线程安全）
        except Exception:
            pass

    def save_snapshot(self):
        if self.cfg.get("privacy_mode") or self._current.get("ephemeral"):
            return
        try:
            sid, data = self._snapshot_assets()
            self._write_snapshot_files(
                sid,
                data,
                self._current.get("name"),
                self._current.get("tags") or [],
                self._current.get("pinned") or [],
            )
        except Exception:
            logging.exception("会话快照保存失败")

    def _safe_sid(self, sid):
        """净化会话 ID：仅允许 [0-9a-zA-Z_-]，杜绝快照/会话文件中的恶意 id 逃逸 SESSIONS_DIR。"""
        return re.sub(r"[^0-9a-zA-Z_-]", "", str(sid or ""))

    def _session_id(self, session):
        if not session.get("id"):
            import uuid

            session["id"] = uuid.uuid4().hex[:12]
        return self._safe_sid(session["id"])

    def save_session_to_file(self, session):
        """把会话完整写入 SESSIONS_DIR/<id>.json（懒加载的数据源）。

        消息列表先在主线程浅拷贝快照（防后台线程写盘期间被 worker 追加），
        序列化 + 写盘移入后台线程，切会话不再冻结 UI。
        """
        if session.get("ephemeral"):
            return
        if not session.get("messages"):
            return
        try:
            sid = self._session_id(session)
            data = {
                "id": sid,
                "name": session.get("name"),
                "messages": list(session["messages"]),
                "usage_total": dict(session.get("usage_total") or {}),
                "stars": session.get("stars") or [],
                "tags": session.get("tags") or [],
                "pinned": session.get("pinned") or [],
                "top": bool(session.get("top")),
                "model": self.cfg.get("model") or "deepseek-v4-flash",
                "scenario": self.cfg.get("scenario") or "通用",
                "ephemeral": bool(session.get("ephemeral")),
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            }
            threading.Thread(
                target=self._write_session_async, args=(sid, data), daemon=True
            ).start()
        except Exception:
            logging.exception("保存会话文件失败")

    def _write_session_async(self, sid, data):
        """后台线程：会话文件原子写盘（纯 Python，无 Tcl 调用）。"""
        try:
            self._atomic_json_write(
                os.path.join(SESSIONS_DIR, f"{self._safe_sid(sid)}.json"),
                data,
                compact=True,
            )
        except Exception:
            logging.exception("保存会话文件失败")

    def delete_session_file(self, sid):
        try:
            sid = self._safe_sid(sid)
            path = os.path.join(SESSIONS_DIR, f"{sid}.json")
            if os.path.exists(path):
                os.remove(path)
                return True
        except Exception:
            logging.exception("删除会话文件失败")
        return False

    def list_saved_sessions(self):
        """扫描历史会话文件，返回轻量元信息列表。

        注意：为拿到消息数与首条预览，仍需解析每个 JSON 文件；调用方应在
        后台线程执行（如 show_history_sessions / search_all_sessions）。
        """
        items = []
        if not os.path.isdir(SESSIONS_DIR):
            return items
        for fn in os.listdir(SESSIONS_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(SESSIONS_DIR, fn), "r", encoding="utf-8") as f:
                    data = json.load(f)
                msgs = data.get("messages") or []
                preview = ""
                for mm in msgs:
                    if mm.get("role") == "user" and mm.get("content"):
                        preview = " ".join(mm["content"].split())[:30]
                        break
                items.append(
                    {
                        "id": str(data.get("id") or fn[:-5]),
                        "name": str(data.get("name") or "") or "未命名会话",
                        "count": len(msgs),
                        "preview": preview,
                        "updated_at": str(data.get("saved_at") or ""),
                        "ephemeral": bool(data.get("ephemeral")),
                    }
                )
            except Exception:
                continue
        items.sort(key=lambda x: x["updated_at"], reverse=True)
        return items

    def load_session_from_file(self, sid):
        """按需从文件载入完整会话，创建为新会话并切换。"""
        sid = self._safe_sid(sid)
        path = os.path.join(SESSIONS_DIR, f"{sid}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            logging.exception("载入历史会话失败")
            return None
        session = self._add_session()
        session["id"] = sid
        session["name"] = str(data.get("name") or "") or None
        # json.load 产物无共享引用，直接复用；dict 拷贝破坏 tokens 缓存并翻倍内存峰值
        session["messages"] = data.get("messages") or []
        session["usage_total"].update(data.get("usage_total") or {})
        stars = data.get("stars")
        if isinstance(stars, list):
            session["stars"] = [dict(s) for s in stars if isinstance(s, dict)]
        tags = data.get("tags")
        if isinstance(tags, list):
            session["tags"] = [str(t) for t in tags if str(t).strip()]
        pinned = data.get("pinned")
        if isinstance(pinned, list):
            session["pinned"] = [str(p) for p in pinned if str(p)]
        session["ephemeral"] = bool(data.get("ephemeral"))
        session["top"] = bool(data.get("top"))
        if not session["messages"] or session["messages"][0].get("role") != "system":
            session["messages"] = [{"role": "system", "content": self.cfg["system_prompt"]}]
        session["first_user"] = next(
            (mm.get("content") for mm in session["messages"] if mm.get("role") == "user"),
            None,
        )
        self._show_session_text(session)
        self.rebuild_view_from_messages()
        self._refresh_session_list()
        self.update_status()
        self._update_context_bar()
        note = f"[历史会话] 已从本地库载入会话「{self._session_display_name(session)}」。\n"
        self._append(note, "time")
        self.blocks.append(("note", note))
        self._append("\n")
        self.blocks.append(("plain", "\n"))
        return session

    def show_history_sessions(self):
        t = self._theme()
        state = {"items": []}
        dialog, body, footer = self._dialog_shell(
            "历史会话库", 580, 460,
            subtitle="所有会话按需落盘，点击载入完整消息；Ctrl/Shift 多选可批量删除",
        )
        listbox = tk.Listbox(
            body,
            width=66,
            selectmode="extended",
            bg=t["input_bg"],
            fg=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
            exportselection=False,
        )
        listbox.pack(fill="both", expand=True)
        listbox.insert("end", "正在加载历史会话…")
        hint = self._lbl(
            body,
            "提示：Ctrl/Shift 或鼠标框选可多选，支持批量删除",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9),
        )
        hint.pack(anchor="w", pady=(4, 0))

        def reload():
            listbox.delete(0, "end")
            listbox.insert("end", "正在加载历史会话…")

            def worker():
                try:
                    state["items"] = self.list_saved_sessions()
                except Exception:
                    state["items"] = []
                    logging.exception("扫描历史会话失败")
                self._ui_queue.put(("history_loaded", (dialog, listbox, state)))

            threading.Thread(target=worker, daemon=True).start()

        def load_selected():
            items = state["items"]
            sel = listbox.curselection()
            if not sel or not items:
                return
            if len(sel) > 1:
                messagebox.showinfo("提示", "载入仅支持单选，请只选择一个会话。")
                return
            it = items[sel[0]]
            if self._current.get("id") == it["id"]:
                messagebox.showinfo("提示", "该会话已在当前列表中。")
                return
            if self.busy:
                messagebox.showinfo("提示", "请先停止当前生成。")
                return
            if self.load_session_from_file(it["id"]):
                dialog.destroy()

        def toggle_select_all():
            items = state["items"]
            if not items:
                return
            if listbox.curselection() and len(listbox.curselection()) == len(items):
                listbox.selection_clear(0, "end")
            else:
                listbox.selection_set(0, "end")

        def delete_selected():
            items = state["items"]
            sel = listbox.curselection()
            if not sel or not items:
                return
            if len(sel) == 1:
                it = items[sel[0]]
                if not messagebox.askyesno(
                    "删除历史会话", f"确认删除会话「{it['name']}」？"
                ):
                    return
            else:
                if not messagebox.askyesno(
                    "批量删除",
                    f"确认删除选中的 {len(sel)} 个历史会话？此操作不可恢复。",
                ):
                    return
            deleted = 0
            for idx in reversed(sel):
                it = items[idx]
                if self.delete_session_file(it["id"]):
                    deleted += 1
            self._flash_status(f"已删除 {deleted} 个历史会话")
            reload()

        listbox.bind("<Double-1>", lambda e: load_selected())
        self._footer_btn(footer, "关闭", dialog.destroy)
        self._footer_btn(footer, "批量删除", delete_selected)
        self._footer_btn(footer, "全选/取消", toggle_select_all)
        self._footer_btn(footer, "载入选中", load_selected, primary=True)

        reload()

    def _show_history_loaded(self, dialog, listbox, state):
        try:
            dialog.winfo_exists()
        except tk.TclError:
            return
        items = state["items"]
        listbox.delete(0, "end")
        for it in items:
            label = f"{it['name']}（{it['count']} 条 · {it['updated_at'][:16]}）"
            if it["preview"]:
                label += f"\n   {it['preview']}"
            listbox.insert("end", label)
        if not items:
            listbox.insert("end", "（暂无历史会话，会话会在对话过程中自动保存）")

    def _maybe_save_snapshot(self):
        """快照惰性落盘：发送/完成后的高频调用只调度，停止输入 10s 后真正写盘。

        长会话 messages 序列化 + 原子替换是主线程重操作（601 条 ~100ms+），
        每轮都写会让长会话操作期间的主线程反复卡顿。10s 空闲窗口内多次
        触发只写一次；退出（on_close）时必定立即写，数据不丢。
        """
        if getattr(self, "_snapshot_after", None) is not None:
            try:
                self.root.after_cancel(self._snapshot_after)
            except Exception:
                pass
        self._snapshot_after = self.root.after(
            int(SNAPSHOT_INTERVAL_S * 1000 * 5), self._save_snapshot_now
        )

    def _save_snapshot_now(self):
        self._snapshot_after = None
        try:
            # 无实际变更（仅进入空闲而未发送/未完成）时跳过双文件序列化写盘
            if not getattr(self, "_snapshot_dirty", True):
                return
            if getattr(self, "_snapshot_writing", False):
                # 上一轮写盘仍在进行（长会话序列化可能 >100ms）：保持脏位，
                # 10s 后再试——防两个写盘线程并发写同一文件互相覆盖
                self._maybe_save_snapshot()
                return
            self._snapshot_writing = True
            sid, data = self._snapshot_assets()  # 主线程取值/浅拷贝

            def _written():
                self._snapshot_writing = False

            threading.Thread(
                target=self._write_snapshot_files,
                args=(
                    sid,
                    data,
                    self._current.get("name"),
                    self._current.get("tags") or [],
                    self._current.get("pinned") or [],
                    _written,
                ),
                daemon=True,
            ).start()  # 序列化 + 写盘在后台线程，主线程零阻塞
            self._snapshot_dirty = False
        except Exception:
            self._snapshot_writing = False
            logging.exception("快照惰性落盘失败")

    def _restore_snapshot(self):
        if self.cfg.get("privacy_mode"):
            return
        if not self.cfg.get("restore_session", True):
            return
        if not os.path.exists(SNAPSHOT_PATH):
            return
        try:
            with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            msgs = data.get("messages")
            if not isinstance(msgs, list) or not msgs or msgs[0].get("role") != "system":
                return
            # json.load 产物无共享引用，直接复用；dict 拷贝会破坏 tokens 缓存并翻倍内存峰值
            self.messages = msgs
            self.usage_total.update(data.get("usage_total") or {})
            stars = data.get("stars")
            if isinstance(stars, list):
                self._current["stars"] = [dict(s) for s in stars if isinstance(s, dict)]
            # 快照携带完整会话身份与元数据：id 保留（防旧会话文件残留）、
            # 名称/标签/固定消息恢复（早期快照可能缺字段，逐项判空）
            if data.get("id"):
                self._current["id"] = str(data["id"])
            if data.get("name"):
                self._current["name"] = str(data["name"])
            tags = data.get("tags")
            if isinstance(tags, list):
                self._current["tags"] = [str(t) for t in tags if str(t).strip()]
            pinned = data.get("pinned")
            if isinstance(pinned, list):
                self._current["pinned"] = [str(p) for p in pinned if str(p)]
            self._current["top"] = bool(data.get("top"))
            self.session_start = datetime.now()
            self.rebuild_view_from_messages()
            first_user = next(
                (mm.get("content") for mm in msgs if mm.get("role") == "user"), None
            )
            self._current["first_user"] = first_user or None
            if first_user and not self._current.get("name"):
                self._current["name"] = " ".join(first_user.split())[:18]
            self._invalidate_session_name(self._current)
            self._refresh_session_list()
            note = f"[会话恢复] 已恢复上次会话（{len(msgs)} 条消息）。\n"
            self._append(note, "time")
            self.blocks.append(("note", note))
            self.update_status()
            self._update_context_bar()
            logging.info("已从快照恢复会话：%s 条消息", len(msgs))
        except Exception:
            logging.exception("会话快照恢复失败")

    def new_conversation(self, export_old=True):
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        self._cancel_paged_render()
        if (
            export_old
            and len(self.messages) > 1
            and not self.cfg.get("privacy_mode")
        ):
            self.export_history(ask_dir=False)
        self.messages = [{"role": "system", "content": self.cfg["system_prompt"]}]
        self.usage_total = {"prompt": 0, "completion": 0, "cache_hit": 0, "cache_miss": 0}
        self._ctx_counts = None  # 新会话重新计数
        self.last_usage = None
        self._pending_send = None
        self._needs_compression = False
        self._round_aborted = False
        self._resend_index = None
        self._stream_start = None
        self._stream_block_start = None
        self.blocks = CappedList()
        self.session_start = datetime.now()
        self._follow_bottom = True
        # 清空当前 text 的折叠/链接/文件链接记录，防止死条目跨会话累积
        self._fold_ranges[self.chat_text] = []
        self._fold_nums[self.chat_text] = []
        self._link_ranges[self.chat_text] = []
        self._filelink_ranges[self.chat_text] = []
        self._stream_thinking_fold = None
        self.chat_text.configure(state="normal")
        self.chat_text.delete("1.0", "end")
        self.chat_text.configure(state="disabled")
        self._append("新会话已开始。输入问题，Enter 发送，Shift+Enter 换行。\n", "time")
        self.blocks.append(("note", "新会话已开始。输入问题，Enter 发送，Shift+Enter 换行。\n"))
        self._append("\n")
        self.blocks.append(("plain", "\n"))
        self.update_status()
        self._update_context_bar()

    def _monthly_cost(self, force=False):
        now = time.monotonic()
        if (
            self._monthly_cost_time is None
            or force
            or now - self._monthly_cost_time > 30
        ):
            data = stats.load_stats(STATS_PATH)
            month_key = date.today().strftime("%Y-%m")
            cost = 0.0
            for day, models in data.items():
                if day.startswith(month_key):
                    for model, usage in models.items():
                        cost += stats.estimate_cost(usage, model)
            self._monthly_cost_cache = cost
            self._monthly_cost_time = now
        return self._monthly_cost_cache

    def edit_budget(self):
        dialog, body, footer = self._dialog_shell(
            "预算设置", 380, 220,
            subtitle="状态栏实时显示本月费用：接近预算变黄 ⚠，超限变红 ⛔",
        )
        self._lbl(body, "月度预算（元，0 = 不限）", role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(anchor="w")
        budget_var = tk.StringVar(value=f"{float(self.cfg.get('monthly_budget', 0) or 0):.2f}")
        ttk.Spinbox(
            body, from_=0.0, to=10000.0, increment=10.0, textvariable=budget_var,
            format="%.2f", width=14,
        ).pack(anchor="w", pady=(6, 12))
        block_var = tk.BooleanVar(value=bool(self.cfg.get("block_on_budget", False)))
        ttk.Checkbutton(body, text="达到预算后阻止发送", variable=block_var).pack(anchor="w")

        def save():
            try:
                budget = max(0.0, float(budget_var.get()))
            except (TypeError, ValueError):
                budget = 0.0
            self.cfg["monthly_budget"] = budget
            self.cfg["block_on_budget"] = bool(block_var.get())
            save_config(self.cfg)
            self.update_status()
            dialog.destroy()

        self._footer_btn(footer, "取消", dialog.destroy)
        self._footer_btn(footer, "保存", save, primary=True)

    def send(self, text=None, silent=False):
        """发送消息。silent=True（定时任务/Webhook 触发）时无 API Key/预算拦截
        弹窗降级为状态栏提示，避免无人值守场景弹出阻塞性对话框。"""
        if text is None:
            self._clear_placeholder()
            text = self.input_text.get("1.0", "end").strip()
        if not text:
            self._flash_status("输入为空，未发送")
            return
        if self.busy:
            self._pending_send = text
            self.input_text.delete("1.0", "end")
            if self.stop_event:
                self.stop_event.set()
            note = "[已打断当前生成，结束后自动发送新消息]\n"
            self._append(note, "time")
            self.blocks.append(("note", note))
            return
        if self._paged_render is not None:
            # 分帧渲染进行中（长会话刚恢复）：同步补全会冻结 UI 0.5-数秒，
            # 改为挂起待发，分帧完成后自动发送
            self._send_when_ready = text
            self._flash_status("正在完成会话渲染，稍后自动发送…", 2000)
            return
        cfg = self.save_widgets_to_config()
        self._capture_client_params()
        if not cfg["api_key"]:
            if silent:
                self._flash_status("⚠ 未配置 API Key，定时任务无法执行", 4000)
                self._show_note("[定时任务] 未配置 API Key，任务未执行。\n")
            else:
                messagebox.showwarning(
                    "未配置 API Key",
                    "请先在顶部填写 DeepSeek API Key（可在 https://platform.deepseek.com 申请），"
                    "配置会自动保存到 config.json。",
                )
            return
        if cfg.get("block_on_budget") and float(cfg.get("monthly_budget", 0) or 0) > 0:
            cost = self._monthly_cost()
            if cost >= float(cfg["monthly_budget"]):
                if silent:
                    self._flash_status(f"⛔ 已达月度预算 ¥{cost:.2f}，任务未发送", 4000)
                    self._show_note("[定时任务] 已达月度预算，任务未执行。\n")
                else:
                    messagebox.showwarning(
                        "已达月度预算",
                        f"本月已花费 ¥{cost:.2f}，达到预算上限，已阻止发送。\n"
                        "可在「工具 → 预算设置」调整预算或关闭拦截。",
                    )
                return
        if (
            cfg.get("peak_warning")
            and is_peak_hour()
            and self._peak_notified != date.today().isoformat()
        ):
            self._peak_notified = date.today().isoformat()
            self._flash_status(
                "⏰ 当前为 DeepSeek 高峰时段（9:00-12:00 / 14:00-18:00），"
                "按高峰价计费（空闲时段为高峰一半）",
                6000,
            )
        self.input_text.delete("1.0", "end")
        if self._resend_index is not None:
            del self.messages[self._resend_index :]
            self._resend_index = None
            self._needs_compression = False
            self.rebuild_view_from_messages()
            note = "[编辑重发] 已替换原消息，正在重新生成回复。\n"
            self._append(note, "time")
            self.blocks.append(("note", note))
        self._append_message_block("我", text, "user")
        session_hist = self._current.setdefault("sent_history", [])
        if not session_hist or session_hist[-1] != text:
            session_hist.append(text)
            if len(session_hist) > 200:
                del session_hist[: len(session_hist) - 200]
        self._hist_index = None
        self.messages.append({"role": "user", "content": text})
        self._maybe_auto_name(text)
        # 相关文件读取与 token 全量估算移入 worker 线程：恢复长会话后的首次发送
        # 在主线程做 tiktoken 全量编码会冻结 UI 1-2s
        self._inject_text = text
        self._current_inject_text = ""
        self._ctx_counts = None
        self._needs_compression = False
        self._compression_note_shown = False
        self._snapshot_dirty = True
        self.assistant_answered = False
        self._set_busy(True)
        self._update_context_bar()
        self._maybe_save_snapshot()
        self.stop_event = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    def _resolve_thinking(self, cfg):
        """生效思考档位：预算感知降档（auto/max → high，接近预算 80% 时）。"""
        thinking = str(cfg.get("thinking") or "high")
        budget = float(cfg.get("monthly_budget", 0) or 0)
        if budget > 0:
            try:
                cost = self._monthly_cost()
            except Exception:
                cost = 0.0
            eff, near = self._budget_thinking(budget, cost, thinking)
            if eff != thinking:
                self._budget_thinking_hint(near)
            return eff
        return thinking

    def _worker(self, continue_mode=False):
        try:
            client = self.ensure_client()
            cfg = self.cfg
            pure = bool(cfg.get("pure_chat", False))
            self._ui_queue.put(("begin", continue_mode))
            # 计算已在 worker 线程执行：token 全量估算 / 相关文件读取 / 压缩判定，
            # 恢复长会话后的首次发送不再冻结主线程（tiktoken 编码可卡 1-2s）
            if self._ctx_counts is None:
                self._ctx_counts = tokens.message_token_counts(self.messages)
            self._current_inject_text = self._relevant_files_text(
                getattr(self, "_inject_text", "") or ""
            )
            if self._needs_compression or self._context_over_limit():
                self._needs_compression = True
                if not self._compression_note_shown:
                    self._compression_note_shown = True
                    self._ui_queue.put(
                        ("info", "[上下文压缩] 上下文接近上限，将先总结历史再继续。\n")
                    )
                self._compress_old_history()
            else:
                trimmed = self._trim_context(counts=self._ctx_counts)
                if trimmed:
                    note = f"[上下文压缩] 上下文过大，已移除最早 {trimmed} 轮对话。\n"
                    self._ui_queue.put(("info", note))
            msgs = self.messages
            if pure:
                # 纯对话模式：请求层使用对话人格（不污染会话中的系统提示词）
                msgs = [dict(x) for x in msgs]
                if msgs and msgs[0].get("role") == "system":
                    msgs[0]["content"] = DIALOG_SYSTEM_PROMPT
            # 自主模式 = 任务能力（运行时语义，不污染 enabled_tools 配置）：
            # 完全智能 = 全部工具（为开发/创作而生）；纯对话 = 无工具；标准 = 按工具中心配置
            enabled_tools, tools_enabled = self._mode_tools_for_request(cfg)
            seed = None
            if self._variant_seed_override is not None:
                seed = self._variant_seed_override
            elif cfg["seed"]:
                try:
                    seed = int(cfg["seed"])
                except ValueError:
                    seed = None
            ok_result = client.chat(
                msgs,
                scenario=cfg["scenario"],
                thinking=self._resolve_thinking(cfg),
                max_tokens=int(cfg["max_tokens"]) or 16384,
                seed=seed,
                tools_enabled=tools_enabled,
                enabled_tools=enabled_tools,
                custom_tools=load_user_tools(USER_TOOLS_PATH),
                max_tool_rounds=int(cfg.get("max_tool_rounds", 10)),
                on_reasoning=self._push_reasoning,
                on_content=self._push_content,
                on_tool=self._push_tool,
                on_tool_duration=self._push_tool_duration,
                on_loop_guard=self._push_loop_guard,
                on_usage=self._push_usage,
                on_approval=self._tool_approval,
                on_plan=self._plan_gate,
                memory_text=self._memory_prompt_text(),
                trailing_text=(
                    "" if pure else self._memory_prompt_dynamic()
                ),
                pure_chat=pure,
                strict_tools=bool(cfg.get("strict_tools", False)),
                stop_event=self.stop_event,
                temperature=(
                    float(cfg.get("custom_temperature", 1.0))
                    if cfg["scenario"] == "自定义" else None
                ),
                top_p=(
                    float(cfg.get("custom_top_p", 1.0))
                    if cfg["scenario"] == "自定义" else None
                ),
                json_output=bool(cfg.get("json_output", False)),
                continue_prefix=bool(continue_mode),
                on_ask=self._ask_user_callback,
                on_request_permission=self._request_whitelist_callback,
                on_truncated=self._push_truncated,
            )
            # chat() 返回 False 且非用户停止 = 空响应重试耗尽 / 计划连续被拒 / 流中断截断，
            # 给出可见反馈（on_truncated 已通知原因时不再重复）
            if ok_result is not None and not ok_result and not (
                self.stop_event and self.stop_event.is_set()
            ):
                self._ui_queue.put(("info", "本轮生成未能取得结果（空响应或计划被拒），请稍后重试。"))
            if pure:
                # 把请求副本中新增的 assistant 消息同步回会话（chat 原地修改的是副本）
                base = len(self.messages)
                if len(msgs) > base:
                    self.messages.extend(msgs[base:])
        except Exception as e:
            if self.stop_event and self.stop_event.is_set():
                # 用户已停止生成：不显示"请求失败"（_finish 会提示已停止）
                logging.info("生成已停止：%s", e)
            elif getattr(e, "status_code", None) == 402:
                self._ui_queue.put(
                    ("error", f"余额不足 (402)：请点击工具栏的查余额按钮查看并充值后再试。\n{e}")
                )
            else:
                logging.exception("请求失败")
                self._ui_queue.put(("error", f"请求失败: {e}"))
                # 异常中断：标记本轮未完成，_finish 的任务报告显示「任务中断」而非「任务完成」
                self._ui_queue.put(("round_error", None))
        finally:
            self._ui_queue.put(("finish", None))

    def _begin_assistant(self, resume=False):
        # 新一轮生成开始：无论此前浏览位置，强制回到底部跟随（发送=新交互）
        self._follow_bottom = True
        # _finish 总会追加 ("plain","\n") 结尾，blocks[-1] 恒为 plain，
        # 续写判定须跳过 plain 找最近的实质块
        last_real = (
            next((b for b in reversed(self.blocks) if b and b[0] != "plain"), None)
            if resume
            else None
        )
        if last_real is not None and last_real[0] == "content":
            self._stream_start = self.chat_text.index("end-1c")
            self._stream_block_start = len(self.blocks) - 1
        else:
            now = datetime.now().strftime("%H:%M:%S")
            header = f"[{now}] 助手\n"
            self._append(header, "time")
            self.blocks.append(("note", header))
            self._stream_start = self.chat_text.index("end-1c")
            self._stream_block_start = len(self.blocks)
        self._agent_tool_count = 0
        self._last_stream_activity = time.monotonic()
        self._stream_begin = time.monotonic()
        self._thinking_received = False
        self._stream_thinking_fold = None
        if self.task_panel is None:
            try:
                self.task_panel = TaskPanel(
                    self.root, on_stop=self.stop_generate, theme=self._theme()
                )
            except Exception:
                self.task_panel = None
        if self.task_panel is not None:
            try:
                self.task_panel.prepare()
            except Exception:
                pass

    def _push_reasoning(self, text):
        self._ui_queue.put(("reasoning", text))

    def _push_content(self, text):
        self._ui_queue.put(("content", text))

    def _push_tool(self, name, args, result):
        self._ui_queue.put(("tool", (name, args, result)))
        # 自动断点：工具链每完成一步即持久化检查点（worker 线程纯写盘），
        # 停止/崩溃/重启后可从断点一键续跑（_finish 中断时插入续跑入口）
        try:
            self._auto_checkpoint(name)
        except Exception:
            pass

    def _auto_checkpoint(self, tool_name=None):
        """工具链执行中断点自动持久化（worker 线程调用；隐私模式跳过）。"""
        if self.cfg.get("privacy_mode"):
            return
        tools = getattr(self, "_auto_ckpt_tools", None)
        if tools is None:
            return
        if tool_name and (not tools or tools[-1] != tool_name):
            tools.append(tool_name)
        title = ""
        for mm in reversed(self.messages):
            if mm.get("role") == "user" and mm.get("content"):
                title = " ".join(str(mm["content"]).split())[:40]
                break
        try:
            _dc.task_checkpoint_save(
                name=title or "未命名任务",
                status="进行中",
                pending=[f"已完成 {len(tools)} 个工具（最近：{'、'.join(tools[-5:])}）"],
                notes=f"[自动断点] 工具链：{' → '.join(tools[-8:])}",
                auto=True,
            )
        except Exception:
            pass

    def _push_tool_duration(self, name, duration):
        self._ui_queue.put(("tool_dur", (name, duration)))

    def _push_loop_guard(self, name, repeats):
        self._ui_queue.put(("loop_guard", (name, repeats)))

    def _push_usage(self, usage):
        self._ui_queue.put(("usage", usage))

    def _push_truncated(self, reason):
        """生成截断/中断通知（worker 线程）：标记本轮未完成，_finish 展示明确提示。"""
        try:
            self._round_aborted = True
            self._ui_queue.put(("truncated", str(reason)))
        except Exception:
            pass

    def _drain_ui_queue(self):
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "reasoning":
                    self._pending["thinking"] += payload
                    self._thinking_received = True
                    self._last_stream_activity = time.monotonic()
                elif kind == "content":
                    self._pending["content"] += payload
                    self._last_stream_activity = time.monotonic()
                elif kind == "tool":
                    self._pending_tools.append(payload)
                    self._agent_tool_count += 1
                    self._last_stream_activity = time.monotonic()
                    if self.busy:
                        try:
                            tname = payload[0]
                            tres = payload[2]
                        except Exception:
                            tname, tres = "工具", ""
                        self.status_label.configure(
                            text=f"⚙ 正在执行「{tname}」（第 {self._agent_tool_count} 个）…"
                        )
                        if str(tres).startswith(_dc.TOOL_RESULT_FAIL_PREFIXES):
                            self._flash_status(f"⚠ 工具「{tname}」执行失败，AI 正在修正…", 4000)
                        if self.task_panel is not None:
                            self.task_panel.add_tool(tname, tres)
                elif kind == "tool_dur":
                    self._pending_tool_durations.append(payload)
                elif kind == "loop_guard":
                    name, repeats = payload
                    note = (
                        f"[工具循环防护] 检测到 {name} 连续重复调用 {repeats} 次，"
                        "已终止工具循环，请检查 Agent 行为。\n"
                    )
                    self._show_note(note)
                elif kind == "ask":
                    prompt, ev, box = payload
                    self._show_ask_dialog(prompt, ev, box)
                elif kind == "whitelist_req":
                    action_type, value, ev, box = payload
                    self._show_whitelist_dialog(action_type, value, ev, box)
                elif kind == "usage":
                    self._apply_usage(payload)
                elif kind == "remap_idx":
                    self._remap_blocks_after_compress(payload)
                elif kind == "begin":
                    self._begin_assistant(bool(payload))
                elif kind == "error":
                    self._show_error(payload)
                elif kind == "balance":
                    self._show_balance(payload)
                elif kind == "info":
                    self._show_note(payload)
                elif kind == "truncated":
                    # 截断/中断原因（_push_truncated 已置 _round_aborted；此处补充可见提示）
                    if payload:
                        self._show_note(f"⚠ {payload}。本轮生成未正常完成，可重新发送或继续生成。")
                elif kind == "round_error":
                    self._round_aborted = True
                elif kind == "balance_done":
                    self.btn_balance.configure(state="normal")
                elif kind == "update":
                    self._handle_update_result(payload)
                elif kind == "update_downloaded":
                    ok, detail = payload
                    if ok:
                        if messagebox.askyesno(
                            "下载完成",
                            f"新版安装包已下载：\n{detail}\n\n"
                            "关闭鲸语后运行该文件即可完成更新。\n打开所在文件夹？",
                        ):
                            try:
                                os.startfile(os.path.dirname(detail))
                            except Exception:
                                pass
                    else:
                        messagebox.showerror(
                            "下载失败", f"更新包下载失败：{detail}\n可前往官网手动下载。"
                        )
                elif kind == "search_all_done":
                    self._show_search_results(payload)
                elif kind == "history_loaded":
                    self._show_history_loaded(*payload)
                elif kind == "knowledge_done":
                    self._on_knowledge_done(payload)
                elif kind == "summary_done":
                    self._on_summary_done(payload)
                elif kind == "export_done":
                    self._show_export_done(payload)
                elif kind == "approval_req":
                    self._show_approval_dialog(*payload)
                elif kind == "plan_req":
                    self._show_plan_dialog(*payload)
                elif kind == "ocr":
                    self._ocr_result(payload)
                elif kind == "timer_task":
                    self.send(text=payload, silent=True)
                elif kind == "schedule_notify":
                    self._flash_status(f"⏰ {payload}", 6000)
                    # Webhook 推送是同步 HTTP（最长 10s），放后台线程防主线程卡顿
                    def _push():
                        try:
                            import deepseek_client as _dc2
                            _dc2.send_webhook_notify(payload)
                        except Exception:
                            pass

                    threading.Thread(target=_push, daemon=True).start()
                elif kind == "proc_out":
                    self._proc_panel_append(*payload)
                elif kind == "tray":
                    try:
                        if payload == "show":
                            self.root.deiconify()
                            self.root.lift()
                            self.root.focus_force()
                        elif payload == "hide":
                            self.root.withdraw()
                        elif payload == "quit":
                            self._quit_from_tray = True
                            self.on_close()
                    except tk.TclError:
                        pass
                elif kind == "fim_done":
                    ok, res = payload
                    w = getattr(self, "_fim_widgets", None)
                    if w is not None:
                        rt, sl, th = w
                        try:
                            if rt.winfo_exists():
                                rt.delete("1.0", "end")
                                if ok:
                                    rt.insert("1.0", res)
                                sl.configure(
                                    text=("完成（最大补全 4K）" if ok else f"失败: {res}"),
                                    fg=th["error"] if not ok else th["text_sec"],
                                )
                        except tk.TclError:
                            pass
                elif kind == "finish":
                    try:
                        self._finish()
                    except Exception:
                        # _finish 中途异常（如渲染索引错误）也不能让 busy 卡死：
                        # 否则发送/停止/切换全部被 busy 守卫拦截，只能重启
                        logging.exception("生成结束处理异常")
                        try:
                            self._set_busy(False)
                        except Exception:
                            pass
        except queue.Empty:
            pass

    def _poll_ui(self):
        try:
            self._drain_ui_queue()
            self._flush_pending()
            if self.busy and self._stream_start is not None:
                idle = time.monotonic() - self._last_stream_activity
                if idle > STREAM_IDLE_WARNING_S:
                    self.status_label.configure(
                        text=(
                            f"⏳ 等待模型响应中…（已 {int(idle)} 秒无新内容，"
                            "可点击「■ 停止」中止或稍后自动重试）"
                        )
                    )
                elif (
                    not self._thinking_received
                    and not self._pending["content"]
                    and not self._pending_tools
                    and time.monotonic() - self._stream_begin > 0.5
                ):
                    self.status_label.configure(text="🤔 思考中…")
        except Exception:
            # 单次异常不能杀死轮询链：否则队列中的 finish/流式内容永远无人消费，
            # busy 恒为 True，界面假死只能重启
            logging.exception("UI 轮询异常（已忽略并继续）")
        if self.busy and time.monotonic() - self._last_stream_activity > 2.0:
            delay = 250  # 流式静默期（长思考/无增量）降频：不再 25Hz 空轮询
        else:
            delay = FLUSH_INTERVAL_MS if (self.busy or not self._ui_queue.empty()) else POLL_IDLE_MS
        try:
            self._poller_id = self.after(delay, self._poll_ui)
        except Exception:
            pass

    def _flush_pending(self, force=False):
        text = self.chat_text
        if self._pending["thinking"]:
            # 思考卡片即将插入：先固化回复尾部（不再逐帧重绘），
            # 避免后续删除尾部时移动已插入卡片的折叠索引
            self._stream_tail = ""
            self._stream_tail_rng = None
            self._stream_tail_links = []
            chunk = self._pending["thinking"]
            self._pending["thinking"] = ""
            if self.blocks and self.blocks[-1][0] == "thinking":
                self.blocks[-1] = ("thinking", self.blocks[-1][1] + chunk)
            else:
                self.blocks.append(("thinking", chunk))
            # 渲染：有进行中的思考卡片则增量追加，否则新建折叠卡片（默认展开实时可见）
            text.configure(state="normal")
            fold = self._stream_thinking_fold
            if fold is not None:
                try:
                    text.insert(fold["p1"], chunk, (fold["style"],))
                    old_p1 = fold["p1"]
                    # 每次 flush 解析回绝对索引：否则 "10.5+8c+12c+…" 链式索引无界增长
                    fold["p1"] = text.index(f"{fold['p1']}+{len(chunk)}c")
                    # 折叠态下新插入的增量也要纳入隐藏范围，否则流式内容「漏」到卡片外
                    if not fold.get("visible", True):
                        text.tag_add("fold_hidden", old_p1, fold["p1"])
                except tk.TclError:
                    fold = None
                    self._stream_thinking_fold = None
            if fold is None:
                self._insert_fold(text, "思考过程", chunk, "thinking", True, "end")
                try:
                    self._stream_thinking_fold = self._fold_ranges[text][-1]
                except (IndexError, KeyError):
                    self._stream_thinking_fold = None
            text.configure(state="disabled")
            self._ensure_follow()
        if self._pending["content"] or (force and self._stream_tail):
            # 内容到来即结束进行中的思考卡片
            self._stream_thinking_fold = None
            buf = self._pending["content"]
            self._pending["content"] = ""
            msg_idx = None
            if self.messages and self.messages[-1].get("role") == "assistant":
                msg_idx = len(self.messages) - 1
            if self.blocks and self.blocks[-1][0] == "content":
                prev = self.blocks[-1]
                idx = prev[2] if len(prev) > 2 and prev[2] is not None else msg_idx
                self.blocks[-1] = ("content", prev[1] + buf, idx)
            else:
                self.blocks.append(("content", buf, msg_idx))
            # 流式 Markdown 渲染：按"段落边界"渲染增量，而不是按输出 chunk。
            # 完整段落（空行/块边界收尾）立即按 Markdown 渲染；正在书写的
            # 最后一个段落保留为"尾部"（实时显示、逐帧重绘），等段落闭合后
            # 整体按 Markdown 渲染，杜绝 chunk 边界把一句话拆成多行/多段。
            # 含未闭合标记（围栏/粗体/半角括号）时尾部按原文显示而非隐藏，
            # 生成结束由 _finish(force=True) 强制补渲染，内容永不丢失。
            if self._md_render:
                tail_raw = (self._stream_tail or "") + buf
                if force:
                    stable_raw, tail_raw, deferred = tail_raw, "", False
                elif _stream_defer_needed(tail_raw):
                    stable_raw, tail_raw, deferred = "", tail_raw, True
                else:
                    stable_raw, tail_raw = _stream_tail_split(tail_raw)
                    deferred = False
                text.configure(state="normal")
                try:
                    if self._stream_tail_rng:
                        rng = self._stream_tail_rng
                        self._stream_tail_rng = None
                        self._drop_tail_links(text)
                        try:
                            text.delete(*rng)
                        except tk.TclError:
                            pass
                    if stable_raw:
                        # 收集代码块（供"复制最近代码块"菜单使用）。
                        # pos 必须用 "end-1c"（末尾换行的真实位置）而非 "end"：
                        # "end" 是虚拟索引，_insert_content 据此计算链接区间
                        # 会得到退化区间（start==end），流式渲染的链接将无法点击
                        self._insert_content(
                            text, stable_raw, "assistant", msg_idx,
                            self._current["last_code_blocks"], "end-1c",
                        )
                        if tail_raw and stable_raw.endswith("\n\n"):
                            # render_markdown 会丢弃段落末尾的空行，导致流式
                            # 渲染的段落边界只剩单个换行；尾部段落存在时补回
                            # 空行，保证与全量重渲染逐字一致
                            text.insert("end", "\n", "assistant")
                    if tail_raw:
                        # 尾部实时可见：未闭合标记时按原文插入；否则走内联
                        # 渲染（与稳定部分同样保留段落换行，保证全量重渲染
                        # 逐字一致；尾部随流式整段替换，不会累积）。
                        # 注意：必须用 "end-1c"（末尾换行处）作插入点——"end"
                        # 是虚拟索引，插入后 index("end") 不变，会导致尾部
                        # 范围退化（t0==t1），下一帧删除尾部时静默失败。
                        t0 = text.index("end-1c")
                        if deferred:
                            disp = tail_raw
                        else:
                            dtext, spans, links, _cb = mdparse.render_markdown(tail_raw)
                            disp = dtext
                        text.insert(t0, disp, "assistant")
                        if not deferred:
                            for a, b, st in spans:
                                if st == "link":
                                    text.tag_add("link", f"{t0}+{a}c", f"{t0}+{b}c")
                                    continue
                                try:
                                    text.tag_add(st, f"{t0}+{a}c", f"{t0}+{b}c")
                                except tk.TclError:
                                    pass
                            if links:
                                # 尾部链接注册 + 旧尾部链接清理（防残留脏区间）
                                self._drop_tail_links(text)
                                new_links = [
                                    (
                                        _index_num(text.index(f"{t0}+{a}c")),
                                        _index_num(text.index(f"{t0}+{b}c")),
                                        url,
                                    )
                                    for a, b, url in links
                                ]
                                self._link_ranges.setdefault(text, []).extend(new_links)
                                self._stream_tail_links = new_links
                            else:
                                self._stream_tail_links = []
                        self._stream_tail = tail_raw
                        self._stream_tail_rng = (t0, text.index(f"{t0}+{len(disp)}c"))
                    else:
                        self._stream_tail = ""
                        self._stream_tail_links = []
                except Exception:
                    logging.exception("流式 Markdown 渲染失败")
                    self._append(buf, "assistant")
                finally:
                    text.configure(state="disabled")
                self._ensure_follow()
            else:
                self._stream_tail = ""
                self._stream_tail_rng = None
                self._stream_tail_links = []
                self._append(buf, "assistant")
        if self._pending_tools:
            # 工具卡片插入前固化回复尾部（防卡片折叠索引被尾部重绘移动）
            self._stream_tail = ""
            self._stream_tail_rng = None
            self._stream_tail_links = []
            for name, args, result in self._pending_tools:
                duration = None
                # 耗时按名称配对；找不到时保留队列（不再 pop(0) 丢弃，防错配/漏配）
                for i, (d_name, d_secs) in enumerate(self._pending_tool_durations):
                    if d_name == name:
                        duration = d_secs
                        del self._pending_tool_durations[i]
                        break
                self._append_tool(name, args, result, duration)
                self.blocks.append(("tool", (name, args, result, duration)))
                self._record_recent_output(str(result))
            self._pending_tools = []

    def _drop_tail_links(self, text):
        """移除当前流式尾部注册的链接区间（尾部被删除/替换时调用）。"""
        stale = self._stream_tail_links
        self._stream_tail_links = []
        if not stale:
            return
        cur = self._link_ranges.get(text)
        if not cur:
            return
        stale_set = set(stale)
        self._link_ranges[text] = [e for e in cur if e not in stale_set]

    def _append_tool(self, name, args, result, duration=None):
        """工具调用渲染为折叠卡片（与重渲染 _render_block 同一标题规范：
        ✅/❌ + 耗时 + 结果摘要；失败的工具自动展开显示错误）。"""
        try:
            args_str = json.dumps(args, ensure_ascii=False) if args else "{}"
        except (TypeError, ValueError):
            args_str = str(args)
        detail = f"参数: {args_str}\n结果: {result}\n"
        res_text = str(result or "")
        failed = res_text.startswith(_dc.TOOL_RESULT_FAIL_PREFIXES)
        mark = "❌" if failed else "✅"
        title = f"{mark} [工具] {name}"
        if duration is not None:
            title += f" · {duration:.1f}s"
        first = res_text.splitlines()[0] if res_text else ""
        if first:
            title += f" · {first[:44]}"
        self._insert_fold(self.chat_text, title, detail, "tool", failed, "end")
        self._stream_thinking_fold = None
        self._ensure_follow()

    def _apply_usage(self, usage):
        self.last_usage = usage
        for k in ("prompt", "completion", "cache_hit", "cache_miss"):
            self.usage_total[k] += int(usage.get(k, 0) or 0)
        if not self.cfg.get("privacy_mode"):
            model = self.model_combo.get().strip() or "unknown"
            acc = self._pending_stats.setdefault(
                model, {"prompt": 0, "completion": 0, "cache_hit": 0, "cache_miss": 0}
            )
            for k in acc:
                acc[k] += int(usage.get(k, 0) or 0)
            if time.monotonic() - self._last_stats_flush >= 10:
                self._flush_stats()
        self.update_status()

    def _flush_stats(self):
        """把内存累计的用量一次性写入 stats.json（生成结束/退出/节流时调用）。

        所有模型合并为一次读-改-写（stats.record_usage 每个模型各做一次全量
        读-写，多月 stats.json 变大后逐模型写会重复解析多次）。
        """
        if not self._pending_stats:
            return
        try:
            data = stats.load_stats(STATS_PATH)
            today = date.today().isoformat()
            for model, usage in self._pending_stats.items():
                if not any(usage.values()):
                    continue
                acc = data.setdefault(today, {}).setdefault(model, stats.empty_day())
                for k in acc:
                    acc[k] += int(usage.get(k, 0) or 0)
            stats.save_stats(STATS_PATH, data)
        except Exception:
            logging.exception("记录用量统计失败")
        self._pending_stats = {}
        self._last_stats_flush = time.monotonic()

    def _show_error(self, text):
        self._append(text + "\n", "error")
        self.blocks.append(("error", text + "\n"))
        self._append("\n")
        self.blocks.append(("plain", "\n"))

    def _show_note(self, text):
        self._append(text + "\n", "time")
        self.blocks.append(("note", text + "\n"))
        self._append("\n")
        self.blocks.append(("plain", "\n"))

    def _remap_blocks_after_compress(self, last_removed):
        """上下文压缩/硬裁剪在 worker 线程删除了 messages 前段，主线程按偏移
        重映射 UI content 块的 msg_idx：被删区间的消息置 None（禁用右键定位），
        之后的统一减去偏移，防止定位到错误消息（误删/误重发）。"""
        if not last_removed:
            return
        for i, blk in enumerate(self.blocks):
            if blk[0] == "content" and len(blk) > 2 and isinstance(blk[2], int):
                old = blk[2]
                self.blocks[i] = (
                    blk[0],
                    blk[1],
                    None if old <= last_removed else old - last_removed,
                )

    def _offer_continue_from_checkpoint(self):
        """中断后存在自动断点：聊天区插入可点击「从断点继续」。"""
        try:
            if not _dc.CHECKPOINT_FILE or not os.path.exists(_dc.CHECKPOINT_FILE):
                return
            with open(_dc.CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                cp = json.load(f)
            if not isinstance(cp, dict) or not cp.get("auto"):
                return
            name = str(cp.get("name") or "未命名任务")
            note = f"▶ 已保存任务断点「{name}」，点击此处从断点继续（或发送『继续任务』）\n"
            self._append(note, "continue_hint")
            self.blocks.append(("note", note, "continue_hint"))
        except Exception:
            pass

    def _finish(self):
        # 本轮是否被截断/中断（max_tokens 截断 / 流断线 / 工具轮数耗尽 / 异常）：
        # 是则任务报告显示「任务中断」并提供继续路径，避免误报「任务完成」
        aborted = bool(getattr(self, "_round_aborted", False))
        self._round_aborted = False
        self._flush_pending(force=True)
        start = self._stream_start
        block_start = self._stream_block_start
        self._stream_start = None
        self._stream_block_start = None
        self._stream_thinking_fold = None
        self._stream_tail = ""
        self._stream_tail_rng = None
        self._stream_tail_links = []
        if start is not None and block_start is not None:
            # 流式期间思考/工具已按折叠卡片渲染，无需全量重建；
            # 仅回填 content 块的 message 索引（供右键菜单定位）
            msg_idx = None
            with self._messages_lock:
                # 与 worker 的压缩/裁剪互斥：防「len 读取」与「del 切片」之间漂移
                if self.messages and self.messages[-1].get("role") == "assistant":
                    msg_idx = len(self.messages) - 1
            for i in range(block_start, len(self.blocks)):
                blk = self.blocks[i]
                if blk[0] == "content" and len(blk) > 2 and blk[2] is None:
                    self.blocks[i] = (blk[0], blk[1], msg_idx)
        # 打断重发场景：send() 已提示「已打断当前生成，结束后自动发送」，不再重复
        if self.stop_event and self.stop_event.is_set() and not self._pending_send:
            self._append("\n[已停止生成]\n", "error")
            self.blocks.append(("error", "\n[已停止生成]\n"))
        self._append("\n")
        self.blocks.append(("plain", "\n"))
        if self._agent_tool_count > 0:
            dur = time.monotonic() - self._stream_begin
            u = self.last_usage or {}
            ok_n = 0
            fail_n = 0
            fail_items = []
            for b in self.blocks:
                if b[0] == "tool":
                    res = str(b[1][2] or "")
                    if res.startswith(_dc.TOOL_RESULT_FAIL_PREFIXES):
                        fail_n += 1
                        fail_items.append(
                            {
                                "tool": str(b[1][0]),
                                "args": (
                                    json.dumps(b[1][1], ensure_ascii=False)
                                    if b[1][1] else ""
                                )[:100],
                                "error": (res.splitlines()[0] or res)[:200],
                                "ts": datetime.now().isoformat(timespec="seconds"),
                            }
                        )
                    else:
                        ok_n += 1
            mark = "✅" if fail_n == 0 else "⚠"
            if aborted:
                report = (
                    f"[任务中断] ⚠ 工具 {ok_n} 成功 / {fail_n} 失败 · 耗时 {dur:.1f}s"
                    f" · 输入 {u.get('prompt', 0):,} / 输出 {u.get('completion', 0):,} token\n"
                    "本轮回复未正常完成：请重新发送上一条消息让 AI 继续任务，"
                    "或开启 Beta API 用「继续生成」从断点续写。\n"
                )
            else:
                report = (
                    f"[任务完成] {mark} 工具 {ok_n} 成功 / {fail_n} 失败 · 耗时 {dur:.1f}s"
                    f" · 输入 {u.get('prompt', 0):,} / 输出 {u.get('completion', 0):,} token\n"
                )
            self._append(report, "time")
            self.blocks.append(("note", report))
            # 产物自动核验：本轮 write/create 工具声明路径的实存性
            try:
                check_paths = []
                for b in self.blocks:
                    if b[0] == "tool" and b[1][0] in (
                        "write_file", "create_doc", "write_code_project", "edit_file",
                    ):
                        res = str(b[1][2] or "")
                        for mm in PATH_RE.finditer(res):
                            p = mm.group(0).rstrip("。.,;: \t")
                            if p and p not in check_paths:
                                check_paths.append(p)
                if check_paths:
                    missing = [p for p in check_paths if not os.path.exists(p)]
                    if missing:
                        vnote = (
                            f"⚠ 产物核验：{len(missing)}/{len(check_paths)} 个文件未找到："
                            f"{', '.join(os.path.basename(p) for p in missing[:3])}\n"
                        )
                    else:
                        vnote = f"✅ 产物核验：{len(check_paths)} 个文件均真实存在\n"
                    self._append(vnote, "time")
                    self.blocks.append(("note", vnote))
            except Exception:
                pass
            if fail_n == 0 and ok_n >= 1:
                self._record_success_pattern()
            elif fail_n > 0 and not self.cfg.get("privacy_mode"):
                # 失败模式积累：供后续任务注入「已知失败模式」帮助 AI 规避
                self._append_failures(fail_items)
                # 自动经验复盘：把本次失败经验沉淀到长期记忆（跨会话复用，
                # 记忆随每次请求注入，AI 下次遇到同类任务能主动规避）
                self._record_reflection(ok_n, fail_n, fail_items)
            if ok_n >= 1:
                self._record_tasklog()
            if aborted:
                # 中断：自动断点已由 _auto_checkpoint 持久化，提供一键续跑入口
                self._offer_continue_from_checkpoint()
            elif not self.cfg.get("privacy_mode"):
                # 正常完成：清除自动断点（手动断点保留），避免残留误导
                try:
                    _dc.task_checkpoint_clear()
                except Exception:
                    pass
        elif aborted:
            # 纯对话（无工具）但被截断/中断：明确提示而非静默「回复完成」
            self._append(
                "\n[回复中断] 本轮生成未正常完成（网络中断 / 输出截断 / 异常）。\n"
                "可重新发送消息继续，或开启 Beta API 后用「继续生成」从断点续写。\n",
                "time",
            )
            self.blocks.append(("note", "[回复中断] 本轮生成未正常完成。\n"))
        if self.task_panel is not None:
            try:
                if aborted:
                    summary = f"⚠ 任务中断 · {self._agent_tool_count} 个工具（回复未完成）" if self._agent_tool_count else "⚠ 回复中断"
                else:
                    summary = f"✅ 任务完成 · {self._agent_tool_count} 个工具" if self._agent_tool_count else "✅ 回复完成"
                self.task_panel.finish(summary=summary)
            except Exception:
                pass
        self._suggest()
        self.assistant_answered = True
        if self.cfg.get("notify_on_done"):
            self._flash_taskbar()
            try:
                self.root.bell()
            except Exception:
                pass
            # v2 联动：任务/回复完成同时发本地桌面 Toast（后台线程，不阻塞）
            if not self.cfg.get("privacy_mode"):
                try:
                    if aborted:
                        summary = "⚠ 任务中断（回复未完成）" if self._agent_tool_count else "⚠ 回复中断"
                    else:
                        summary = "✅ 回复完成"
                        if self._agent_tool_count > 0:
                            ok_n = sum(
                                1 for b in self.blocks
                                if b[0] == "tool" and not str(b[1][2] or "").startswith(
                                    ("错误", "权限拒绝", "超时", "（用户停止")
                                )
                            )
                            summary = f"✅ 任务完成 · {ok_n} 个工具成功"
                    reply_hint = ""
                    for b in reversed(self.blocks):
                        if b[0] == "content" and str(b[1]).strip():
                            reply_hint = " ".join(str(b[1]).split())[:60]
                            break
                    if reply_hint:
                        summary += f"\n{reply_hint}"
                    threading.Thread(
                        target=_dc.notify_desktop, args=("鲸语", summary), daemon=True
                    ).start()
                except Exception:
                    pass
        self._set_busy(False)
        self._variant_seed_override = None  # 变体 seed 只对本轮生效，防止污染后续生成
        self._pending_tool_durations.clear()  # 清残留工具耗时，防马拉松会话累积
        self._flush_recent()  # 最近产物一次性落盘（替代每工具 2 读 1 写）
        self._snapshot_dirty = True
        self.update_status()
        self._update_context_bar()
        self._flush_stats()
        self._maybe_save_snapshot()
        if self._pending_send:
            text = self._pending_send
            self._pending_send = None
            self.send(text=text)

    def _insert_content(self, text, payload, tag, msg_idx, last_code_blocks, pos):
        pos = text.index(pos)
        if self._md_render:
            dtext, spans, links, code_blocks = mdparse.render_markdown(payload)
            text.insert(pos, dtext, tag)
            # 存储数值键 (start, end, url)：点击时二分定位，零 Tcl round-trip
            new_links = [
                (
                    _index_num(text.index(f"{pos}+{a}c")),
                    _index_num(text.index(f"{pos}+{b}c")),
                    url,
                )
                for a, b, url in links
            ]
            self._link_ranges.setdefault(text, []).extend(new_links)
            for a, b, st in spans:
                try:
                    text.tag_add(st, f"{pos}+{a}c", f"{pos}+{b}c")
                except tk.TclError:
                    pass
            # 只给本次新增的链接打 tag，避免长会话全量重渲染 O(n²) 次 Tcl 调用
            for r0, r1, url in new_links:
                try:
                    text.tag_add("link", f"{r0[0]}.{r0[1]}", f"{r1[0]}.{r1[1]}")
                except tk.TclError:
                    pass
            if last_code_blocks is not None:
                for _a, _b, code in code_blocks:
                    if code.strip():
                        last_code_blocks.append(code + "\n")
            return text.index(f"{pos}+{len(dtext)}c")
        for style, seg in split_code_blocks(payload):
            text.insert(pos, seg, style)
            if style == "code" and seg.strip() and last_code_blocks is not None:
                last_code_blocks.append(seg)
            pos = text.index(f"{pos}+{len(seg)}c")
        return pos

    def _insert_fold(self, text, title, payload, style, visible, pos):
        """插入可折叠卡片。折叠态共用 fold_hidden tag（而非每块新建 tag），
        避免长对话下 Tk tag 数量无限累积。

        位置全部在插入后基于 "end-偏移" 计算为绝对索引：不依赖插入前
        index 猜测（"end" 附近的虚拟行会导致偏移），后续插入不受影响。
        """
        text.configure(state="normal")  # disabled 状态下 insert 会被静默忽略
        arrow = "▼" if visible else "▶"
        head = f"{arrow} {title}\n"
        text.insert(pos, head, (f"{style}_toggle",))
        t0 = text.index(f"end-{len(head)}c")
        t1 = text.index("end-1c")
        body = payload if payload.endswith("\n") else payload + "\n"
        text.insert("end", body, (style,))
        p1 = text.index("end-1c")
        if not visible:
            text.tag_add("fold_hidden", t1, p1)
        self._fold_nums.setdefault(text, []).append(_index_num(t0))
        self._fold_ranges[text].append(
            {
                "style": style,
                "title": title,
                "head": head,
                "ttag": f"{style}_toggle",
                "visible": visible,
                "t0": t0,
                "t1": t1,
                "p1": p1,
                "t0_num": _index_num(t0),  # 数值键：点击命中走二分而非线性 compare
            }
        )
        return p1

    def _on_fold_click(self, event):
        text = self.chat_text
        self._follow_bottom = False  # 点击折叠卡片 = 阅读操作，停止自动跟随
        try:
            index = text.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        folds = self._fold_ranges.get(text, [])
        if folds:
            # 快速路径：按数值键二分定位（t0 随插入严格递增），零 Tcl round-trip
            nums = self._fold_nums.get(text)
            if nums is None:
                nums = [f["t0_num"] for f in folds]
            i = bisect.bisect_right(nums, _index_num(index)) - 1
            if i >= 0:
                f = folds[i]
                if text.compare(f["t0"], "<=", index) and text.compare(index, "<", f["t1"]):
                    self._toggle_fold(text, f)
                    return
        for ttag in ("thinking_toggle", "tool_toggle"):
            try:
                r = text.tag_prevrange(ttag, index)
                if not r:
                    continue
                s, e = r
                if text.compare(e, ">", index):
                    for f in folds:
                        if (
                            f.get("ttag") == ttag
                            and text.compare(f["t0"], "<=", index)
                            and text.compare(index, "<", f["t1"])
                        ):
                            self._toggle_fold(text, f)
                            return
            except tk.TclError:
                continue
        # 兜底：原有逆序遍历
        for f in reversed(folds):
            try:
                if text.compare(f["t0"], "<=", index) and text.compare(index, "<", f["t1"]):
                    self._toggle_fold(text, f)
                    return
            except tk.TclError:
                continue

    def _toggle_fold(self, text, f):
        try:
            f["visible"] = not f["visible"]
            if f["visible"]:
                text.tag_remove("fold_hidden", f["t1"], f["p1"])
            else:
                text.tag_add("fold_hidden", f["t1"], f["p1"])
            arrow = "▼" if f["visible"] else "▶"
            new_head = f"{arrow} {f['title']}\n"
            text.delete(f["t0"], f"{f['t0']}+{len(f['head'])}c")
            text.insert(f["t0"], new_head, (f["ttag"],))
            f["head"] = new_head
        except tk.TclError:
            # 索引失效（重渲染/会话切换后残留的折叠记录）：静默忽略，避免事件层冒泡
            pass

    def _set_all_folds_elide(self, text, elide):
        try:
            if elide:
                pairs = []
                for f in self._fold_ranges.get(text, []):
                    pairs.extend((f["t1"], f["p1"]))
                if pairs:
                    text.tag_add("fold_hidden", *pairs)  # 一次调用批量隐藏
            else:
                text.tag_remove("fold_hidden", "1.0", "end")  # 全量展开一次调用
        except tk.TclError:
            pass

    def _restore_fold_elides(self, text):
        pairs = []
        for f in self._fold_ranges.get(text, []):
            if not f.get("visible", True):
                pairs.extend((f["t1"], f["p1"]))
        if not pairs:
            return
        try:
            text.tag_add("fold_hidden", *pairs)
        except tk.TclError:
            pass

    def _render_block(self, text, block, last_code_blocks, pos):
        """渲染单个 blocks 元素，返回渲染后的插入位置。"""
        kind = block[0]
        if kind == "content":
            payload = block[1]
            msg_idx = block[2] if len(block) > 2 else None
            return self._insert_content(text, payload, "assistant", msg_idx, last_code_blocks, pos)
        elif kind == "tool":
            name, args, result = block[1][0], block[1][1], block[1][2]
            duration = block[1][3] if len(block[1]) > 3 else None
            try:
                args_str = json.dumps(args, ensure_ascii=False) if args else "{}"
            except (TypeError, ValueError):
                args_str = str(args)
            detail = f"参数: {args_str}\n结果: {result}\n"
            res_text = str(result or "")
            failed = res_text.startswith(_dc.TOOL_RESULT_FAIL_PREFIXES)
            mark = "❌" if failed else "✅"
            title = f"{mark} [工具] {name}"
            if duration is not None:
                title += f" · {duration:.1f}s"
            first = res_text.splitlines()[0] if res_text else ""
            if first:
                title += f" · {first[:44]}"
            new_pos = self._insert_fold(text, title, detail, "tool", failed, pos)
            self._mark_filelinks(text, new_pos, detail)
            return new_pos
        elif kind == "toolresult":
            seg = f"[工具结果] {block[1]}\n"
            text.insert(pos, seg, "tool")
            return text.index(f"{pos}+{len(seg)}c")
        elif kind == "note":
            htag = block[2] if len(block) > 2 else "time"
            text.insert(pos, block[1], htag)
            return text.index(f"{pos}+{len(block[1])}c")
        elif kind == "user":
            text.insert(pos, block[1], "user")
            return text.index(f"{pos}+{len(block[1])}c")
        elif kind == "thinking":
            return self._insert_fold(text, "思考过程", block[1], "thinking", True, pos)
        elif kind == "error":
            text.insert(pos, block[1], "error")
            return text.index(f"{pos}+{len(block[1])}c")
        else:
            text.insert(pos, block[1])
            return text.index(f"{pos}+{len(block[1])}c")

    def _render_blocks(self, text, blocks, last_code_blocks, start_pos="1.0"):
        pos = text.index(start_pos)
        for block in blocks:
            pos = self._render_block(text, block, last_code_blocks, pos)

    def _fold_early_view(self, blocks):
        """长会话惰性折叠：早期消息块折叠为一条提示（配置默认 0=关闭）。

        只构造渲染视图，不修改 self.blocks 本身；折叠前块存入会话
        `_early_snapshot`，点击提示后设置 `_early_expanded`（本次会话
        不再折叠）并全量重渲染。
        """
        if self._current.get("_early_expanded"):
            return blocks
        threshold = int(self.cfg.get("fold_early_threshold", 0) or 0)
        if threshold <= 0 or len(blocks) <= threshold:
            self._current.pop("_early_snapshot", None)
            return blocks
        keep = max(2, threshold)
        self._current["_early_snapshot"] = list(blocks[:keep])
        hint = f"⋯ 早期内容已折叠（{keep} 个消息块）⋯ 点击此处展开 ⋯\n"
        return [("note", hint, "fold_hint")] + blocks[keep:]

    def _on_fold_early_click(self, _event=None):
        if self._current.pop("_early_snapshot", None) is None:
            return
        self._current["_early_expanded"] = True
        self._render_all()
        self._flash_status("已展开早期消息")

    def _on_continue_click(self, _event=None):
        """中断后的「从断点继续」：以检查点上下文恢复执行。"""
        if self.busy:
            self._flash_status("请先停止当前生成")
            return
        self._clear_placeholder()
        self.input_text.delete("1.0", "end")
        self.input_text.insert(
            "1.0",
            "请从断点继续执行未完成任务：先调用 task_checkpoint_load 恢复任务上下文，"
            "然后继续完成剩余步骤，全部完成后用 task_checkpoint_save 标记完成。",
        )
        self.input_text.focus_set()
        self.send()

    def _sync_thinking_fold(self, text):
        """全量重渲染完成后恢复进行中思考块的卡片游标。

        仅当 blocks 末尾确实是进行中的思考块（重渲染发生于该轮思考流式期间，
        如生成中切换 Markdown 渲染）时，把 _stream_thinking_fold 指回它渲染
        的卡片，使后续思考增量继续追加到同一张卡片；
        否则清空（新一轮思考必须创建新卡片，绝不能复用上一轮的思考卡片）。"""
        if self.blocks and self.blocks[-1][0] == "thinking":
            for f in reversed(self._fold_ranges.get(text, [])):
                if f.get("style") == "thinking":
                    self._stream_thinking_fold = f
                    return
        self._stream_thinking_fold = None

    def _render_all(self, paged=True):
        """全量重渲染。

        大内容分帧渲染：Tk 对长 Text（数万行/几十万字符）的映射与布局是一次性的，
        长会话一次性渲染会让界面卡死数秒（实测 601 条消息 5.7s）。分帧后每帧
        只插入一小批 block，事件循环保持响应（窗口可拖动、可切换），内容渐进出现。
        paged=False 强制同步渲染（分帧被打断后需要完整内容的场景）。
        """
        self._cancel_paged_render()
        text = self.chat_text
        blocks = self._fold_early_view(self.blocks)
        text.configure(state="normal")
        text.delete("1.0", "end")
        self._current["last_code_blocks"] = []
        self._link_ranges[text] = []
        self._fold_ranges[text] = []
        self._fold_nums[text] = []
        self._filelink_ranges[text] = []
        self._stream_thinking_fold = None  # 全量重绘后旧的折叠游标失效
        self._stream_tail = ""  # 全量重绘后旧尾部的索引全部失效
        self._stream_tail_rng = None
        self._stream_tail_links = []
        if paged and len(blocks) > PAGED_RENDER_THRESHOLD:
            self._render_blocks_paged(text, blocks, self._current["last_code_blocks"])
        else:
            self._render_blocks(text, blocks, self._current["last_code_blocks"])
            text.configure(state="disabled")
            self._follow_bottom = True
            text.see("end")
            self._sync_thinking_fold(text)

    def _cancel_paged_render(self):
        """取消进行中的分帧渲染（新渲染/关闭/编辑前必须调用，防游标错位）。"""
        self._paged_render = None
        if getattr(self, "_paged_render_after", None) is not None:
            try:
                self.root.after_cancel(self._paged_render_after)
            except Exception:
                pass
            self._paged_render_after = None

    def _render_blocks_paged(self, text, blocks, last_code_blocks):
        self._paged_render = {
            "text": text,
            "blocks": blocks,
            "last_code_blocks": last_code_blocks,
            "pos": "1.0",
            "idx": 0,
        }
        self._paged_step()

    def _paged_step(self):
        state = self._paged_render
        if state is None:
            return
        text = state["text"]
        try:
            blocks = state["blocks"]
            text.configure(state="normal")  # 帧内渲染需要可写
            pos = text.index(state["pos"])
            end = min(state["idx"] + PAGED_RENDER_SIZE, len(blocks))
            for block in blocks[state["idx"]:end]:
                pos = self._render_block(
                    text, block, state["last_code_blocks"], pos
                )
            state["idx"] = end
            state["pos"] = pos
            # 帧间立即恢复 disabled：分帧期间文本区不可编辑（否则用户可在
            # 渲染中的文档里输入字符，索引全部错位）
            text.configure(state="disabled")
            if state["idx"] >= len(blocks):
                self._paged_render = None
                self._paged_render_after = None
                text.configure(state="disabled")
                # 分帧期间用户可能已切换会话：跟随只对渲染目标生效，别把新会话拉到底
                if text is self.chat_text:
                    self._ensure_follow()
                    self._sync_thinking_fold(text)
                self._flush_paged_pending()
                return
        except tk.TclError:
            self._paged_render = None
            self._paged_render_after = None
            # 分帧中断时 text 可能停留在 normal 态，恢复 disabled 防止误编辑
            try:
                text.configure(state="disabled")
            except tk.TclError:
                pass
            # 渲染目标被销毁（竞态关闭会话）时也不能丢用户操作：挂起项照常续上
            self._flush_paged_pending()
            return
        self._paged_render_after = self.root.after(PAGED_RENDER_MS, self._paged_step)

    def _flush_paged_pending(self):
        """分帧渲染结束后续上挂起的发送/搜索/追加（成功与 TclError 路径共用）。"""
        pending_send = self._send_when_ready
        if pending_send is not None:
            self._send_when_ready = None
            self.after(20, lambda: self.send(text=pending_send))
        if self._search_when_ready:
            self._search_when_ready = False
            self.after(20, self._do_search)
        if self._pending_appends:
            todo, self._pending_appends = self._pending_appends, []
            for t, tg in todo:
                self._append(t, tg)

    def check_balance(self):
        cfg = self.save_widgets_to_config()
        if not cfg["api_key"]:
            messagebox.showwarning("未配置 API Key", "请先填写 DeepSeek API Key。")
            return
        self.btn_balance.configure(state="disabled")

        def worker():
            try:
                data = check_balance(cfg["api_key"], cfg["base_url"])
                self._ui_queue.put(("balance", data))
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status == 401:
                    msg = "API Key 无效或认证失败"
                elif status == 402:
                    msg = "余额不足，请前往充值"
                else:
                    msg = str(e)
                self._ui_queue.put(("error", f"查询余额失败: {msg}"))
            finally:
                self._ui_queue.put(("balance_done", None))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_update_result(self, payload):
        latest, url = payload

        def parse(v):
            try:
                return tuple(int(x) for x in v.split("."))
            except (ValueError, AttributeError):
                return (0,)

        if parse(latest) > parse(VERSION):
            msg = f"发现新版本 {latest}（当前 {VERSION}）。"
            if url:
                msg += "\n更新前将自动备份当前版本源码（backups/ 目录）。\n是否备份并继续？"
                if messagebox.askyesno("发现新版本", msg):
                    # 备份（os.walk + zip 压缩 0.5-2s）放后台线程，避免主线程卡顿
                    def do_backup():
                        try:
                            import backup

                            bpath = backup.make_backup()
                            self._ui_queue.put(
                                ("info", f"已备份当前版本：{os.path.basename(bpath)}")
                            )
                        except Exception:
                            logging.exception("更新前备份失败")

                    threading.Thread(target=do_backup, daemon=True).start()
                    if url.lower().endswith((".exe", ".zip", ".7z", ".msi")):
                        # 直链安装包：应用内下载到下载目录（进度可见）
                        self._download_update(latest, url)
                    else:
                        webbrowser.open(url)
            else:
                messagebox.showinfo("发现新版本", msg + "\n请从官方渠道获取安装包。")
        else:
            self._show_note(f"[更新] 当前已是最新版本 {VERSION}。")

    def _download_update(self, version, url):
        """下载新版安装包到用户下载目录（后台线程，完成经队列提示）。"""
        self._flash_status(f"正在下载 v{version} 安装包…")
        try:
            from urllib.parse import urlparse

            fn = os.path.basename(urlparse(str(url)).path) or f"WhaleTalk_v{version}.exe"
        except Exception:
            fn = f"WhaleTalk_v{version}.exe"
        target_dir = os.path.join(os.path.expanduser("~"), "Downloads", "WhaleTalk 更新")

        def worker():
            try:
                os.makedirs(target_dir, exist_ok=True)
                path = os.path.join(target_dir, fn)
                with _dc._http_client().stream("GET", str(url), timeout=60) as resp:
                    resp.raise_for_status()
                    with open(path, "wb") as f:
                        total = 0
                        for chunk in resp.iter_bytes(64 * 1024):
                            total += len(chunk)
                            if total > 2 * 1024 * 1024 * 1024:
                                raise RuntimeError("安装包超过 2GB，已放弃")
                            f.write(chunk)
                self._ui_queue.put(("update_downloaded", (True, path)))
            except Exception as e:
                logging.exception("更新包下载失败")
                self._ui_queue.put(("update_downloaded", (False, str(e))))

        threading.Thread(target=worker, daemon=True).start()

    def _show_balance(self, data):
        """余额查询结果：品牌对话框展示（替代系统 messagebox）。"""
        t = self._theme()
        dialog, body, footer = self._dialog_shell("余额查询", 460, 320, subtitle="DeepSeek 账户余额")
        self._lbl(
            body, "账户状态: " + ("✅ 可用" if data.get("is_available") else "❌ 不可用"),
            role="label_accent" if data.get("is_available") else "label_error",
            bg="panel", font=(FONT_FAMILY, 11, "bold"),
        ).pack(anchor="w", pady=(0, 10))
        for info in data.get("balance_infos", []):
            card = tk.Frame(body, bg=t["surface"], padx=12, pady=10)
            card.pack(fill="x", pady=4)
            self._restyle.append((card, "surface"))
            self._lbl(
                card, f"总余额 ¥{info.get('total_balance')}",
                bg="surface", font=(FONT_FAMILY, 12, "bold"),
            ).pack(anchor="w")
            self._lbl(
                card,
                f"赠送 ¥{info.get('granted_balance')} · 充值 ¥{info.get('topped_up_balance')}",
                role="label_sec", bg="surface", font=(FONT_FAMILY, 9),
            ).pack(anchor="w", pady=(2, 0))
        self._footer_btn(footer, "关闭", dialog.destroy)

    def show_stats(self):
        t = self._theme()
        data = stats.load_stats(STATS_PATH)
        today = date.today().isoformat()
        today_usage = stats.day_total(data, today)
        month_key = date.today().strftime("%Y-%m")
        month_usage = stats.empty_day()
        for day, models in data.items():
            if day.startswith(month_key):
                for usage in models.values():
                    for key in month_usage:
                        month_usage[key] += usage.get(key, 0)
        total_usage = stats.all_total(data)
        today_cost = sum(
            stats.estimate_cost(usage, model)
            for model, usage in (data.get(today) or {}).items()
        )
        month_cost = sum(
            stats.estimate_cost(usage, model)
            for day, models in data.items()
            if day.startswith(month_key)
            for model, usage in models.items()
        )
        total_cost = sum(
            stats.estimate_cost(usage, model)
            for models in data.values()
            for model, usage in models.items()
        )
        savings = 0.0
        for models in data.values():
            for model, usage in models.items():
                price = stats.pricing().get(model, stats.DEFAULT_PRICE)
                savings += (
                    usage.get("cache_hit", 0) * (price["prompt"] - price["cache_hit"])
                    / 1_000_000
                )

        def fmt(u, cost):
            hit = u["cache_hit"]
            miss = u["cache_miss"]
            ratio = f"缓存占比 {hit / (hit + miss):.0%}" if (hit + miss) else "缓存占比 -"
            return (
                f"输入 {u['prompt']:,} (命中 {hit:,} / 未命中 {miss:,} · {ratio})\n"
                f"输出 {u['completion']:,}  |  预估费用 ¥{stats.format_cost(cost)}"
            )

        # 品牌对话框展示（替代系统 messagebox）
        dialog, body, footer = self._dialog_shell("用量统计", 520, 460, subtitle="按天/月/累计汇总 · 含缓存节省估算")
        scroll_wrap = tk.Frame(body, bg=t["panel"])
        scroll_wrap.pack(fill="both", expand=True)
        self._restyle.append((scroll_wrap, "panel"))

        def section(title, usage, cost):
            self._lbl(scroll_wrap, title, role="label_accent", bg="panel",
                      font=(FONT_FAMILY, 10, "bold")).pack(anchor="w", pady=(8, 2))
            self._lbl(scroll_wrap, fmt(usage, cost), bg="panel",
                      font=(FONT_FAMILY, 9)).pack(anchor="w", padx=(12, 0))

        section(f"今日 ({today})", today_usage, today_cost)
        section(f"本月 ({month_key})", month_usage, month_cost)
        section("累计", total_usage, total_cost)
        self._lbl(
            scroll_wrap, f"💰 缓存节省 ≈ ¥{stats.format_cost(savings)}（未命中价差估算）",
            role="label_accent", bg="panel", font=(FONT_FAMILY, 9, "bold"),
        ).pack(anchor="w", pady=(10, 2))
        if data:
            self._lbl(scroll_wrap, "各模型累计:", role="label_sec", bg="panel",
                      font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(6, 2))
            for model in sorted({mdl for models in data.values() for mdl in models}):
                mu = stats.model_total(data, model)
                mc = stats.estimate_cost(mu, model)
                self._lbl(
                    scroll_wrap,
                    f"· {model}: 输入 {mu['prompt']:,} / 输出 {mu['completion']:,} ≈ ¥{stats.format_cost(mc)}",
                    bg="panel", font=(FONT_FAMILY, 9),
                ).pack(anchor="w", padx=(12, 0))
            self._lbl(scroll_wrap, f"统计文件: {STATS_PATH}", role="label_sec", bg="panel",
                      font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(8, 0))
        self._footer_btn(footer, "关闭", dialog.destroy)

    def _estimate_context_tokens(self):
        return tokens.estimate_messages_tokens(self.messages)

    def _update_context_bar(self):
        if getattr(self, "_ctx_counts", None) is not None:
            n_tokens = tokens.BASE_OVERHEAD + sum(self._ctx_counts)
        else:
            # 无缓存（恢复长会话后首次发送前）：字符估算兜底，避免主线程全量 tiktoken
            n_tokens = int(self._estimate_chars() / 1.5)
        model = self.model_combo.get().strip()
        max_tok = MODELS.get(model, {}).get("max_context_tokens", MAX_CONTEXT_TOKENS)
        self.context_bar.configure(maximum=max_tok, value=min(n_tokens, max_tok))
        self.context_label.configure(
            text=f"上下文 {n_tokens:,}/{max_tok:,} token ({n_tokens / max_tok:.1%})"
        )

    def _set_busy(self, busy):
        self.busy = busy
        # 发送按钮始终可用：生成中点击 = 打断当前生成并立即发送（README「发送即打断」）
        self.btn_send.configure(state="normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")
        if busy:
            note = "[正在生成...]\n"
            self._append(note, "time")
            self.blocks.append(("note", note))

    def _estimate_chars(self):
        total = 0
        for m in self.messages:
            total += len(m.get("content") or "")
            total += len(m.get("reasoning_content") or "")
        return total

    def _trim_context(self, counts=None):
        # 整个裁剪流程持锁：worker 线程内唯一的 messages 复合写操作，
        # 与主线程的 _snapshot_assets / _finish 回填互斥
        with self._messages_lock:
            return self._trim_context_locked(counts)

    def _trim_context_locked(self, counts=None):
        token_limit = int(self.cfg.get("max_context_tokens", 400000))
        char_limit = int(self.cfg.get("max_context_chars", 500000))
        max_turns = max(3, int(self.cfg.get("min_kept_turns", 8)))
        user_indices = [i for i, m in enumerate(self.messages) if m.get("role") == "user"]
        if len(user_indices) <= max_turns:
            return 0
        if counts is None:
            counts = tokens.message_token_counts(self.messages)
        total_tokens = tokens.BASE_OVERHEAD + sum(counts)
        total_chars = self._estimate_chars()
        # 单遍前缀和取代逐轮 O(n) 重算：exchange k = messages[max(1,ui_k):ui_{k+1}]
        # （messages[0] 系统提示词永不移除），removed_idx_total 供 UI 消息索引重映射
        n = len(user_indices)
        next_user = user_indices[1:] + [len(self.messages)]
        pref_tok = [0] * (n + 1)
        pref_chr = [0] * (n + 1)
        for k in range(n):
            s = max(1, user_indices[k])
            e = next_user[k]
            pref_tok[k + 1] = pref_tok[k] + (
                sum(counts[s:e]) + tokens.PER_MESSAGE_OVERHEAD * (e - s)
            )
            pref_chr[k + 1] = pref_chr[k] + sum(
                len(m.get("content") or "") + len(m.get("reasoning_content") or "")
                for m in self.messages[s:e]
            )
        removed = 0
        running_tok, running_chr = total_tokens, total_chars
        for k in range(n):
            if n - removed <= max_turns:
                break
            e_tok = pref_tok[k + 1] - pref_tok[k]
            e_chr = pref_chr[k + 1] - pref_chr[k]
            # 语义与原实现一致：移除下一轮会跌破阈值则停（避免过度裁剪）
            if running_tok - e_tok <= token_limit and running_chr - e_chr <= char_limit:
                break
            running_tok -= e_tok
            running_chr -= e_chr
            removed += 1
        if removed:
            cut = next_user[removed - 1]
            dropped_msgs = self.messages[1:cut]
            removed_idx_total = cut - 1
            del self.messages[1:cut]
            self._ctx_counts = None  # 消息集已变，缓存计数失效（长度对不上旧列表）
            logging.info(
                "上下文压缩：移除 %s 轮，剩余估算字符 %s / token %s",
                removed,
                self._estimate_chars(),
                tokens.estimate_messages_tokens(self.messages),
            )
            tokens.clear_cache()  # 缓存强引用被裁剪消息，清空防滞留
            # UI content 块的 msg_idx 指向被移除区间的消息会错位/失效，通知主线程重映射
            self._ui_queue.put(("remap_idx", removed_idx_total))
            archived = self._archive_dropped(dropped_msgs, self._session_display_name(self._current))
            if archived:
                note = f"[归档] 硬裁剪移除的内容已保存至 {archived}。\n"
                self._ui_queue.put(("info", note))
        return removed

    def _context_over_limit(self):
        token_limit = int(self.cfg.get("max_context_tokens", 400000))
        char_limit = int(self.cfg.get("max_context_chars", 500000))
        if self._ctx_counts is None:
            self._ctx_counts = tokens.message_token_counts(self.messages)
        total_tokens = tokens.BASE_OVERHEAD + sum(self._ctx_counts)
        return total_tokens > token_limit or self._estimate_chars() > char_limit

    def _collect_exchanges(self):
        exchanges = []
        current = None
        for i, msg in enumerate(self.messages):
            if i == 0:
                continue
            role = msg.get("role")
            if role == "user":
                current = {"start": i, "end": i, "texts": [msg.get("content") or ""]}
                exchanges.append(current)
            elif current is not None and role in ("assistant", "tool"):
                if role == "assistant" and msg.get("content"):
                    current["texts"].append(msg.get("content"))
                elif role == "tool" and msg.get("content"):
                    current["texts"].append(f"[工具结果] {msg.get('content')}")
                current["end"] = i
        return exchanges

    def _is_pinned(self, msg_idx):
        try:
            content = self.messages[msg_idx].get("content") or ""
            return content in (self._current.get("pinned") or [])
        except (IndexError, TypeError):
            return False

    def _toggle_pin(self, msg_idx):
        if not (1 <= msg_idx < len(self.messages)):
            return
        content = self.messages[msg_idx].get("content") or ""
        if not content:
            return
        pinned = self._current.setdefault("pinned", [])
        if content in pinned:
            pinned.remove(content)
            self._flash_status("已取消固定")
        else:
            pinned.append(content)
            self._flash_status("已固定消息（上下文压缩时会被保留进摘要）")

    def edit_session_tags(self):
        session = self._current
        current = ",".join(session.get("tags") or [])
        tags = simpledialog.askstring(
            "设置标签", "输入标签（逗号分隔，支持 #标签 过滤）:", parent=self.root, initialvalue=current
        )
        if tags is None:
            return
        clean = [t.strip() for t in tags.split(",") if t.strip()]
        session["tags"] = clean
        self._refresh_session_list()
        self._flash_status("标签已更新")

    def _archive_dropped(self, drop_region, session_name):
        """把被压缩裁剪的轮次归档为本地 Markdown 文件，返回路径或 None。

        隐私模式下不写盘（与"不保存快照/统计/日志"承诺一致）。
        """
        if self.cfg.get("privacy_mode"):
            return None
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            safe = re.sub(r'[\\/:*?"<>|]', "_", session_name)[:40]
            path = os.path.join(ARCHIVES_DIR, f"{safe}_{ts}.md")
            lines = [
                f"# 会话归档：{session_name}",
                f"归档时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
                "",
            ]
            for msg in drop_region:
                role = msg.get("role")
                if role == "user":
                    lines += ["", "## 用户", "", msg.get("content") or ""]
                elif role == "assistant":
                    if msg.get("content"):
                        lines += ["", "## 助手", "", msg.get("content")]
                    if msg.get("reasoning_content"):
                        lines += ["", "## 助手（思考）", "", msg.get("reasoning_content")]
                elif role == "tool":
                    lines += ["", f"> 工具结果: {msg.get('content')}", ""]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return path
        except Exception:
            logging.exception("归档失败")
            return None

    def _compress_old_history(self):
        """AI 摘要压缩旧轮次（失败回退硬裁剪）。

        锁策略：收集 drop_region 与 del/insert 是复合写操作，必须持锁；
        _call_summary（最长 30s）不碰 messages，放锁外执行，避免阻塞主线程快照。
        """
        with self._messages_lock:
            exchanges = self._collect_exchanges()
            keep = max(3, int(self.cfg.get("min_kept_turns", 8)))
            if len(exchanges) <= keep:
                return False
            drop = exchanges[: len(exchanges) - keep]
            drop_end = drop[-1]["end"]
            drop_region = list(self.messages[1 : drop_end + 1])
        pinned = set(self._current.get("pinned") or [])
        history_text = ""
        for msg in drop_region:
            if msg.get("role") == "user":
                prefix = "【固定】" if (msg.get("content") or "") in pinned else ""
                history_text += f"\n用户{prefix}: {msg.get('content')}"
            elif msg.get("role") == "tool":
                history_text += f"\n[工具结果]: {msg.get('content')}"
            elif msg.get("content"):
                history_text += f"\n助手: {msg.get('content')}"
        if not history_text.strip():
            return False
        summary = self._call_summary(history_text[:12000])
        if not summary:
            logging.warning("历史总结失败，回退到硬裁剪")
            removed = self._trim_context()
            archived = self._archive_dropped(drop_region, self._session_display_name(self._current))
            note = (
                f"[上下文压缩] 总结失败，已移除最早 {removed} 轮对话。"
                if removed
                else "[上下文压缩] 总结失败，保留全部上下文。"
            )
            if archived:
                note += f"\n[归档] 被裁剪内容已保存至 {archived}"
            self._ui_queue.put(("info", note))
            return False
        summary_msg = {
            "role": "system",
            "content": (
                f"[历史对话摘要] 以下为较早轮次的压缩摘要：\n{summary}\n"
                "（摘要末尾的「关键事实」小节为可长期引用的要点，后续回答请参考）"
            ),
        }
        with self._messages_lock:
            # 摘要调用期间消息不会变（busy 时 send 挂起），del/insert 仍持锁防主线程快照穿插
            del self.messages[1 : drop_end + 1]
            self.messages.insert(1, summary_msg)
            self._ctx_counts = None  # 消息集已变，缓存计数失效
        logging.info("历史总结成功：替换 %s 条消息为摘要", len(drop_region))
        tokens.clear_cache()  # 缓存强引用被裁剪消息，清空防滞留
        self._ui_queue.put(("remap_idx", drop_end))  # UI 块 msg_idx 同步重映射
        note = f"[上下文压缩] 已用摘要替代最早 {len(drop)} 轮对话（{len(drop_region)} 条消息）。"
        archived = self._archive_dropped(drop_region, self._session_display_name(self._current))
        if archived:
            note += f"\n[归档] 被压缩内容已保存至 {archived}，可随时读取。"
        self._ui_queue.put(("info", note))
        return True

    def _call_summary(self, history_text):
        """生成历史摘要（非流式、思考关闭）。失败/停止/超时返回 ""（调用方回退硬裁剪）。

        API 请求放内部线程执行，worker 线程 0.5s 切片轮询：用户点「停止」或
        30s 超时立即放弃，不再阻塞 worker 至满超时（请求留在后台自然结束）。
        """
        box = {"resp": None}

        def _req():
            try:
                client = self.ensure_client()
                response = client.client.chat.completions.create(
                    model=client.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是对话历史摘要器。请将以下对话历史压缩为不超过400字的中文摘要，"
                                "保留关键事实、已做出的决定和未完成的任务，不要添加新信息。\n"
                                "摘要后另起一行输出「关键事实」小节：列出 2-6 条可长期引用的事实/"
                                "决策/待办（每条 - 前缀），只保留与未来继续对话相关的要点。"
                            ),
                        },
                        {"role": "user", "content": history_text},
                    ],
                    max_tokens=1024,
                    stream=False,
                    timeout=30.0,  # 摘要失败走硬裁剪兜底，不必久等
                    extra_body={"thinking": {"type": "disabled"}},
                )
                box["resp"] = response.choices[0].message.content or ""
            except Exception:
                logging.exception("历史摘要生成失败")
                box["resp"] = None

        t = threading.Thread(target=_req, daemon=True)
        t.start()
        deadline = time.monotonic() + 30.0
        while t.is_alive():
            if self.stop_event and self.stop_event.is_set():
                return ""
            if time.monotonic() >= deadline:
                return ""
            time.sleep(0.5)
        return box["resp"] or ""

    def generate_session_summary(self):
        """一键生成当前会话纪要（主题/要点/决策/待办/产物），写入工作区 summaries/。"""
        cfg = self.save_widgets_to_config()
        self._capture_client_params()
        if not cfg["api_key"]:
            messagebox.showwarning("未配置 API Key", "请先填写 DeepSeek API Key。")
            return
        msgs = [m for m in self.messages if m.get("role") != "system"]
        if len(msgs) < 2:
            messagebox.showinfo("提示", "会话内容太少，暂无可生成的纪要。")
            return
        history_text = ""
        for m in msgs:
            role = "用户" if m.get("role") == "user" else "助手"
            body = str(m.get("content") or "")
            if body:
                history_text += f"[{role}] {body}\n"
            reasoning = str(m.get("reasoning_content") or "")
            if reasoning:
                history_text += f"[思考] {reasoning}\n"
            for tc in m.get("tool_calls") or ():
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    history_text += f"[工具调用] {fn.get('name')}: {fn.get('arguments', '')[:200]}\n"
        if len(history_text) > 80000:
            history_text = history_text[:80000] + "\n…（内容过长已截断）"
        self._flash_status("⏳ 正在生成会话纪要…")
        title = self._session_display_name(self._current)
        session_name = self._current.get("name") or "未命名会话"

        def worker():
            try:
                client = self.ensure_client()
                response = client.client.chat.completions.create(
                    model=client.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是专业纪要助手。请根据对话内容生成结构化中文纪要，"
                                "使用以下标题：\n"
                                "## 主题\n## 要点\n## 决策\n## 待办\n## 产物\n"
                                "基于对话事实归纳，不添加新信息；没有的内容写「无」。"
                            ),
                        },
                        {"role": "user", "content": history_text},
                    ],
                    max_tokens=2048,
                    stream=False,
                    timeout=60.0,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                summary = response.choices[0].message.content or ""
                self._ui_queue.put(("summary_done", (session_name, title, summary)))
            except Exception as e:
                self._ui_queue.put(("error", f"纪要生成失败: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_summary_done(self, payload):
        session_name, title, summary = payload
        if not summary.strip():
            messagebox.showinfo("会话纪要", "纪要生成失败：模型返回空内容，请重试。")
            return
        try:
            safe = re.sub(r'[\\/:*?"<>|\s]+', "_", session_name)[:40] or "会话"
            d = os.path.join(WORKSPACE_DIR, "summaries")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"{safe}_{datetime.now():%Y%m%d_%H%M%S}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# {title} · 会话纪要\n\n{session_name} · {datetime.now():%Y-%m-%d %H:%M:%S}\n\n---\n\n{summary}\n")
            self._record_recent_output(path)
            self._flash_status("✅ 会话纪要已生成")
            if messagebox.askyesno("会话纪要", f"已生成会话纪要：\n{path}\n\n摘要预览：\n{summary[:300]}\n\n是否打开文件？"):
                self._open_path(path)
        except Exception:
            logging.exception("保存会话纪要失败")
            messagebox.showinfo("会话纪要", "纪要已生成，但写入文件失败（见日志）。\n\n" + summary[:500])

    def _register_modal(self, dialog):
        """登记模态弹窗：停止生成时统一关闭（ask/审批/计划确认弹窗
        在 worker 已返回后若不关闭会残留屏幕）。"""
        lst = getattr(self, "_modal_dialogs", [])
        if dialog not in lst:
            lst.append(dialog)

            def _cleanup(_e=None, d=dialog):
                try:
                    if d in lst:
                        lst.remove(d)
                except Exception:
                    pass

            dialog.bind("<Destroy>", _cleanup, add="+")

    def _close_modal_dialogs(self):
        for d in list(getattr(self, "_modal_dialogs", []) or []):
            try:
                d.destroy()
            except Exception:
                pass

    def stop_generate(self):
        if self.stop_event:
            self.stop_event.set()
        # 模型对比流程跟随停止：busy 一 False 就推进下一个模型会继续消耗额度
        self._compare_pending = None
        # worker 已返回但用户还没处理的弹窗（ask/审批/计划确认）一并关闭
        self._close_modal_dialogs()

    def update_status(self):
        """状态栏刷新（Tk 销毁期间/控件已销毁时静默失败，不冒泡中断调用链）。"""
        try:
            self._update_status_inner()
        except tk.TclError:
            pass
        except Exception:
            logging.exception("状态栏刷新失败")

    def _update_status_inner(self):
        # 状态栏被正常刷新（非 flash）时让挂起的 flash 恢复回调作废，防止旧文本回退
        self._flash_gen = getattr(self, "_flash_gen", 0) + 1
        t = self._theme()
        if self.last_usage:
            cur = self.last_usage
            hit = cur["cache_hit"]
            miss = cur["cache_miss"]
            ratio = hit / (hit + miss) if (hit + miss) else None
            ratio_str = ""
            if ratio is not None:
                ratio_str = f" 缓存占比 {ratio:.0%}"
            cur_str = (
                f"本轮: 输入 {cur['prompt']} (缓存命中 {hit} / 未命中 {miss})"
                f" 输出 {cur['completion']}{ratio_str}"
            )
            fg = None
            if ratio is not None:
                if ratio >= 0.9:
                    fg = t["success"]
                elif ratio >= 0.5:
                    fg = t["warning"]
                else:
                    fg = t["error"]
        else:
            cur_str = "本轮: -"
            fg = None
        total_str = (
            f"累计: 输入 {self.usage_total['prompt']} 输出 {self.usage_total['completion']}"
            f" (缓存命中 {self.usage_total['cache_hit']})"
        )
        budget = float(self.cfg.get("monthly_budget", 0) or 0)
        budget_str = ""
        if budget > 0:
            try:
                cost = self._monthly_cost()
            except Exception:
                cost = 0.0
            pct = cost / budget
            budget_str = f" | 本月 ¥{cost:.2f}/¥{budget:.2f}"
            if pct >= 1.0:
                budget_str += " ⛔ 已超限"
                fg = t["error"]
            elif pct >= 0.9:
                budget_str += " ⚠ 接近上限"
                fg = t["warning"]
        privacy = "🔒 " if self.cfg.get("privacy_mode") else ""
        auto = "🤖 " if self.cfg.get("full_auto") else ""
        chat = "💬 " if self.cfg.get("pure_chat") else ""
        peak = " ⏰ 高峰" if is_peak_hour() else ""
        wd = self._get_active_dir()
        wd_short = wd if len(wd) <= 28 else "…" + wd[-27:]
        # 左段：模式 + 工作目录 + 本轮/累计 + 预算/高峰（信息密度最高的部分）
        self.status_label.configure(
            text=(
                f"{privacy}{auto}{chat}📁 {wd_short} | {cur_str} | {total_str}{budget_str}{peak}"
            )
        )
        # 右段：模型 / 角色 / 场景 / 思考档位（角色 = 当前生效人格，状态全程可见）
        role_name = self._current_role_name(self.cfg.get("system_prompt", ""))
        self.status_right.configure(
            text=f"模型 {self.model_combo.get()} · 🎭 {role_name} · 场景 {self.scenario_combo.get()} · 思考 {self.thinking_combo.get()}"
        )
        if fg is not None:
            self.status_label.configure(fg=fg)

    def rebuild_view_from_messages(self):
        """从 messages 重建渲染视图（切换会话/重发/删除消息后调用）。

        增量优化：与现有 blocks 一致时跳过重绘（编辑重发未改动时零成本）；
        全量重建仅发生在内容真正变化时。
        """
        # 构造目标 blocks 时跳过 system 消息本身，但保留其内容参与比较
        target = [("note", "新会话已开始。输入问题，Enter 发送，Shift+Enter 换行。\n")]
        target.append(("plain", "\n"))
        with self._messages_lock:
            # 遍历期间与 worker 的裁剪/压缩互斥：防读一半列表被删
            msgs = list(self.messages)
        sys_marker = ("_sys", msgs[0].get("content") if msgs else "")
        target.insert(0, sys_marker)
        for i, msg in enumerate(msgs):
            role = msg.get("role")
            if role == "system":
                continue
            if role == "user":
                header = f"[{datetime.now():%H:%M:%S}] 用户\n"
                target.append(("note", header))
                target.append(("user", (msg.get("content") or "") + "\n"))
                target.append(("plain", "\n"))
            elif role == "assistant":
                header = f"[{datetime.now():%H:%M:%S}] 助手\n"
                target.append(("note", header))
                if msg.get("reasoning_content"):
                    target.append(("thinking", msg["reasoning_content"] + "\n"))
                if msg.get("content"):
                    target.append(("content", msg["content"] + "\n", i))
                target.append(("plain", "\n"))
            elif role == "tool":
                target.append(("toolresult", str(msg.get("content") or "")))
                target.append(("plain", "\n"))
        # 内容未变则跳过重绘（保持滚动位置与折叠状态）；
        # 系统提示词变化单独检测（影响注入提示词，需要重绘）
        sys_content = msgs[0].get("content") if msgs else ""
        sys_changed = getattr(self, "_rebuild_sys_content", None) != sys_content
        if target[1:] == self.blocks and not sys_changed:
            return
        # 消息集已变化（删除/分叉/变体恢复/载入）：token 计数缓存立即失效，
        # 否则上下文进度条/压缩判定继续用旧会话的计数
        self._ctx_counts = None
        # CappedList 保上限：普通 list 会让 8000 块裁剪永久失效（马拉松会话内存膨胀）
        self.blocks = CappedList(target[1:])
        self._rebuild_sys_content = sys_content
        self._render_all()

    def edit_last_message(self):
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        for i in range(len(self.messages) - 1, 0, -1):
            if self.messages[i].get("role") == "user":
                self._resend_index = i
                self._clear_placeholder()
                self.input_text.delete("1.0", "end")
                self.input_text.insert("1.0", self.messages[i].get("content", ""))
                self.input_text.focus_set()
                self._append(
                    "[编辑重发] 已载入消息，修改后按 Enter 发送（将替换原消息及其后续）。\n",
                    "time",
                )
                return
        messagebox.showinfo("提示", "暂无消息可编辑。")

    def regenerate(self):
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        for i in range(len(self.messages) - 1, 0, -1):
            if self.messages[i].get("role") == "user":
                text = self.messages[i].get("content", "")
                self._resend_index = None  # 重新生成会自行裁剪，清掉残留重发索引防止 send() 二次误删
                del self.messages[i:]
                self.rebuild_view_from_messages()
                note = "[重新生成] 已移除上次回复，重新生成中。\n"
                self._append(note, "time")
                self.blocks.append(("note", note))
                self.send(text=text)
                return
        messagebox.showinfo("提示", "暂无消息可重新生成。")

    def continue_generation(self):
        """利用 DeepSeek 对话前缀续写（Beta）：从当前回复末尾继续输出。"""
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        if not self.cfg.get("beta_api"):
            messagebox.showinfo(
                "提示",
                "「继续生成」使用 DeepSeek 对话前缀续写（Beta），"
                "请先在设置面板开启「Beta API」。",
            )
            return
        msgs = self.messages
        if not msgs or msgs[-1].get("role") != "assistant" or not msgs[-1].get("content"):
            messagebox.showinfo("提示", "需要最后一条为助手回复才能继续生成。")
            return
        # token 估算与压缩判定由 worker 线程完成（恢复长会话后主线程全量编码会卡 UI）
        self._ctx_counts = None
        self._needs_compression = False
        self._compression_note_shown = False
        self._set_busy(True)
        self._capture_client_params()
        self._update_context_bar()
        self.stop_event = threading.Event()
        threading.Thread(target=lambda: self._worker(continue_mode=True), daemon=True).start()

    def regenerate_variant(self):
        """保存当前回复为变体，以新 seed 重新生成一版。"""
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        for i in range(len(self.messages) - 1, 0, -1):
            m = self.messages[i]
            if m.get("role") == "assistant" and m.get("content"):
                variants = self._current.setdefault("variants", [])
                if m["content"] not in variants:
                    variants.append(m["content"])
                    if len(variants) > 20:
                        del variants[: len(variants) - 20]  # 只保留最近 20 版，防无界增长
                j = i
                while j > 0 and self.messages[j].get("role") != "user":
                    j -= 1
                if j == 0:
                    return
                text = self.messages[j].get("content", "")
                self._resend_index = None  # 清残留重发索引，防 send() 二次误删
                del self.messages[j:]
                self._needs_compression = False
                self.rebuild_view_from_messages()
                base = 0
                try:
                    base = int(self.cfg.get("seed") or 0)
                except (TypeError, ValueError):
                    base = 0
                self._variant_seed_override = base + len(variants)
                note = (
                    f"[变体] 已保存第 {len(variants)} 版回复，"
                    f"正在以 seed={self._variant_seed_override} 生成下一版。\n"
                )
                self._append(note, "time")
                self.blocks.append(("note", note))
                self.send(text=text)
                return
        messagebox.showinfo("提示", "暂无回复可生成变体。")

    def _replace_last_reply(self, content):
        for i in range(len(self.messages) - 1, 0, -1):
            if self.messages[i].get("role") == "assistant":
                self.messages[i]["content"] = content
                self.rebuild_view_from_messages()
                self._flash_status("已恢复该版本回复")
                return
        self._flash_status("未找到可替换的回复")

    def show_variants(self):
        t = self._theme()
        variants = self._current.get("variants") or []
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title(f"回复变体（{self._session_display_name(self._current)}）")
        dialog.geometry("620x420")
        dialog.transient(self.root)

        left = tk.Frame(dialog, bg=t["panel"])
        left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        self._restyle.append((left, "panel"))
        listbox = tk.Listbox(
            left,
            width=16,
            bg=t["input_bg"],
            fg=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
            exportselection=False,
        )
        listbox.pack(fill="both", expand=True)
        for i in range(len(variants)):
            listbox.insert("end", f"第 {i + 1} 版")

        right = tk.Frame(dialog, bg=t["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)
        self._restyle.append((right, "panel"))
        viewer = tk.Text(
            right,
            wrap="word",
            bg=t["input_bg"],
            fg=t["input_fg"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
            padx=10,
            pady=8,
        )
        viewer.pack(fill="both", expand=True)
        if not variants:
            viewer.insert("1.0", "暂无变体。\n\n使用「编辑 → 生成变体」保存回复版本。")

        def select_item(_e=None):
            sel = listbox.curselection()
            if not sel:
                return
            viewer.delete("1.0", "end")
            viewer.insert("1.0", variants[sel[0]])

        def restore_item():
            sel = listbox.curselection()
            if not sel or not variants:
                return
            self._replace_last_reply(variants[sel[0]])
            dialog.destroy()

        def copy_item():
            sel = listbox.curselection()
            if not sel or not variants:
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(variants[sel[0]])
            self._flash_status("已复制该版本")

        listbox.bind("<<ListboxSelect>>", select_item)
        bar = tk.Frame(right, bg=t["panel"])
        bar.pack(fill="x", pady=(8, 0))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "恢复此版本", restore_item, kind="primary", fsz=9).pack(side="left")
        self._mk_button(bar, "复制", copy_item, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")
        if variants:
            listbox.selection_set(0)
            select_item()

    def copy_last_code(self):
        last_code_blocks = self._current.get("last_code_blocks") or []
        if not last_code_blocks:
            messagebox.showinfo("提示", "暂无代码块可复制。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(last_code_blocks[-1])
        self._flash_status("已复制最近代码块")

    def copy_last_reply(self):
        for m in reversed(self.messages):
            if m.get("role") == "assistant" and m.get("content"):
                self.root.clipboard_clear()
                self.root.clipboard_append(m["content"])
                self._flash_status("已复制最近回复")
                return
        messagebox.showinfo("提示", "暂无助手回复可复制。")

    def toggle_md_render(self):
        self._md_render = bool(self.md_var.get())
        self.cfg["md_render"] = self._md_render
        save_config(self.cfg)
        self._render_all()

    def export_session_json(self):
        if len(self.messages) <= 1:
            messagebox.showinfo("提示", "暂无对话可导出。")
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            initialdir=HISTORY_DIR,
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json")],
            initialfile=f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("导出成功", f"已导出会话 JSON:\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _flash_status(self, msg, ms=1600):
        try:
            old = self.status_label.cget("text")
        except Exception:
            old = ""
        self.status_label.configure(text=msg)
        # 连续 flash 只恢复最新一次之前的状态；期间若有正常 update_status 写入
        # 新文本，恢复时不再覆盖它
        self._flash_gen = getattr(self, "_flash_gen", 0) + 1
        gen = self._flash_gen
        if getattr(self, "_status_after", None) is not None:
            try:
                self.root.after_cancel(self._status_after)
            except Exception:
                pass
        self._status_after = self.root.after(
            ms,
            lambda g=gen, o=old: (
                self.status_label.configure(text=o)
                if getattr(self, "_flash_gen", 0) == g
                else None
            ),
        )

    def _copy_selection(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.chat_text.get("sel.first", "sel.last"))
        self._flash_status("已复制选中内容")

    def _msg_range_at(self, text, index):
        """定位包含 index 的消息区间。

        用 tag_prevrange（Tcl 侧区间树）O(1) 定位，替代全量 tag_ranges 枚举：
        长会话每条消息 2 次 compare 的 O(n) 开销变为常数次调用。
        """
        best = None
        for tag in ("user", "assistant"):
            try:
                r = text.tag_prevrange(tag, f"{index}+1c")
            except tk.TclError:
                r = None
            if r and text.compare(r[0], "<=", index) and text.compare(index, "<", r[1]):
                if best is None or text.compare(r[0], ">", best[0][0]):
                    best = (r, tag)
        return best

    def _msg_index_at(self, text, index):
        found = self._msg_range_at(text, index)
        if found is None:
            return None
        r0, r1 = found[0]
        content = text.get(r0, r1).rstrip()
        for i in range(len(self.messages) - 1, 0, -1):
            msg = self.messages[i]
            if msg.get("role") == "user" and (msg.get("content") or "") == content:
                return i
        if found[1] == "assistant":
            for blk in reversed(self.blocks):
                if blk[0] == "content" and len(blk) > 2 and blk[2] is not None:
                    payload = blk[1]
                    try:
                        rendered = mdparse.render_markdown(payload)[0].rstrip("\n")
                    except Exception:
                        rendered = payload.rstrip("\n")
                    if rendered.rstrip() == content:
                        return blk[2]
        return None

    def _is_starred(self, msg_idx):
        try:
            role = self.messages[msg_idx].get("role")
            content = self.messages[msg_idx].get("content") or ""
            for star in self._current.get("stars", []):
                if star.get("role") == role and star.get("content") == content:
                    return True
        except (IndexError, TypeError):
            return False
        return False

    def _toggle_star(self, msg_idx):
        if not (1 <= msg_idx < len(self.messages)):
            return
        msg = self.messages[msg_idx]
        role = msg.get("role")
        if role not in ("user", "assistant"):
            return
        content = msg.get("content") or ""
        stars = self._current.setdefault("stars", [])
        for star in stars:
            if star.get("role") == role and star.get("content") == content:
                stars.remove(star)
                self._flash_status("已取消收藏")
                return
        stars.append(
            {
                "role": role,
                "content": content,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        self._flash_status("已收藏消息")

    def show_stars(self):
        stars = self._current.get("stars") or []
        t = self._theme()
        dialog, body, footer = self._dialog_shell(
            f"收藏消息（{self._session_display_name(self._current)}）", 620, 420,
            subtitle="聊天区右键消息选择「☆ 收藏此消息」；左侧列表点击查看，双击跳转",
        )
        left = tk.Frame(body, bg=t["panel"])
        left.pack(side="left", fill="y", padx=(0, 8))
        self._restyle.append((left, "panel"))
        listbox = tk.Listbox(
            left,
            width=24,
            bg=t["input_bg"],
            fg=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
            exportselection=False,
        )
        listbox.pack(fill="both", expand=True)
        for star in stars:
            content = star.get("content") or ""
            preview = " ".join(content.split())[:20] or "（空消息）"
            tag = "用户" if star.get("role") == "user" else "助手"
            listbox.insert("end", f"[{tag}] {preview}")
        right = tk.Frame(body, bg=t["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._restyle.append((right, "panel"))
        text = tk.Text(
            right,
            wrap="word",
            bg=t["input_bg"],
            fg=t["input_fg"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
            padx=12,
            pady=10,
        )
        text.pack(fill="both", expand=True)
        if not stars:
            text.insert("1.0", "暂无收藏消息。\n\n在聊天区右键任意消息，选择「☆ 收藏此消息」。")
        text.configure(state="disabled")

        def show_detail(_e=None):
            sel = listbox.curselection()
            text.configure(state="normal")
            text.delete("1.0", "end")
            if not sel or not stars:
                text.insert("1.0", "（选择左侧列表查看详情）" if stars else "暂无收藏消息。")
            else:
                star = stars[sel[0]]
                tag = "用户" if star.get("role") == "user" else "助手"
                text.insert("end", f"【{tag}】{star.get('time', '')}\n\n")
                text.insert("end", star.get("content") or "")
            text.configure(state="disabled")

        def copy_stars():
            lines = []
            for star in stars:
                tag = "用户" if star.get("role") == "user" else "助手"
                lines.append(f"【{tag}】{star.get('time', '')}\n{star.get('content')}")
            self.root.clipboard_clear()
            self.root.clipboard_append("\n\n".join(lines))
            self._flash_status("已复制收藏内容")

        def jump_to_star():
            sel = listbox.curselection()
            if not sel or not stars:
                return
            star = stars[sel[0]]
            content = star.get("content")
            role = star.get("role")
            idx = next(
                (
                    i
                    for i, m in enumerate(self.messages)
                    if m.get("role") == role and m.get("content") == content
                ),
                None,
            )
            if idx is None:
                self._flash_status("该消息已被删除，无法跳转")
                return
            dialog.destroy()
            self.after(80, lambda: self._scroll_to_message(idx))

        listbox.bind("<<ListboxSelect>>", show_detail)
        listbox.bind("<Double-1>", lambda e: jump_to_star())
        self._footer_btn(footer, "关闭", dialog.destroy)
        self._footer_btn(footer, "复制全部", copy_stars)
        self._footer_btn(footer, "跳转", jump_to_star, primary=True)
        show_detail()

    def _fork_from(self, msg_idx):
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        if not (1 <= msg_idx < len(self.messages)):
            return
        base = self.messages
        prefix = [dict(m) for m in base[: msg_idx + 1]]
        session = self._add_session()
        session["messages"] = prefix
        session["first_user"] = next(
            (mm.get("content") for mm in prefix if mm.get("role") == "user"), None
        )
        base_name = self._current.get("name") or self._session_display_name(self._current)
        session["name"] = f"分支 · {base_name}"
        self._invalidate_session_name(session)
        self._show_session_text(session)
        self.rebuild_view_from_messages()
        self.assistant_answered = False
        self._snapshot_dirty = True
        self._maybe_save_snapshot()
        self._refresh_session_list()
        note = f"[分支对话] 已从第 {msg_idx} 条消息分叉为新会话。\n"
        self._append(note, "time")
        self.blocks.append(("note", note))
        self._append("\n")
        self.blocks.append(("plain", "\n"))
        self.update_status()

    def open_global_search(self):
        query = simpledialog.askstring(
            "全局搜索", "搜索所有会话与历史消息（Ctrl+Shift+F）:", parent=self.root
        )
        if query is None:
            return
        query = query.strip()
        if not query:
            return
        self.search_all_sessions(query)

    @staticmethod
    def _make_snippet(content, q, width=60):
        idx = content.lower().find(q)
        if idx < 0:
            return content[:width].replace("\n", " ")
        start = max(0, idx - 20)
        snippet = content[start : start + width].replace("\n", " ")
        if start > 0:
            snippet = "…" + snippet
        return snippet

    def search_all_sessions(self, query):
        """后台线程搜索内存会话与历史文件，结果经队列回传。"""
        if not query.strip():
            return
        self._flash_status("正在搜索全部会话…")

        def worker():
            results = []
            q = query.lower()
            for session in self._sessions:
                sid = session.get("id")
                name = self._session_display_name(session)
                for i, msg in enumerate(session.get("messages") or []):
                    content = (msg.get("content") or "") + "\n" + (msg.get("reasoning_content") or "")
                    if q in content.lower():
                        results.append(
                            ("memory", sid, name, i, msg.get("role"), self._make_snippet(content, q))
                        )
            if os.path.isdir(SESSIONS_DIR):
                for fn in os.listdir(SESSIONS_DIR):
                    if not fn.endswith(".json"):
                        continue
                    try:
                        with open(os.path.join(SESSIONS_DIR, fn), "r", encoding="utf-8") as f:
                            data = json.load(f)
                        name = str(data.get("name") or "") or "未命名会话"
                        for i, msg in enumerate(data.get("messages") or []):
                            content = (msg.get("content") or "") + "\n" + (msg.get("reasoning_content") or "")
                            if q in content.lower():
                                results.append(
                                    ("file", str(data.get("id") or fn[:-5]), name, i, msg.get("role"), self._make_snippet(content, q))
                                )
                    except Exception:
                        continue
            self._ui_queue.put(("search_all_done", results))

        threading.Thread(target=worker, daemon=True).start()

    def _show_search_results(self, results):
        t = self._theme()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title(f"全局搜索结果（{len(results)} 条）")
        dialog.geometry("640x460")
        dialog.transient(self.root)
        listbox = tk.Listbox(
            dialog,
            width=80,
            bg=t["input_bg"],
            fg=t["input_fg"],
            selectbackground=t["selection"],
            selectforeground=t["accent_text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=t["border"],
            highlightcolor=t["accent"],
            exportselection=False,
        )
        listbox.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        if not results:
            listbox.insert("end", "（无匹配结果）")
        for src, sid, name, idx, role, snippet in results:
            tag = "用户" if role == "user" else ("助手" if role == "assistant" else role)
            listbox.insert("end", f"【{name}】{tag} · 第 {idx} 条")
            listbox.insert("end", f"    {snippet}")
            listbox.itemconfig("end", fg=t["text_sec"])

        def jump():
            sel = listbox.curselection()
            if not sel or not results:
                return
            sel = sel[0] // 2
            if sel >= len(results):
                return
            src, sid, name, idx, role, snippet = results[sel]
            if self.busy:
                messagebox.showinfo("提示", "请先停止当前生成。")
                return
            if src == "memory":
                target = next((s for s in self._sessions if s.get("id") == sid), None)
                if target is None:
                    return
                if target is not self._current:
                    old = self._current
                    if (
                        not old.get("ephemeral")
                        and not self.cfg.get("privacy_mode")
                        and old.get("messages")
                    ):
                        try:
                            self.save_session_to_file(old)
                        except Exception:
                            logging.exception("全局搜索跳转前保存旧会话失败")
                    self._current = target
                    self._ctx_counts = None
                    self._paged_render = None
                    self._paged_render_after = None
                    self._snapshot_dirty = True
                    self._show_session_text(target)
                    self._refresh_session_list()
                    self.update_status()
                    self._update_context_bar()
            else:
                if self.load_session_from_file(sid) is None:
                    messagebox.showinfo("提示", "历史会话文件载入失败。")
                    return
            dialog.destroy()
            self.after(80, lambda: self._scroll_to_message(idx))

        def scroll_prev():
            sel = listbox.curselection()
            if sel and sel[0] > 1:
                listbox.selection_clear(0, "end")
                listbox.selection_set(sel[0] - 2)
                listbox.see(sel[0] - 2)

        listbox.bind("<Double-1>", lambda e: jump())
        bar = tk.Frame(dialog, bg=t["panel"])
        bar.pack(fill="x", padx=12, pady=(8, 12))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "跳转", jump, kind="primary", fsz=9).pack(side="left")
        self._mk_button(bar, "关闭", dialog.destroy, fsz=9).pack(side="right")

    def _scroll_to_message(self, msg_idx):
        if not (1 <= msg_idx < len(self.messages)):
            return
        msg = self.messages[msg_idx]
        content = (msg.get("content") or "").strip()
        if not content:
            return
        try:
            # 原文锚点（user 消息不渲染 md，直接匹配）；assistant 消息先试原文，
            # 再试 md 渲染后的文本（渲染会改写链接/粗体等，原文锚点找不到）
            anchors = []
            raw = " ".join(content.split())[:20]
            if raw:
                anchors.append(raw)
            if msg.get("role") == "assistant":
                try:
                    rendered = mdparse.render_markdown(content)[0]
                    rendered = " ".join(rendered.split())[:20]
                    if rendered and rendered != raw:
                        anchors.append(rendered)
                except Exception:
                    pass
            for anchor in anchors:
                start = "1.0"
                while True:
                    pos = self.chat_text.search(anchor, start, stopindex="end")
                    if not pos:
                        break
                    # 校验命中是否属于目标消息：多条消息同开头（重复提问/相同代码块）
                    # 时跳过前面的同锚点命中，避免跳错位置
                    if self._msg_index_at(self.chat_text, pos) == msg_idx:
                        self.chat_text.see(pos)
                        self._follow_bottom = False  # 手动定位后停止自动跟随
                        self._flash_status(f"已定位到消息 #{msg_idx}")
                        return
                    start = f"{pos}+1c"
            self._flash_status(f"消息 #{msg_idx} 的内容已在视图中（未找到精确位置）")
        except tk.TclError:
            pass

    @staticmethod
    def _timeline_items(blocks):
        """从渲染块构建会话轨迹条目：[(msg_idx 或 None, 显示文本, 可跳转)]。

        混合时间线：用户消息 / 助手回复 / 思考过程 / 工具调用（含参数）/
        系统事件（压缩、任务完成、中断等 note/error）。
        """
        items = []
        for b in blocks:
            kind = b[0]
            if kind == "user":
                items.append((None, "💬 用户 " + (" ".join(str(b[1]).split())[:40] or "（空）"), False))
            elif kind == "content":
                idx = b[2] if len(b) > 2 and isinstance(b[2], int) else None
                items.append((idx, "🤖 助手 " + (" ".join(str(b[1]).split())[:40] or "（空）"), idx is not None))
            elif kind == "thinking":
                items.append((None, "🧠 思考 " + (" ".join(str(b[1]).split())[:30] or ""), False))
            elif kind == "tool":
                name, args, result = b[1][0], b[1][1], b[1][2]
                args_s = ""
                try:
                    args_s = json.dumps(args, ensure_ascii=False)[:40] if args else ""
                except (TypeError, ValueError):
                    args_s = str(args)[:40]
                res = " ".join(str(result or "").split())[:30]
                mark = "✅" if not str(result or "").startswith(_dc.TOOL_RESULT_FAIL_PREFIXES) else "❌"
                items.append((None, f"🔧 {mark} {name} {args_s} → {res}", False))
            elif kind == "note":
                text = str(b[1]).strip()
                if text:
                    items.append((None, "📌 " + text[:40], False))
            elif kind == "error":
                items.append((None, "⚠ " + str(b[1]).strip()[:40], False))
        return items

    def show_session_timeline(self):
        """会话轨迹：按时间顺序列出消息/思考/工具调用/系统事件的混合时间线，
        双击/跳转定位到聊天区原文（Harness 式 Trajectory 的鲸语版）。"""
        t = self._theme()
        dialog, body, footer = self._dialog_shell(
            "会话轨迹", 620, 480,
            subtitle="完整轨迹：消息 + 思考 + 工具调用 + 系统事件；双击助手/用户行定位原文",
        )
        frame = tk.Frame(body, bg=t["panel"])
        frame.pack(fill="both", expand=True)
        self._restyle.append((frame, "panel"))
        listbox = tk.Listbox(
            frame, bg=t["input_bg"], fg=t["input_fg"],
            selectbackground=t["selection"], selectforeground=t["accent_text"],
            activestyle="none", relief="flat", borderwidth=0,
            highlightthickness=0, font=(FONT_FAMILY, 9), exportselection=False,
        )
        sb = tk.Scrollbar(
            frame, orient="vertical", command=listbox.yview,
            relief="flat", bd=0, highlightthickness=0, width=10,
        )
        sb.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)
        listbox.configure(yscrollcommand=sb.set)
        sb.configure(
            bg=t["disabled"], activebackground=t["text_sec"],
            troughcolor=t["input_bg"],
            highlightbackground=t["input_bg"], highlightcolor=t["input_bg"],
        )
        labels = self._timeline_items(self.blocks)
        if len(labels) > 300:
            labels = labels[-300:]
        for _idx, text_, _j in labels:
            listbox.insert("end", text_)
        if not labels:
            listbox.insert("end", "（会话暂无轨迹）")

        def jump():
            sel = listbox.curselection()
            if not sel or sel[0] >= len(labels):
                return
            idx, _text, jumpl = labels[sel[0]]
            if not jumpl or idx is None:
                self._flash_status("该条目无原文可跳转（工具/事件/思考）")
                return
            dialog.destroy()
            self._scroll_to_message(idx)

        listbox.bind("<Double-1>", lambda e: jump())
        listbox.bind("<Return>", lambda e: jump())
        self._footer_hint(footer, f"{len(labels)} 个轨迹条目 · 消息/思考/工具/事件")
        self._footer_btn(footer, "关闭", dialog.destroy)
        self._footer_btn(footer, "跳转", jump, primary=True)


    def show_batch_task(self):
        """批量任务：多选文件 + 指令模板（{file} 占位），一次发送让 AI 逐个处理。"""
        files = filedialog.askopenfilenames(
            title="选择要批量处理的文件",
            parent=self.root,
            filetypes=[("所有文件", "*.*")],
        )
        if not files:
            return
        template = simpledialog.askstring(
            "批量任务",
            f"已选 {len(files)} 个文件。\n输入任务指令（用 {{{{file}}}} 表示每个文件路径）：\n"
            "示例：读取 {file} 并总结要点",
            parent=self.root,
        )
        if template is None or not template.strip():
            return
        file_list = "\n".join(f"- {p}" for p in files)
        prompt = (
            f"【批量任务】请对以下 {len(files)} 个文件逐个执行同一指令，每个文件都处理并单独汇报结果，不要遗漏。\n"
            f"指令模板：{template.strip()}\n"
            f"（模板中的 {{{{file}}}} 请替换为对应的文件完整路径）\n\n"
            f"文件列表：\n{file_list}\n\n"
            "请依次处理，逐个说明处理结果。"
        )
        self._clear_placeholder()
        self.send(text=prompt)

    @staticmethod
    def load_user_roles():
        """用户自定义角色（可增删改）：[{"name", "prompt", "thinking", "desc", "category"}]。"""
        try:
            if os.path.exists(USER_ROLES_PATH):
                with open(USER_ROLES_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [r for r in data if isinstance(r, dict) and r.get("name") and r.get("prompt")]
        except Exception:
            logging.exception("读取用户角色失败")
        return []

    @staticmethod
    def save_user_roles(roles):
        """保存用户角色列表（原子写）；角色变更后清识别缓存。"""
        try:
            tmp = USER_ROLES_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(roles, f, ensure_ascii=False, indent=1)
            os.replace(tmp, USER_ROLES_PATH)
            AssistantApp._role_cache["prompt"] = None  # 角色集变更，缓存失效
            return True
        except Exception:
            logging.exception("保存用户角色失败")
            return False

    _role_cache = {"prompt": None, "name": None}  # 角色识别缓存（状态栏高频调用防读盘）

    @staticmethod
    def load_all_roles():
        """全部角色 = 内置预设（只读）+ 用户自定义。"""
        roles = []
        for name, role in ROLES.items():
            roles.append({"name": name, "prompt": str(role.get("prompt") or ""),
                          "thinking": str(role.get("thinking") or "high"),
                          "desc": str(role.get("desc") or ""), "builtin": True})
        for u in AssistantApp.load_user_roles():
            u["builtin"] = False
            roles.append(u)
        return roles

    @staticmethod
    def _current_role_name(prompt):
        """识别当前生效角色：system_prompt 与任一预设/用户角色完全一致 → 角色名；否则「自定义」。

        结果按提示词缓存（状态栏每秒级调用，避免反复读盘匹配）。
        """
        prompt = str(prompt or "")
        cache = AssistantApp._role_cache
        if cache["prompt"] == prompt:
            return cache["name"]
        name = "自定义"
        for r in AssistantApp.load_all_roles():
            if str(r.get("prompt") or "") == prompt:
                name = str(r.get("name") or "")
                break
        cache["prompt"] = prompt
        cache["name"] = name
        return name

    def show_roles(self):
        """角色与提示词：完整管理——内置/用户角色（增删改/分类）+ 自定义编辑。

        概念模型：角色 = 系统提示词预设。左侧选角色应用；「✍ 自定义」直接编辑
        当前提示词；用户角色可新增/编辑/删除/分类。当前生效角色自动识别高亮。
        """
        t = self._theme()
        dialog, body, footer = self._dialog_shell(
            "角色与提示词", 600, 480,
            subtitle="角色 = 系统提示词预设 · 内置只读，用户角色可增删改分类 · 自定义=直接编辑当前提示词",
        )
        cur_prompt = str(self.cfg.get("system_prompt") or DEFAULT_SYSTEM_PROMPT)
        cur_role = self._current_role_name(cur_prompt)
        roles = self.load_all_roles()
        # 列表结构：[role...] + [自定义]（自定义固定为最后一项）
        user_roles = [r for r in roles if not r.get("builtin")]
        builtin_roles = [r for r in roles if r.get("builtin")]
        CUSTOM_IDX = len(roles)

        left = tk.Frame(body, bg=t["panel"])
        left.pack(side="left", fill="y", padx=(0, 8))
        self._restyle.append((left, "panel"))
        listbox = tk.Listbox(
            left, width=24, bg=t["input_bg"], fg=t["input_fg"],
            selectbackground=t["selection"], selectforeground=t["accent_text"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], exportselection=False,
        )
        lb_sb = tk.Scrollbar(left, orient="vertical", command=listbox.yview,
                             relief="flat", bd=0)
        listbox.configure(yscrollcommand=lb_sb.set)
        lb_sb.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)

        def fill_list():
            listbox.delete(0, "end")
            listbox.insert("end", "── 内置角色 ──")
            for r in builtin_roles:
                mark = "✅" if r["name"] == cur_role else "⭘"
                listbox.insert("end", f"{mark} {r['name']}")
            if user_roles:
                listbox.insert("end", "── 我的角色 ──")
                cats = {}
                for r in user_roles:
                    cats.setdefault(str(r.get("category") or "未分类"), []).append(r)
                for cat, items in cats.items():
                    listbox.insert("end", f"  · {cat}")
                    for r in items:
                        mark = "✅" if r["name"] == cur_role else "⭘"
                        listbox.insert("end", f"{mark} {r['name']}")
            listbox.insert("end", "──────────")
            listbox.insert("end", "✍ 自定义")

        fill_list()

        # 列表行 → role 名 映射（跳过分组标题）
        def row_to_role(row_idx):
            items = list(listbox.get(0, "end"))
            if 0 <= row_idx < len(items):
                label = items[row_idx]
                if label.startswith(("──", "  ·", "✍", "────")):
                    return None
                return label[2:].strip()  # 去掉 ✅/⭘ 前缀
            return None

        right = tk.Frame(body, bg=t["panel"])
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._restyle.append((right, "panel"))
        desc_lbl = self._lbl(right, "", role="label_sec", bg="panel",
                             font=(FONT_FAMILY, 9), wraplength=420, justify="left")
        desc_lbl.pack(anchor="w", pady=(0, 4))
        think_lbl = self._lbl(right, "", role="label_sec", bg="panel",
                              font=(FONT_FAMILY, 9))
        think_lbl.pack(anchor="w", pady=(0, 4))
        prompt_text = tk.Text(
            right, wrap="word", height=10, bg=t["input_bg"], fg=t["input_fg"],
            insertbackground=t["input_fg"], relief="flat", highlightthickness=1,
            highlightbackground=t["border"], highlightcolor=t["accent"],
            font=(FONT_FAMILY, 9), padx=10, pady=8,
        )
        prompt_text.pack(fill="both", expand=True)
        prompt_text.configure(state="disabled")
        apply_btn = self._mk_button(right, "应用此角色", lambda: apply_selected(), kind="primary", fsz=9)
        apply_btn.pack(anchor="e", pady=(8, 0))

        def show_role(role):
            desc_lbl.configure(text=f"描述：{role.get('desc', '')}"
                                    f"{'（内置）' if role.get('builtin') else '（我的' + ('·' + str(role.get('category')) if role.get('category') else '') + '）'}")
            think_lbl.configure(text=f"思考档位：{THINKING_MODES.get(str(role.get('thinking') or 'high'), str(role.get('thinking') or 'high'))}")
            prompt_text.configure(state="normal")
            prompt_text.delete("1.0", "end")
            prompt_text.insert("1.0", role.get("prompt") or "")
            prompt_text.configure(state="disabled")
            apply_btn.configure(text="应用此角色", state="normal")

        def show_custom():
            desc_lbl.configure(text="自定义提示词：直接编辑当前生效的系统提示词（修改后点「保存自定义」）")
            think_lbl.configure(text="思考档位：手动在设置面板选择")
            prompt_text.configure(state="normal")
            prompt_text.delete("1.0", "end")
            prompt_text.insert("1.0", cur_prompt)
            apply_btn.configure(text="保存自定义", state="normal")

        def on_select(_e=None):
            sel = listbox.curselection()
            if not sel:
                return
            name = row_to_role(sel[0])
            if name == "自定义":
                show_custom()
            elif name:
                role = next((r for r in roles if r["name"] == name), None)
                if role:
                    show_role(role)

        def apply_selected():
            sel = listbox.curselection()
            if not sel:
                return
            name = row_to_role(sel[0])
            if name == "自定义":
                new_prompt = prompt_text.get("1.0", "end").strip()
                dialog.destroy()
                self._apply_custom_prompt(new_prompt)
            elif name:
                role = next((r for r in roles if r["name"] == name), None)
                if role:
                    dialog.destroy()
                    self.apply_role(name)

        # ── 角色管理：新增 / 编辑 / 删除（仅用户角色） ──
        def _selected_role():
            sel = listbox.curselection()
            if not sel:
                return None
            name = row_to_role(sel[0])
            if not name or name == "自定义":
                return None
            return next((r for r in roles if r["name"] == name), None)

        def edit_role_dialog(role=None):
            """角色编辑对话框（新增/编辑共用）。"""
            sub_t = self._theme()
            sub = tk.Toplevel(self.root, bg=sub_t["panel"])
            sub.title("新增角色" if role is None else "编辑角色")
            sub.geometry(self._center_geometry(520, 400))
            sub.transient(dialog)
            sub.bind("<Escape>", lambda e: sub.destroy())
            sub_body = tk.Frame(sub, bg=sub_t["panel"])
            sub_body.pack(fill="both", expand=True, padx=16, pady=(12, 0))
            self._restyle.append((sub_body, "panel"))
            fields = {}
            for key, label in (("name", "角色名称"), ("category", "分类（如 我的/写作/办公）"),
                               ("desc", "描述（一句话说明用途）")):
                self._lbl(sub_body, label, role="label_sec", bg="panel",
                          font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(4, 2))
                var = tk.StringVar(value=str(role.get(key, "")) if role else "")
                ttk.Entry(sub_body, textvariable=var).pack(fill="x")
                fields[key] = var
            self._lbl(sub_body, "思考档位", role="label_sec", bg="panel",
                      font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(4, 2))
            think_var = tk.StringVar(value=str(role.get("thinking") or "high") if role else "high")
            ttk.Combobox(sub_body, textvariable=think_var, values=list(THINKING_MODES.keys()),
                         state="readonly").pack(fill="x")
            self._lbl(sub_body, "系统提示词（角色人格全文）", role="label_sec", bg="panel",
                      font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(4, 2))
            prompt_edit = tk.Text(
                sub_body, wrap="word", height=7, bg=sub_t["input_bg"], fg=sub_t["input_fg"],
                insertbackground=sub_t["input_fg"], relief="flat", highlightthickness=1,
                highlightbackground=sub_t["border"], highlightcolor=sub_t["accent"],
                font=(FONT_FAMILY, 9), padx=8, pady=6,
            )
            prompt_edit.pack(fill="both", expand=True, pady=(0, 8))
            if role:
                prompt_edit.insert("1.0", role.get("prompt") or "")
            bar = tk.Frame(sub, bg=sub_t["panel"])
            bar.pack(fill="x", padx=16, pady=(6, 12))
            self._restyle.append((bar, "panel"))

            def save_edit():
                name = fields["name"].get().strip()
                prompt = prompt_edit.get("1.0", "end").strip()
                if not name or not prompt:
                    messagebox.showinfo("提示", "名称与系统提示词必填。")
                    return
                if role is None:
                    if any(r["name"] == name for r in roles):
                        messagebox.showinfo("提示", f"角色「{name}」已存在。")
                        return
                new_role = {
                    "name": name,
                    "prompt": prompt,
                    "thinking": think_var.get(),
                    "desc": fields["desc"].get().strip(),
                    "category": fields["category"].get().strip() or "我的",
                }
                user_roles_local = self.load_user_roles()
                if role is None:
                    user_roles_local.append(new_role)
                else:
                    user_roles_local = [
                        (new_role if r["name"] == role["name"] else r) for r in user_roles_local
                    ]
                if self.save_user_roles(user_roles_local):
                    sub.destroy()
                    dialog.destroy()
                    self.show_roles()
                    self._flash_status(f"角色「{name}」已保存")
                else:
                    messagebox.showerror("保存失败", "无法写入用户角色文件。")

            self._mk_button(bar, "取消", sub.destroy, fsz=9).pack(side="right")
            self._mk_button(bar, "保存", save_edit, kind="primary", fsz=9).pack(side="right", padx=(0, 8))

        def add_role():
            edit_role_dialog(None)

        def edit_role():
            r = _selected_role()
            if not r:
                messagebox.showinfo("提示", "请选择一个角色（内置角色不可编辑，可复制内容到新增）。")
                return
            if r.get("builtin"):
                messagebox.showinfo("提示", "内置角色只读。可在「新增」中创建副本后编辑。")
                return
            edit_role_dialog(r)

        def del_role():
            r = _selected_role()
            if not r:
                messagebox.showinfo("提示", "请先选择一个角色。")
                return
            if r.get("builtin"):
                messagebox.showinfo("提示", "内置角色不可删除。")
                return
            in_use = (self._current_role_name(self.cfg.get("system_prompt", "")) == r["name"])
            note = "\n（当前正在使用此角色，删除后当前会话将显示为「自定义」，人格提示词保持不变）" if in_use else ""
            if not messagebox.askyesno("删除角色", f"确认删除角色「{r['name']}」？{note}"):
                return
            user_roles_local = [x for x in self.load_user_roles() if x.get("name") != r["name"]]
            self.save_user_roles(user_roles_local)
            self._update_role_label()
            self._flash_status(f"已删除角色「{r['name']}」")
            dialog.destroy()
            self.show_roles()

        listbox.bind("<<ListboxSelect>>", on_select)
        listbox.bind("<Double-1>", lambda e: apply_selected())
        listbox.bind("<Return>", lambda e: apply_selected())
        default_idx = CUSTOM_IDX
        for i, r in enumerate(roles):
            if r["name"] == cur_role:
                default_idx = i + 1  # 列表首行是分组标题
                break
        if default_idx != CUSTOM_IDX:
            try:
                listbox.selection_set(default_idx)
                listbox.activate(default_idx)
                on_select()
            except tk.TclError:
                pass
        else:
            listbox.selection_set(listbox.size() - 1)
            listbox.activate(listbox.size() - 1)
            on_select()
        # 管理按钮
        bar = tk.Frame(body, bg=t["panel"])
        bar.pack(fill="x", side="bottom", pady=(8, 0))
        self._restyle.append((bar, "panel"))
        self._mk_button(bar, "＋ 新增角色", add_role, fsz=9).pack(side="left")
        self._mk_button(bar, "✏ 编辑", edit_role, fsz=9).pack(side="left", padx=(6, 0))
        self._mk_button(bar, "🗑 删除", del_role, fsz=9).pack(side="left", padx=(6, 0))
        self._lbl(bar, "内置角色只读 · 用户角色可增删改分类",
                  role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="right")
        self._footer_hint(footer, f"当前角色：{cur_role} · 修改会破坏前缀缓存（{self._cache_cost_hint()}）")
        self._footer_btn(footer, "关闭", dialog.destroy)

    def _apply_custom_prompt(self, new_prompt):
        """保存自定义系统提示词（含缓存警示，逻辑吸收原 edit_system_prompt）。"""
        new_prompt = str(new_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
        if (
            new_prompt != self.cfg.get("system_prompt")
            and len(self.messages) > 1
            and not self.cfg.get("privacy_mode")
        ):
            if not messagebox.askyesno(
                "缓存提示",
                f"修改系统提示词会破坏前缀缓存（{self._cache_cost_hint()}），并影响所有会话。\n"
                "建议保持固定提示词，如需新提示词可新建会话后修改。\n仍要修改吗？",
            ):
                return
        self.cfg["system_prompt"] = new_prompt
        if self.messages:
            self.messages[0]["content"] = new_prompt
        save_config(self.cfg)
        self._update_role_label()
        self._flash_status("已保存自定义提示词")

    def _update_role_label(self):
        """更新设置面板「当前角色」显示（角色应用/自定义保存后调用）。"""
        lbl = getattr(self, "_role_lbl", None)
        if lbl is not None:
            try:
                lbl.configure(
                    text=f"🎭 当前角色：{self._current_role_name(self.cfg.get('system_prompt', ''))}"
                )
            except tk.TclError:
                pass

    def apply_role(self, name):
        """应用角色（内置或用户自定义）：更新 system_prompt + 思考档（含缓存警示，联动角色显示）。"""
        role = next((r for r in self.load_all_roles() if r.get("name") == name), None)
        if not role:
            return
        new_prompt = str(role.get("prompt") or "")
        if (
            new_prompt != self.cfg.get("system_prompt")
            and len(self.messages) > 1
            and not self.cfg.get("privacy_mode")
        ):
            if not messagebox.askyesno(
                "缓存提示",
                f"应用角色「{name}」将修改系统提示词，会破坏前缀缓存"
                f"（{self._cache_cost_hint()}），并影响所有会话。\n仍要应用吗？",
            ):
                return
        self.cfg["system_prompt"] = new_prompt
        if self.messages:
            self.messages[0]["content"] = new_prompt
        thinking = role.get("thinking")
        if thinking in THINKING_MODES:
            self.cfg["thinking"] = thinking
        save_config(self.cfg)
        try:
            self.thinking_combo.set(THINKING_MODES[thinking])
        except Exception:
            pass
        self._update_role_label()
        self._flash_status(f"🎭 已应用角色「{name}」")

    def show_command_palette(self):
        """命令面板（Ctrl+K）：输入过滤全部常用操作，Enter 执行。"""
        t = self._theme()
        if getattr(self, "_palette", None) is not None:
            try:
                if self._palette.winfo_exists():
                    self._palette.destroy()
            except tk.TclError:
                pass
            self._palette = None
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        self._palette = dialog
        dialog.title("命令面板")
        dialog.overrideredirect(True)
        dialog.transient(self.root)
        dialog.geometry("420x360")
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        entry = tk.Entry(
            dialog, bg=t["input_bg"], fg=t["input_fg"], insertbackground=t["input_fg"],
            relief="flat", highlightthickness=1, highlightbackground=t["border"],
            highlightcolor=t["accent"], font=(FONT_FAMILY, 10),
        )
        entry.pack(fill="x", padx=10, pady=(10, 6))
        hint = tk.Label(
            dialog, text="Esc 关闭 · ↑/↓ 选择 · Enter 执行", bg=t["panel"],
            fg=t["text_sec"], font=(FONT_FAMILY, 9),
        )
        hint.pack(anchor="e", padx=12, pady=(0, 2))
        listbox = tk.Listbox(
            dialog, bg=t["panel"], fg=t["text"],
            selectbackground=t["selection"], selectforeground=t["accent_text"],
            activestyle="none", relief="flat", borderwidth=0,
            highlightthickness=0, font=(FONT_FAMILY, 9), exportselection=False,
        )
        listbox.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        actions = [
            ("新会话", self.new_conversation),
            ("新建临时会话", lambda: self.add_tab(ephemeral=True)),
            ("生成会话纪要", self.generate_session_summary),
            ("会话结构导航", self.show_session_timeline),
            ("角色与提示词", self.show_roles),
            ("切换主题", self.toggle_theme),
            ("查余额", self.check_balance),
            ("用量统计", self.show_stats),
            ("预算设置", self.edit_budget),
            ("上下文详情", self.show_context_details),
            ("模型对比", self.compare_models),
            ("工具设置", self.edit_tools),
            ("权限设置", self.edit_permissions),
            ("工作目录", self.choose_working_dir),
            ("工作区文件树", self.show_workspace_tree),
            ("最近产物", self.show_recent_outputs),
            ("提示词库管理", self.manage_prompts),
            ("定时任务", self.manage_schedules),
            ("推送与数据库配置", self.show_external_config),
            ("进程终端", self.show_process_terminal),
            ("导出历史", self.export_history),
            ("导入会话", self.import_session_file),
            ("数据清理", self.show_cleanup),
            ("关于", self.show_about),
        ]

        def refresh(_e=None):
            q = entry.get().strip().lower()
            listbox.delete(0, "end")
            for label, _fn in actions:
                if not q or q in label.lower():
                    listbox.insert("end", label)
            if listbox.size():
                listbox.selection_set(0)
                listbox.activate(0)

        def run(_e=None):
            sel = listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            label = listbox.get(idx)
            fn = dict((l, f) for l, f in actions)[label]
            dialog.destroy()
            self._palette = None
            try:
                fn()
            except Exception:
                logging.exception("命令面板执行失败: %s", label)

        entry.bind("<KeyRelease>", refresh)
        entry.bind("<Return>", run)
        # 返回 "break"：阻止 Entry 默认光标移动与列表激活状态混杂
        entry.bind("<Down>", lambda e: (
            listbox.selection_clear(0, "end"),
            listbox.selection_set(min(listbox.size() - 1, (listbox.curselection() or [0])[0] + 1)),
            listbox.activate((listbox.curselection() or [0])[0]),
            "break")[3])
        entry.bind("<Up>", lambda e: (
            listbox.selection_clear(0, "end"),
            listbox.selection_set(max(0, (listbox.curselection() or [0])[0] - 1)),
            listbox.activate((listbox.curselection() or [0])[0]),
            "break")[3])
        listbox.bind("<Button-1>", lambda e: None)
        listbox.bind("<Double-1>", run)
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        refresh()
        try:
            cx = self.root.winfo_rootx() + (self.root.winfo_width() - 420) // 2
            cy = self.root.winfo_rooty() + (self.root.winfo_height() - 360) // 3
            dialog.geometry(f"+{cx}+{cy}")
        except Exception:
            pass
        # 注意：不用 grab_set——无边框窗口 + grab 会拦截主窗口全部事件，
        # 且没有可见关闭按钮，极易表现为"程序卡死"（Windows 上还会与
        # 输入法/焦点组合锁死事件循环）。非模态 + Esc 关闭更安全，
        # 主窗口始终可交互，面板残留无害（再次 Ctrl+K 会先销毁旧面板）。
        entry.focus_force()
        entry.focus_set()

    def run_task_template(self, template_name):
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        target = simpledialog.askstring(
            "Agent 任务", f"{template_name}\n请输入任务目标:", parent=self.root
        )
        if target is None:
            return
        target = target.strip()
        if not target:
            return
        prompt = TASK_TEMPLATES[template_name] + target
        self._clear_placeholder()
        self.send(text=prompt)

    def show_context_details(self):
        t = self._theme()
        # 复用 worker 已算好的计数（若有），避免主线程全量 tiktoken（1M 上下文可卡 1-2s）
        with self._messages_lock:
            msgs = list(self.messages)
        if getattr(self, "_ctx_counts", None) is None or len(self._ctx_counts) != len(msgs):
            # 长度不一致 = worker 压缩/裁剪过，计数已失效，按当前消息重算
            self._ctx_counts = tokens.message_token_counts(msgs)
        counts = self._ctx_counts
        total = tokens.BASE_OVERHEAD + sum(counts)

        def cat(msg):
            if msg.get("role") == "system":
                if (msg.get("content") or "").startswith("[历史对话摘要]"):
                    return "历史摘要"
                return "系统提示词"
            if msg.get("role") == "user":
                return "用户消息"
            if msg.get("role") == "tool":
                return "工具调用记录"
            if msg.get("role") == "assistant":
                if msg.get("reasoning_content"):
                    return "思考过程（含回复）"
                return "助手回复"
            return "其他"

        groups = {}
        for i, msg in enumerate(msgs):
            key = cat(msg)
            g = groups.setdefault(key, {"count": 0, "tokens": 0, "msgs": 0})
            g["count"] += 1
            g["tokens"] += counts[i] if i < len(counts) else 0
            g["msgs"] += 1

        dialog, body, footer = self._dialog_shell(
            "上下文占用详情", 480, 440,
            subtitle="当前上下文的构成占比，帮助了解 1M 窗口的使用情况",
        )
        self._lbl(
            body,
            f"当前上下文估算 {total:,} token / "
            f"{MODELS.get(self.model_combo.get(), {}).get('max_context_tokens', MAX_CONTEXT_TOKENS):,}",
            role="label_sec",
            bg="panel",
            font=(FONT_FAMILY, 10, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        if self.last_usage:
            lu = self.last_usage
            hit = lu.get("cache_hit", 0)
            miss = lu.get("cache_miss", 0)
            ratio = f"{hit / (hit + miss):.0%}" if (hit + miss) else "-"
            self._lbl(
                body,
                f"最近一轮：输入 {lu.get('prompt', 0):,} token（缓存命中 {hit:,} / 未命中 {miss:,}，占比 {ratio}）"
                f" · 输出 {lu.get('completion', 0):,} token",
                role="label_sec",
                bg="panel",
                font=(FONT_FAMILY, 9),
            ).pack(anchor="w", pady=(0, 10))
        order = ["系统提示词", "历史摘要", "用户消息", "助手回复", "思考过程（含回复）", "工具调用记录", "其他"]
        max_tokens = max((g["tokens"] for g in groups.values()), default=1)
        for key in order:
            g = groups.get(key)
            if not g:
                continue
            row = tk.Frame(body, bg=t["panel"])
            row.pack(fill="x", pady=3)
            self._restyle.append((row, "panel"))
            pct = g["tokens"] / total if total else 0
            bar = ttk.Progressbar(
                row,
                style="Context.Horizontal.TProgressbar",
                maximum=max_tokens,
                value=g["tokens"],
                length=200,
            )
            bar.pack(side="left")
            self._lbl(
                row,
                f"{key}: {g['tokens']:,} ({pct:.1%}) · {g['msgs']} 条",
                bg="panel",
                font=(FONT_FAMILY, 9),
            ).pack(side="left", padx=(10, 0))
        self._footer_btn(footer, "关闭", dialog.destroy)

    def compare_models(self):
        """模型对比（参数化）：弹窗选择/输入任意两个模型，分会话对比结果。

        可选模型列表 = 内置模型 + 各 Profile 配置的模型名（模型名可自由输入）。
        """
        text = self.input_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("提示", "请先在输入框写下要对比的问题。")
            return
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        cur = self.model_combo.get().strip()
        other = "deepseek-v4-pro" if cur == "deepseek-v4-flash" else "deepseek-v4-flash"
        models = list(MODELS.keys())
        try:
            for p in (load_profiles().get("profiles") or {}).values():
                m_ = str(p.get("model") or "").strip()
                if m_ and m_ not in models:
                    models.append(m_)
        except Exception:
            pass
        t = self._theme()
        dialog, body, footer = self._dialog_shell(
            "模型对比", 460, 260,
            subtitle="同一问题分别用两个模型生成回复，结果分会话对比（模型名可自由输入）",
        )
        var1 = tk.StringVar(value=cur)
        var2 = tk.StringVar(value=other)
        for label, var in (("模型 A", var1), ("模型 B", var2)):
            row = tk.Frame(body, bg=t["panel"])
            row.pack(fill="x", pady=4)
            self._restyle.append((row, "panel"))
            self._lbl(row, label, role="label_sec", bg="panel",
                      font=(FONT_FAMILY, 9), width=8).pack(side="left")
            ttk.Combobox(row, textvariable=var, values=models,
                         state="normal").pack(side="left", fill="x", expand=True)
        self._lbl(
            body, f"待对比问题：{text[:60]}{'…' if len(text) > 60 else ''}",
            role="label_sec", bg="panel", font=(FONT_FAMILY, 9), wraplength=400, justify="left",
        ).pack(anchor="w", pady=(8, 0))

        def start():
            a = var1.get().strip() or cur
            b = var2.get().strip() or other
            if a == b:
                messagebox.showinfo("提示", "两个模型相同，请选择不同的模型。")
                return
            dialog.destroy()
            self._compare_pending = text
            self._compare_models = [a, b]
            self._compare_idx = 0
            self.after(50, self._compare_step)

        self._footer_btn(footer, "取消", dialog.destroy)
        self._footer_btn(footer, "开始对比", start, primary=True)

    def _compare_step(self):
        if self._compare_idx >= len(self._compare_models):
            self._compare_pending = None
            self._flash_status("模型对比完成，两个会话均已生成回复")
            return
        model = self._compare_models[self._compare_idx]
        self._compare_idx += 1
        self.add_tab()
        self.model_combo.set(model)
        self.cfg["model"] = model
        self._current["name"] = f"对比·{model}"
        self._refresh_session_list()
        self.send(text=self._compare_pending)
        self.after(200, self._poll_compare)

    def _poll_compare(self):
        if self._compare_pending is None:
            return  # 用户已停止对比流程
        if self.busy:
            self.after(200, self._poll_compare)
        else:
            self._compare_step()

    QUICK_ACTIONS = {
        "🔍 解释代码": "请解释以下代码的作用与实现原理，逐段说明，适合开发者理解：\n\n{content}",
        "📝 总结要点": "请用 3-5 条要点简洁总结以下内容：\n\n{content}",
        "🌐 翻译成英文": "请将以下内容翻译成地道的英文，保持格式：\n\n{content}",
        "🌏 翻译成中文": "请将以下内容翻译成自然流畅的中文，保持格式：\n\n{content}",
        "✨ 润色改写": "请润色以下内容，使其更流畅、专业、简洁，不改变原意：\n\n{content}",
        "🧪 生成单元测试": "请为以下代码编写完整的单元测试（unittest 风格），覆盖正常与边界情况：\n\n{content}",
        "🛠 重构代码": "请重构以下代码：保持行为不变，提升可读性、消除重复，并简要说明改动点：\n\n{content}",
        "🔐 代码审查": "请审查以下代码：按严重度指出 bug、安全问题、性能隐患与可维护性问题，并给出修复建议：\n\n{content}",
    }

    def _quick_action(self, template, content):
        """快速动作：把模板+消息内容组装后作为新消息发送。"""
        prompt = template.replace("{content}", content[:8000])
        if self.busy:
            # 不覆盖已挂起的待发消息：追加到待发队列尾部，生成结束后依次发送
            if self._pending_send:
                self._pending_send += "\n\n" + prompt
            else:
                self._pending_send = prompt
            self._flash_status("正在生成，结束后自动执行快速动作", 2500)
            return
        self._clear_placeholder()
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", prompt)
        self.input_text.focus_set()
        self.send()

    def _on_chat_menu(self, event):
        text = self.chat_text
        try:
            click = text.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        t = self._theme()
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=t["panel"],
            fg=t["text"],
            activebackground=t.get("hover", t["surface"]),
            activeforeground=t["text"],
            bd=1,
            relief="flat",
        )
        has_sel = bool(text.tag_ranges("sel"))
        if has_sel:
            menu.add_command(label="复制选中", command=self._copy_selection)
            menu.add_separator()
        msg_range = self._msg_range_at(text, click)
        msg_idx = self._msg_index_at(text, click)
        self._menu_msg_index = msg_idx
        if msg_range is not None:
            menu.add_command(
                label="复制消息",
                command=lambda r=msg_range[0]: self._copy_message_range(r),
            )
        code_range = self._code_range_at(text, click)
        if code_range is not None:
            menu.add_command(label="复制该代码块", command=lambda r=code_range: self._copy_code_range(r))
        menu.add_command(label="复制全部对话", command=self._copy_all)
        if has_sel and msg_range is not None:
            sel_start = text.index("sel.first")
            if text.compare(sel_start, ">=", msg_range[0][0]) and text.compare(
                sel_start, "<", msg_range[0][1]
            ):
                menu.add_command(
                    label="复制选中消息",
                    command=lambda r=msg_range[0]: self._copy_message_range(r, selected=True),
                )
        menu.add_separator()
        if msg_idx is not None and not self.busy:
            menu.add_command(label="编辑此消息", command=self._menu_edit_message)
            menu.add_command(label="从此重新生成", command=self._menu_regenerate_from)
            menu.add_command(label="删除此消息", command=self._menu_delete_message)
        if msg_idx is not None:
            star_label = "取消收藏" if self._is_starred(msg_idx) else "☆ 收藏此消息"
            menu.add_command(
                label=star_label,
                command=lambda i=msg_idx: self._toggle_star(i),
            )
            pin_label = "取消固定" if self._is_pinned(msg_idx) else "📌 固定此消息"
            menu.add_command(
                label=pin_label,
                command=lambda i=msg_idx: self._toggle_pin(i),
            )
            menu.add_command(
                label="从此分叉为新会话",
                command=lambda i=msg_idx: self._fork_from(i),
            )
            menu.add_command(
                label="引用此消息回复",
                command=lambda i=msg_idx: self._quote_message(i),
            )
            if (
                self.messages[msg_idx].get("role") == "assistant"
                and self.messages[msg_idx].get("content")
            ):
                menu.add_command(label="复制 Markdown 原文", command=self._copy_md_original)
                menu.add_command(label="🔊 朗读此消息", command=self._menu_speak_message)
            # 快速动作：对消息内容一键发起处理（解释/总结/翻译/润色/测试）
            content = (self.messages[msg_idx].get("content") or "").strip()
            if content and len(content) >= 20:
                qa = tk.Menu(menu, tearoff=0, bg=t["panel"], fg=t["text"],
                             activebackground=t.get("hover", t["surface"]), activeforeground=t["text"],
                             bd=1, relief="flat")
                for action_name, template in self.QUICK_ACTIONS.items():
                    qa.add_command(
                        label=action_name,
                        command=lambda tpl=template, c=content: self._quick_action(tpl, c),
                    )
                menu.add_cascade(label="⚡ 快速动作", menu=qa)
        menu.add_separator()
        menu.add_command(label="继续生成（Beta 续写）", command=self.continue_generation)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass
            # 延迟回收：立即 destroy 会让菜单一闪即逝（点击无反应）
            self.root.after(3000, lambda: _destroy_menu(menu))

    def _code_range_at(self, text, index):
        try:
            rng = text.tag_prevrange("code", f"{index}+1c")
        except tk.TclError:
            return None
        if rng and text.compare(rng[0], "<=", index) and text.compare(index, "<", rng[1]):
            return rng
        return None

    def _copy_message_range(self, rng, selected=False):
        text = self.chat_text
        if selected:
            segment = text.get("sel.first", "sel.last")
        else:
            segment = text.get(rng[0], rng[1])
        self.root.clipboard_clear()
        self.root.clipboard_append(segment.rstrip())
        self._flash_status("已复制消息")

    def _copy_code_range(self, rng):
        text = self.chat_text
        self.root.clipboard_clear()
        self.root.clipboard_append(text.get(rng[0], rng[1]).rstrip())
        self._flash_status("已复制代码块")

    def _quote_message(self, msg_idx):
        """引用指定消息插入输入框，便于结合上下文追问。"""
        if not (1 <= msg_idx < len(self.messages)):
            return
        content = (self.messages[msg_idx].get("content") or "").strip()
        if not content:
            return
        lines = content.splitlines()
        quoted = "\n".join("> " + ln for ln in lines[:8])
        if len(lines) > 8:
            quoted += "\n> …"
        self._clear_placeholder()
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", f"请结合以下内容回答：\n{quoted}\n\n")
        self.input_text.mark_set("insert", "end-1c")
        self.input_text.focus_set()
        self._flash_status("已插入引用，补充问题后按 Enter 发送")

    def _copy_md_original(self):
        """复制消息的 Markdown 原文（未渲染）。"""
        idx = self._menu_msg_index
        if idx is None or not (1 <= idx < len(self.messages)):
            return
        content = self.messages[idx].get("content") or ""
        if not content:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self._flash_status("已复制 Markdown 原文")

    def _copy_all(self):
        text = self.chat_text
        self._set_all_folds_elide(text, False)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text.get("1.0", "end"))
        finally:
            self._restore_fold_elides(text)
        self._flash_status("已复制全部对话")

    def _menu_edit_message(self):
        idx = self._menu_msg_index
        if idx is None or not (1 <= idx < len(self.messages)):
            return
        self._resend_index = idx
        self._clear_placeholder()
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", self.messages[idx].get("content", ""))
        self.input_text.focus_set()
        note = "[编辑] 已载入消息，修改后按 Enter 发送（将替换原消息及其后续）。\n"
        self._append(note, "time")
        self.blocks.append(("note", note))

    def _menu_regenerate_from(self):
        idx = self._menu_msg_index
        if idx is None or not (1 <= idx < len(self.messages)):
            return
        j = idx
        while j > 0 and self.messages[j].get("role") != "user":
            j -= 1
        if j == 0:
            return
        text = self.messages[j].get("content", "")
        self._resend_index = None  # 清残留重发索引，防 send() 二次误删
        del self.messages[j:]
        self.rebuild_view_from_messages()
        note = "[重新生成] 已移除后续消息，正在重新生成。\n"
        self._append(note, "time")
        self.blocks.append(("note", note))
        self.send(text=text)

    def _menu_delete_message(self):
        idx = self._menu_msg_index
        if idx is None or not (1 <= idx < len(self.messages)):
            return
        if self.busy:
            messagebox.showinfo("提示", "请先停止当前生成。")
            return
        role = self.messages[idx].get("role")
        if role == "assistant":
            nxt = idx + 1
            while nxt < len(self.messages) and self.messages[nxt].get("role") in ("assistant", "tool"):
                nxt += 1
            del self.messages[idx:nxt]
        else:
            del self.messages[idx]
        self.rebuild_view_from_messages()
        self._snapshot_dirty = True  # 消息集变化需落盘（空闲 10s 或退出时写）
        self._maybe_save_snapshot()
        self._flash_status("已删除消息")

    def _mark_filelinks(self, text, fold_end, detail):
        """把工具结果中的本地绝对路径标记为可点击（打开文件/目录）。"""
        try:
            # fold_end=p1 指向 body 末尾换行符，body 首字符在 p1-(len-1)，此前实现
            # 用 -len(detail) 把高亮整体左移 1 字符（落在卡片头部换行上）
            start_base = text.index(f"{fold_end}+1c-{len(detail)}c")
        except tk.TclError:
            return
        for mm in PATH_RE.finditer(detail):
            path = mm.group(0).rstrip("。.,;: \t")
            if not os.path.exists(path):
                continue
            try:
                r0 = text.index(f"{start_base}+{mm.start()}c")
                r1 = text.index(f"{start_base}+{mm.end()}c")
                text.tag_add("filelink", r0, r1)
                self._filelink_ranges.setdefault(text, []).append(
                    (_index_num(r0), _index_num(r1), path)
                )
            except tk.TclError:
                pass

    def _on_filelink_click(self, event):
        """点击本地路径：打开文件；文件不存在则打开所在目录。"""
        text = self.chat_text
        try:
            index = text.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        ranges = self._filelink_ranges.get(text, [])
        if ranges:
            # 数值键二分定位：零 Tcl round-trip（原实现每次点击线性扫描全表）
            key = _index_num(index)
            i = bisect.bisect_right([r[0] for r in ranges], key) - 1
            if i >= 0:
                r0, r1, path = ranges[i]
                if r0 <= key < r1:
                    self._open_path(path)
                    return

    def _open_path(self, path):
        # 应用内预览优先：md/txt 文本与图片在应用内查看（保留系统打开入口）
        try:
            if self._preview_path(path):
                return
        except Exception:
            pass
        self._open_system(path)

    def _open_system(self, path):
        """用系统默认程序打开（文件不存在时打开所在目录）。"""
        try:
            if os.path.exists(path):
                os.startfile(path)
                return
        except Exception:
            pass
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            self._flash_status(f"无法打开：{path}")

    _PREVIEW_TEXT_EXTS = (
        ".md", ".txt", ".log", ".ini", ".cfg", ".json",
        ".py", ".js", ".css", ".yaml", ".yml", ".csv",
    )
    _PREVIEW_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

    def _preview_path(self, path):
        """路径是否可应用内预览（按扩展名分类；失败回退系统打开）。"""
        try:
            if not path or not os.path.isfile(path):
                return False
            low = str(path).lower()
            if low.endswith(self._PREVIEW_IMAGE_EXTS):
                return self._preview_image(path)
            if low.endswith(self._PREVIEW_TEXT_EXTS):
                return self._preview_text(path)
        except Exception:
            logging.exception("应用内预览失败")
        return False

    def _preview_text(self, path):
        """文本/Markdown 应用内预览：md 走 mdparse 渲染（样式/链接可点击），其余原样。"""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(200000)
            if len(content) >= 200000:
                content += "\n…[文件较大，已截断前 200000 字符]"
        except Exception:
            return False
        t = self._theme()
        scale = self._screen_scale()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title(f"预览：{os.path.basename(path)}")
        pw, ph = int(760 * scale), int(560 * scale)
        dialog.geometry(self._center_geometry(pw, ph))
        dialog.transient(self.root)
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        head = tk.Frame(dialog, bg=t["panel"])
        head.pack(fill="x", padx=14, pady=(10, 6))
        self._restyle.append((head, "panel"))
        self._lbl(head, path, role="label_sec", bg="panel", font=(MONO_FAMILY, 8),
                  anchor="w").pack(side="left", fill="x", expand=True)
        self._mk_button(head, "用系统程序打开",
                        lambda: (dialog.destroy(), self._open_system(path)), fsz=9).pack(side="right")
        wrap = tk.Frame(dialog, bg=t["panel"])
        wrap.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        self._restyle.append((wrap, "panel"))
        text = tk.Text(
            wrap, wrap="word", bg=t["chat_bg"], fg=t["text"],
            relief="flat", highlightthickness=0, font=(FONT_FAMILY, 10),
            padx=14, pady=10, state="disabled",
        )
        sb = tk.Scrollbar(wrap, orient="vertical", command=text.yview, relief="flat", bd=0)
        text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        self._preview_tags(text, t)
        text.configure(state="normal")
        if str(path).lower().endswith(".md"):
            try:
                dtext, spans, _links, _cbs = mdparse.render_markdown(content)
                text.insert("1.0", dtext)
                for a, b, st in spans:
                    try:
                        text.tag_add(st, f"1.0+{a}c", f"1.0+{b}c")
                    except tk.TclError:
                        pass
            except Exception:
                text.insert("1.0", content)
        else:
            text.insert("1.0", content)
        text.configure(state="disabled")
        return True

    def _preview_tags(self, text, t):
        """预览窗口的样式 tag（与聊天区渲染同款视觉）。"""
        sizes = {"small": max(8, int(self.cfg.get("font_size", 10)) - 2)}
        text.tag_configure("code", font=(MONO_FAMILY, 10), background=t["code_bg"], foreground=t["code_fg"])
        text.tag_configure("code_lang", background=t["surface"], foreground=t["text_sec"],
                           font=(MONO_FAMILY, sizes["small"]))
        text.tag_configure("bold", font=(FONT_FAMILY, 10, "bold"))
        text.tag_configure("italic", font=(FONT_FAMILY, 10, "italic"))
        text.tag_configure("strike", overstrike=True, foreground=t["text_sec"])
        text.tag_configure("quote", foreground=t["text_sec"], background=t.get("quote_bg", t["surface"]))
        text.tag_configure("list", foreground=t["text"])
        text.tag_configure("table", font=(MONO_FAMILY, 9), foreground=t["text"])
        text.tag_configure("table_head", font=(FONT_FAMILY, 10, "bold"))
        text.tag_configure("link", foreground=t["accent"], underline=True)
        for i in range(1, 7):
            text.tag_configure(f"h{i}", font=(FONT_FAMILY, max(11, 15 - i), "bold"))
        text.tag_bind("link", "<Button-1>", self._on_preview_link_click)
        text.tag_bind("link", "<Enter>", lambda e: text.configure(cursor="hand2"))
        text.tag_bind("link", "<Leave>", lambda e: text.configure(cursor=""))

    def _on_preview_link_click(self, event):
        """预览窗链接点击：仅放行 http(s)。"""
        text = event.widget
        try:
            index = text.index(f"@{event.x},{event.y}")
            rng = text.tag_prevrange("link", f"{index}+1c")
            if not rng:
                return
            url = text.get(rng[0], rng[1])
        except tk.TclError:
            return
        if url.startswith(("http://", "https://")):
            webbrowser.open(url)
        else:
            self._flash_status(f"已阻止非 http(s) 链接：{url[:40]}", 2000)

    def _preview_image(self, path):
        """图片应用内预览：等比缩放适配窗口（最大 800x600）。"""
        try:
            from PIL import Image as _PILImage, ImageTk as _PILImageTk
        except ImportError:
            return False  # 无 Pillow 回退系统打开
        try:
            img = _PILImage.open(path)
            img.load()
        except Exception:
            return False
        t = self._theme()
        scale = self._screen_scale()
        dialog = tk.Toplevel(self.root, bg=t["panel"])
        dialog.title(f"预览：{os.path.basename(path)}")
        dialog.transient(self.root)
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        w, h = img.size
        max_w, max_h = int(900 * scale), int(650 * scale)
        if w > max_w or h > max_h:
            r = min(max_w / w, max_h / h)
            img = img.resize((max(1, int(w * r)), max(1, int(h * r))), _PILImage.LANCZOS)
        try:
            photo = _PILImageTk.PhotoImage(img)
        except Exception:
            dialog.destroy()
            return False
        dialog.geometry(f"{img.width + 40}x{img.height + 100}")
        label = tk.Label(dialog, image=photo, bg=t["panel"])
        label.image = photo  # 防 GC 回收
        label.pack(padx=10, pady=(10, 0))
        foot = tk.Frame(dialog, bg=t["panel"])
        foot.pack(fill="x", padx=14, pady=(6, 10))
        self._restyle.append((foot, "panel"))
        self._lbl(foot, f"{os.path.basename(path)} · {w}x{h}",
                  role="label_sec", bg="panel", font=(FONT_FAMILY, 9)).pack(side="left")
        self._mk_button(foot, "用系统程序打开",
                        lambda: (dialog.destroy(), self._open_system(path)), fsz=9).pack(side="right")
        return True

    def _on_link_click(self, event):
        text = self.chat_text
        try:
            index = text.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        ranges = self._link_ranges.get(text, [])
        if ranges:
            # 数值键二分定位：零 Tcl round-trip
            key = _index_num(index)
            i = bisect.bisect_right([r[0] for r in ranges], key) - 1
            if i >= 0:
                r0, r1, url = ranges[i]
                if r0 <= key < r1:
                    # 仅放行 http/https：模型输出的链接可控，file:// 等协议会绕过浏览器
                    # 安全边界（读取本地文件 / 触发本机程序）
                    if url.startswith(("http://", "https://")):
                        webbrowser.open(url)
                    else:
                        self._flash_status(f"已阻止非 http(s) 链接：{url[:40]}", 2000)
                    return

    def _paste_as_link(self, event=None):
        """Ctrl+Shift+V：剪贴板为 URL 时粘贴为 Markdown 链接，否则放行普通粘贴。"""
        try:
            clip = self.root.clipboard_get().strip()
        except tk.TclError:
            return "break"
        if clip.startswith(("http://", "https://")):
            self._clear_placeholder()
            sel = self.input_text.tag_ranges("sel")
            if sel:
                label = self.input_text.get("sel.first", "sel.last") or "链接"
                self.input_text.delete("sel.first", "sel.last")
                self.input_text.insert("sel.first", f"[{label}]({clip})")
            else:
                self.input_text.insert("insert", f"[链接]({clip})")
            self.input_text.focus_set()
            self._flash_status("已粘贴为 Markdown 链接")
            return "break"
        self.input_text.event_generate("<<Paste>>")
        return "break"

    def _input_history_nav(self, delta):
        """Alt+↑/↓ 浏览当前会话已发送的输入历史（首次按↑自动保存当前草稿）。"""
        hist = self._current.setdefault("sent_history", [])
        if not hist:
            return "break"
        if getattr(self, "_hist_index", None) is None:
            self._hist_draft = self.input_text.get("1.0", "end-1c")
            # 首次按键（无论 ↑/↓）都从最近一条开始：↑ 走更旧，↓ 走更新
            self._hist_index = len(hist) - 1
        else:
            new_idx = self._hist_index + delta
            if new_idx < 0:
                self._hist_index = None
                self._clear_placeholder()
                self.input_text.delete("1.0", "end")
                self.input_text.insert("1.0", self._hist_draft or "")
                self.input_text.mark_set("insert", "end-1c")
                return "break"
            self._hist_index = min(new_idx, len(hist) - 1)
        self._clear_placeholder()
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", hist[self._hist_index])
        self.input_text.mark_set("insert", "end-1c")
        return "break"

    def _wrap_selection(self, before, after, placeholder):
        self._clear_placeholder()
        sel_range = self.input_text.tag_ranges("sel")
        if sel_range:
            sel = self.input_text.get("sel.first", "sel.last")
            start = str(sel_range[0])
            self.input_text.delete(start, sel_range[1])
            self.input_text.insert(start, f"{before}{sel}{after}")
        else:
            self.input_text.insert("insert", f"{before}{placeholder}{after}")
        self.input_text.focus_set()
        return "break"

    def _insert_link(self, _event=None):
        self._clear_placeholder()
        url = ""
        try:
            clip = self.root.clipboard_get()
            if clip.strip().startswith(("http://", "https://")):
                url = clip.strip()
        except tk.TclError:
            pass
        if not url:
            url = "https://"
        sel_range = self.input_text.tag_ranges("sel")
        if sel_range:
            sel = self.input_text.get("sel.first", "sel.last")
            start = str(sel_range[0])
            self.input_text.delete(start, sel_range[1])
            self.input_text.insert(start, f"[{sel}]({url})")
        else:
            self.input_text.insert("insert", f"[文本]({url})")
        self.input_text.focus_set()
        return "break"

    # ---- 编辑器增强实现 ----
    _INPUT_DELIM_PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}

    def _on_input_open_delimiter(self, event):
        """括号/引号自动配对：插入开字符 + 闭字符，光标落在中间。
        输入开引号时若右侧紧跟可配对字符则视为闭合（原样输入）；
        有选区时用配对包裹选区（与 Ctrl+B 同款语义）。"""
        self._clear_placeholder()
        ch = event.char
        close = self._INPUT_DELIM_PAIRS.get(ch)
        if close is None:
            return
        text = self.input_text
        sel_range = text.tag_ranges("sel")
        if sel_range:
            sel = text.get("sel.first", "sel.last")
            start = str(sel_range[0])
            text.delete(start, sel_range[1])
            text.insert(start, f"{ch}{sel}{close}")
            text.focus_set()
            return "break"
        idx = text.index("insert")
        after = text.get(idx, f"{idx} +1c")
        if after == close:
            # 后一字符已是配对字符（双引号场景输入闭合引号）：不自动配对
            return None
        text.insert("insert", ch)
        text.insert("insert", close)
        text.mark_set("insert", f"{idx}+1c")
        text.focus_set()
        return "break"

    def _on_input_tab(self, _event=None):
        """Tab：无选区时插入 4 空格（对话场景不用制表符）；有选区时整行缩进。"""
        self._clear_placeholder()
        text = self.input_text
        sel_range = text.tag_ranges("sel")
        if not sel_range:
            text.insert("insert", "    ")
            return "break"
        start, end = str(sel_range[0]), str(sel_range[1])
        sline = int(start.split(".")[0])
        eline = int(end.split(".")[0])
        # 只对选区涉及的整行行首加 4 空格
        for ln in range(sline, eline + 1):
            text.insert(f"{ln}.0", "    ")
        text.focus_set()
        return "break"

    def _on_input_shift_tab(self, _event=None):
        """Shift+Tab：删除选区行的行首 4 空格（不足则删除现有缩进）。"""
        self._clear_placeholder()
        text = self.input_text
        sel_range = text.tag_ranges("sel")
        if not sel_range:
            return "break"
        start, end = str(sel_range[0]), str(sel_range[1])
        sline = int(start.split(".")[0])
        eline = int(end.split(".")[0])
        for ln in range(sline, eline + 1):
            cur = text.get(f"{ln}.0", f"{ln}.4")
            indent = len(cur) - len(cur.lstrip(" "))
            if indent:
                text.delete(f"{ln}.0", f"{ln}.{min(4, indent)}")
        text.focus_set()
        return "break"

    def _on_input_backspace(self, _event=None):
        """Backspace：若光标前是开括号且紧跟配对闭括号，一次删除整对（配对退格）。"""
        text = self.input_text
        sel_range = text.tag_ranges("sel")
        if sel_range:
            return None  # 有选区时走默认删除
        idx = text.index("insert")
        if idx == "1.0":
            return None
        prev = text.get(f"{idx}-1c", idx)
        close = self._INPUT_DELIM_PAIRS.get(prev)
        if close is None:
            return None
        nxt = text.get(idx, f"{idx} +1c")
        if nxt == close:
            text.delete(f"{idx}-1c", f"{idx} +1c")
            return "break"
        return None

    def _on_input_delete_word(self, _event=None):
        """Ctrl+Backspace：删除光标前的一个单词（连片空白也一并吞掉）。"""
        self._clear_placeholder()
        text = self.input_text
        idx = text.index("insert")
        if idx == "1.0":
            return "break"
        # 跳过光标前空白，再跳过单词字符（中文按字处理）
        cur = text.get("1.0", idx)
        if not cur:
            return "break"
        pos = len(cur) - 1
        while pos >= 0 and cur[pos] in " \t":
            pos -= 1
        while pos >= 0 and cur[pos] not in " \t":
            pos -= 1
        text.delete(f"{pos + 1}.0", idx)
        text.focus_set()
        return "break"

    def _on_drop(self, event):
        data = getattr(event, "data", "") or ""
        # tkinterdnd2 的 data 形如 "{C:\a.txt} {C:\b.txt}"：
        # 用正则提取花括号内容，split("} {") 会吞掉边界导致文件丢失
        paths = re.findall(r"\{(.*?)\}", data)
        if not paths:
            paths = [p.strip() for p in data.split() if os.path.isfile(p.strip())]
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return
        self._clear_placeholder()
        parts = []
        existing = self.input_text.get("1.0", "end-1c").strip()
        if existing:
            parts.append(existing)
        for path in paths[:3]:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(8000)
                if len(content) >= 8000:
                    content += "\n[文件较大，已截断前 8000 字符]"
                parts.append(f"[文件] {os.path.basename(path)}:\n{content}")
            except Exception as e:
                parts.append(f"[文件] {path}: 读取失败: {e}")
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", "\n\n".join(parts))
        self.input_text.focus_set()
        self._flash_status(f"已附加 {len(paths)} 个文件")
        return "break"

    def _menu_speak_message(self):
        """朗读右键指向的助手消息（复用朗读线程防叠加逻辑）。"""
        idx = getattr(self, "_menu_msg_index", None)
        if idx is None:
            return
        try:
            content = self.messages[idx].get("content") or ""
        except (IndexError, TypeError):
            return
        if not content:
            messagebox.showinfo("提示", "该消息没有可朗读的内容。")
            return
        self._speak_text(content)

    def speak_last_reply(self):
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                self._speak_text(msg["content"])
                return
        messagebox.showinfo("提示", "暂无助手回复可朗读。")

    def _speak_text(self, text):
        # 防叠加：连续点击朗读只启动一个线程，避免多个 SAPI 声音重叠
        if getattr(self, "_speak_thread", None) and self._speak_thread.is_alive():
            self._flash_status("正在朗读中…")
            return
        try:
            import win32com.client
        except ImportError:
            messagebox.showinfo(
                "提示", "朗读回复需要 pywin32，请运行：pip install pywin32"
            )
            return
        try:
            plain = mdparse.to_plain(text)
        except Exception:
            plain = text

        def _do_speak():
            try:
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                speaker.Speak(plain)
            except Exception:
                logging.exception("朗读失败")

        self._speak_thread = threading.Thread(target=_do_speak, daemon=True)
        self._speak_thread.start()
        self._flash_status("正在朗读…")

    def _show_input_menu(self, event):
        t = self._theme()
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=t["panel"],
            fg=t["text"],
            activebackground=t.get("hover", t["surface"]),
            activeforeground=t["text"],
            bd=1,
            relief="flat",
        )
        menu.add_command(
            label="剪切", command=lambda: self.input_text.event_generate("<<Cut>>")
        )
        menu.add_command(
            label="复制", command=lambda: self.input_text.event_generate("<<Copy>>")
        )
        menu.add_command(
            label="粘贴", command=lambda: self.input_text.event_generate("<<Paste>>")
        )
        menu.add_command(
            label="撤销", command=lambda: self.input_text.event_generate("<<Undo>>")
        )
        menu.add_command(
            label="清空输入", command=lambda: self.input_text.delete("1.0", "end")
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass
            # 延迟回收：立即 destroy 会让菜单一闪即逝（点击无反应）
            self.root.after(3000, lambda: _destroy_menu(menu))

    def build_markdown(self):
        cfg = self.save_widgets_to_config()
        return _build_markdown(
            self.messages, self.usage_total, self.session_start, cfg
        )

    def export_history(self, ask_dir=True):
        if len(self.messages) <= 1:
            return
        if ask_dir:
            target_dir = filedialog.askdirectory(initialdir=HISTORY_DIR, title="选择导出目录")
            if not target_dir:
                return
        else:
            target_dir = HISTORY_DIR
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(target_dir, f"session_{timestamp}")
        # 主线程快照：导出在后台线程执行（长会话 md 构建 + 4 文件写盘可冻结 UI 数秒），
        # 期间 worker 线程可能继续往 self.messages 追加，快照保证导出内容一致
        msgs = list(self.messages)
        usage = dict(self.usage_total)
        start = self.session_start
        cfg_snap = self.cfg.copy()
        md_path = base + ".md"

        def _do_export():
            try:
                content = _build_markdown(msgs, usage, start, cfg_snap)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(content)
                plain = content.replace("```text\n", "【思考】\n").replace("```\n", "")
                with open(base + ".txt", "w", encoding="utf-8") as f:
                    f.write(plain)
                # 增量扩展：HTML / JSONL（失败不影响 md/txt）
                try:
                    exporters.export_html(
                        msgs,
                        base + ".html",
                        title=f"鲸语会话记录 {start:%Y-%m-%d %H:%M}",
                        model=str(cfg_snap.get("model", "")),
                        scenario=str(cfg_snap.get("scenario", "")),
                    )
                    exporters.export_jsonl(msgs, base + ".jsonl")
                except Exception:
                    logging.exception("HTML/JSONL 导出失败")
                if ask_dir:
                    self._ui_queue.put(
                        ("export_done", (True, f"{md_path}\n{base}.txt\n{base}.html\n{base}.jsonl"))
                    )
            except Exception:
                logging.exception("MD/TXT 导出失败")
                if ask_dir:
                    self._ui_queue.put(("export_done", (False, "写入文件失败，请检查目录权限。")))

        threading.Thread(target=_do_export, daemon=True).start()
        if ask_dir:
            self._flash_status("正在后台导出会话…")
        return md_path

    def _show_export_done(self, payload):
        ok, detail = payload
        if ok:
            dialog, body, footer = self._dialog_shell(
                "导出成功", 520, 300, subtitle="会话已导出（MD / TXT / HTML / JSONL）"
            )
            self._lbl(body, "✅ 导出完成", role="label_accent", bg="panel",
                      font=(FONT_FAMILY, 12, "bold")).pack(anchor="w", pady=(0, 8))
            for ln in str(detail).splitlines():
                self._lbl(body, ln, bg="panel", font=(FONT_FAMILY, 9), wraplength=460,
                          justify="left").pack(anchor="w", pady=1)
            self._footer_btn(footer, "关闭", dialog.destroy)
        else:
            messagebox.showerror("导出失败", detail)

    def _cache_cost_hint(self):
        """缓存成本提示（按当前模型高峰价动态生成，配合 V4 正式版峰谷定价）。"""
        try:
            model = self.cfg.get("model") or "deepseek-v4-flash"
            p = stats.pricing().get(model, stats.DEFAULT_PRICE)
            hit, miss = p.get("cache_hit", 0.1), p.get("prompt", 3.0)
            return f"缓存命中 {hit:.2f} 元 vs 未命中 {miss:.2f} 元/百万 tokens（高峰价）"
        except Exception:
            return "缓存命中 0.10 元 vs 未命中 3.00 元/百万 tokens（高峰价）"

    def on_enter(self, event):
        if event.state & 0x0001:
            self.input_text.insert("insert", "\n")
            return "break"
        self.send()
        return "break"

    def on_close(self):
        # 关闭时最小化到托盘（托盘退出走 _quit_from_tray 绕过拦截；
        # 托盘线程异常退出后不再拦截，避免窗口被隐藏后无法恢复）
        if (
            not getattr(self, "_quit_from_tray", False)
            and self.cfg.get("minimize_to_tray")
            and self._tray_alive()
        ):
            try:
                self.root.withdraw()
                self._flash_status("已最小化到系统托盘（右键托盘图标可退出）", 2500)
                return
            except tk.TclError:
                pass  # 托盘拦截失败则继续正常退出流程
        if self.busy:
            if not messagebox.askyesno(
                "正在生成", "当前正在生成回复，确定退出吗？（未完成的回复不会保存）"
            ):
                return
            if self.stop_event:
                self.stop_event.set()
        try:
            if self._poller_id is not None:
                try:
                    self.root.after_cancel(self._poller_id)
                except Exception:
                    pass
        except Exception:
            pass
        self._cancel_paged_render()
        if getattr(self, "_snapshot_after", None) is not None:
            try:
                self.root.after_cancel(self._snapshot_after)
            except Exception:
                pass
            self._snapshot_after = None
        if getattr(self, "_search_after_id", None) is not None:
            try:
                self.root.after_cancel(self._search_after_id)
            except Exception:
                pass
        if getattr(self, "task_panel", None) is not None:
            try:
                self.task_panel.destroy()
            except Exception:
                pass
        if getattr(self, "proc_panel", None) is not None:
            try:
                self.proc_panel.close()
            except Exception:
                pass
        try:
            from deepseek_client import stop_all_processes

            stop_all_processes()
        except Exception:
            pass
        if getattr(self, "_inbound_server", None) is not None:
            try:
                self._inbound_server.shutdown()
            except Exception:
                pass
        try:
            from deepseek_client import close_browser

            close_browser()
        except Exception:
            pass
        try:
            self.save_widgets_to_config()
        except Exception:
            pass
        self._flush_stats()
        self._save_draft()
        # 退出前把所有非临时会话完整落盘（此前只写 current 快照，
        # 其他会话切换走后就再没写过盘，退出即丢失）
        try:
            for s in self._sessions:
                if not s.get("ephemeral") and not self.cfg.get("privacy_mode"):
                    self.save_session_to_file(s)
        except Exception:
            logging.exception("退出前保存会话失败")
        self.save_snapshot()
        # 记忆窗口几何（屏幕内校验），下次启动恢复；最大化状态下不覆盖记忆
        # （zoomed 时 geometry() 无意义，保留用户上次手动调整的普通几何）
        if not self.cfg.get("privacy_mode"):
            try:
                if self.root.state() != "zoomed":
                    self.cfg["window_geometry"] = self.root.geometry()
                save_config(self.cfg)
            except Exception:
                pass
        if not self.cfg.get("privacy_mode"):
            write_clean_exit_flag()
        self._stop_tray()
        self.root.destroy()


def ensure_app_icon():
    """生成应用图标 app.ico（不存在时），返回路径或 None。"""
    path = os.path.join(BASE_DIR, "app.ico")
    if os.path.exists(path):
        return path
    try:
        from PIL import Image, ImageDraw

        def make(size):
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle(
                [0, 0, size - 1, size - 1], radius=max(2, size // 5),
                fill=(52, 120, 246, 255),
            )
            cx = cy = size // 2
            dot_r = max(1, size * 0.16)
            ring_r = max(2, size * 0.34)
            d.ellipse(
                [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
                fill=(255, 255, 255, 255),
            )
            d.ellipse(
                [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
                outline=(255, 255, 255, 255),
                width=max(1, size // 16),
            )
            return img

        sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        images = [make(s) for s, _ in sizes]
        images[-1].save(path, format="ICO", sizes=sizes)
        return path
    except Exception:
        logging.warning("生成应用图标失败", exc_info=True)
        return None


def single_instance_lock():
    """Windows 命名互斥体，防止多实例。返回 False 表示已有实例在运行。"""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME_EN}Assistant")
        return kernel32.GetLastError() != 183
    except Exception:
        return True


def _enable_dpi_awareness():
    """启用 Windows 高 DPI 感知（Per-Monitor v2 → System 兜底），避免文字发虚。"""
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception:
        pass


def _apply_dpi_scaling(root):
    """按系统 DPI 校正 tk 字体缩放（tk 默认按 96dpi，高 DPI 屏需等比放大）。"""
    try:
        import ctypes

        hdc = ctypes.windll.user32.GetDC(0)
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi and dpi > 0:
            root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass


def main():
    _t_start = time.perf_counter()
    _enable_dpi_awareness()
    if not single_instance_lock():
        messagebox.showwarning(
            "已在运行", "DeepSeek V4 Flash 对话助手已在运行，请勿重复启动。"
        )
        return
    icon_path = ensure_app_icon()
    if DND_AVAILABLE:
        root = tkinterdnd2.TkinterDnD.Tk()
    else:
        root = tk.Tk()
    _apply_dpi_scaling(root)
    root.withdraw()
    splash = None
    try:
        splash = SplashScreen(root, version=VERSION)
        splash.show()
    except Exception:
        logging.warning("启动界面初始化失败", exc_info=True)
    if icon_path:
        try:
            root.iconbitmap(icon_path)
        except Exception:
            pass
    app = AssistantApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    if splash is not None:
        root.after(600, splash.fade_out)
    root.deiconify()
    root.mainloop()
    logging.info("鲸语退出，本次运行时长 %.1fs", time.perf_counter() - _t_start)


if __name__ == "__main__":
    main()
