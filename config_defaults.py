# -*- coding: utf-8 -*-
"""默认配置与系统提示词常量。

从 main.py 中拆出，供配置加载/角色/场景等模块复用。
"""

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
    "download_file",
    "archive_list",
    "epub_read",
    "mobi_read",
    "doc_read",
    "msg_read",
    "im_send",
    "telegram_poll_updates",
    "email_summary",
    "agent_mail",
    "secret_store",
    "rpa_screen_size",
    "rpa_click",
    "rpa_type",
    "rpa_hotkey",
    "rpa_move",
    "rpa_scroll",
    "rpa_screenshot",
    # 公众号写作能力（安全：只产草稿不发布，发布权在用户；permissions 白名单已含 publish_draft）
    "run_wechat_writer",
    "publish_draft",
]

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

# 更新源：默认指向 GitHub Releases
UPDATE_URL = "https://api.github.com/repos/pythonshiyi/WhaleTalk/releases/latest"

# 在线插件市场索引（index.json）；用户可在配置 plugin_market_url 覆盖
PLUGIN_MARKET_URL = "https://raw.githubusercontent.com/pythonshiyi/WhaleTalk/main/plugin_market/index.json"

# 可自定义的根窗口快捷键（动作名 -> Tk 键序列）。留空值表示使用默认。
DEFAULT_SHORTCUTS = {
    "new_conversation": "<Control-n>",
    "toggle_search": "<Control-f>",
    "export_history": "<Control-e>",
    "export_session_json": "<Control-Shift-s>",
    "open_global_search": "<Control-Shift-f>",
    "paste_clipboard_ask": "<Control-Shift-q>",
    "close_tab": "<Control-w>",
    "show_help": "<F1>",
    "regenerate": "<F5>",
    "show_command_palette": "<Control-k>",
    "toggle_fullscreen": "<F11>",
    "show_tool_hub": "<Control-Shift-t>",
    "show_plugin_hub": "<Control-Shift-p>",
}

MAX_CONTEXT_TOKENS = 1_000_000

SCENARIO_DEFAULT_THINKING = {"通用": "high", "编程": "max", "Agent": "max"}

DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "scenario": "通用",
    "thinking": "none",  # 初始最干净：思考关闭（用户可在设置中开启）
    "max_tokens": 16384,
    "seed": "",
    "tools_enabled": True,
    "enabled_tools": list(BUILTIN_TOOL_NAMES),
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "max_context_chars": 500000,
    "max_context_tokens": 400000,
    "min_kept_turns": 8,
    "timeout": 120,
    "restore_session": False,
    "font_size": 10,
    "font_size_custom": False,  # 用户手动改过字号后为 True，启动时不再自动按屏幕调整
    "theme": "light",
    "md_render": True,
    "custom_temperature": 1.0,
    "custom_top_p": 1.0,
    "privacy_mode": False,
    "check_update": True,  # 初始自动检测 GitHub Releases 更新
    "welcomed": False,
    "max_tool_rounds": 100,  # 单条消息工具轮数上限：能力最大化，默认 100
    "monthly_budget": 0.0,
    "block_on_budget": False,
    "input_height": 4,
    "input_height_px": 0,
    "sidebar_width": 260,
    "panel_width": 280,
    "browser_headless": False,  # 初始浏览器可见（有头模式）
    "json_output": False,
    "beta_api": False,
    "peak_warning": True,
    "fold_early_threshold": 0,
    "current_profile": "",
    "notify_on_done": True,  # 初始完成通知开启
    "ssrf_trusted": [],
    "project_context": False,
    "full_auto": True,  # 初始即完全智能：全部工具、零审批、零开关（黑名单仍生效）
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
    "minimize_to_tray": True,   # 初始关闭时最小化到系统托盘（需 pystray；不可用时回滚）
    "autostart": True,          # 初始开机自启（注册表 Run 键；失败自动回滚）
    "strict_tools": False,    # strict 工具模式（Beta）：模型严格遵循工具 JSON Schema
    "update_url": "",         # 更新检查源（latest.json，如 https://example.com/latest.json）
    "call_api_allowed_hosts": [],  # call_api 内网/回环白名单（精确主机名，建议 IP；如 ["127.0.0.1"]）
    "plugin_market_url": "",  # 在线插件市场索引（index.json；留空则使用默认 GitHub 源）
    "custom_themes": {},      # 自定义主题：名称 -> 主题 token 字典（合并到内置主题）
    "shortcuts": {},          # 快捷键自定义：动作名 -> Tk 键序列（留空使用内置默认）
    "update_public_key": "",  # 更新包签名公钥（可选；配置后校验 Ed25519 签名/或 sha256 字段）
    "agent_mail_enabled": False,  # Agent Mail（agently-cli）集成开关；默认关闭，不配置不影响使用
    "agent_mail_cli": "agently-cli",  # agently-cli 可执行文件（或绝对路径）
}
