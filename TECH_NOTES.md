# 鲸语 WhaleTalk 技术文档（面向 AI 智能体）

本文档面向后续维护/开发的 AI 智能体，描述项目的架构、数据流、核心约定与历史踩坑记录。行号会随代码演化漂移，定位时以**符号名**为准，本文档不承诺行号。

## 0. 品牌与版本

- 品牌：鲸语（APP_NAME），英文 WhaleTalk（APP_NAME_EN），见 main.py 顶部。**任何对外展示（窗口标题/启动界面/欢迎页/关于/exe 名/备份名/单实例锁名）一律使用品牌名，不出现官方品牌**；技术描述可写"基于 DeepSeek API"。
- 版本：VERSION（当前 2.12.10），版本升级时同步 bump；备份脚本产物名 `WhaleTalk_v{version}_*.zip`；`build_exe.bat` 产物 `WhaleTalk.exe`；单实例锁 `Local\WhaleTalkAssistant`。

## 1. 项目概览

Windows 桌面聊天助手（Tkinter），品牌名「鲸语 WhaleTalk」（独立产品，与 DeepSeek 官方无关联），对接 DeepSeek V4 API，核心卖点：流式输出（思考过程+回答）、Agent 工具调用多轮循环、百万级 token 上下文自动压缩、多标签会话、Markdown 渲染、DeepSeek 特性深度适配（JSON 输出 / 前缀续写 / FIM / 峰谷定价 / 缓存命中优化）。

- 语言/运行时：Python 3.9+（开发环境 3.12），仅标准库 + `openai` + `tiktoken`
- GUI：`tkinter`（ttk 混用），无第三方 UI 框架
- 入口：`main.py`，`AssistantApp` 类（约 13500 行）承载全部 UI 逻辑

## 2. 目录与模块职责

```
WhaleTalk/（项目目录名不依赖，可自由改名）
├── main.py              # GUI 入口 + AssistantApp（UI、会话、流式渲染、上下文管理）
├── splash.py            # 启动界面（深海渐变 + 蓝鲸徽标 + 加载动画 + 淡出）
├── deepseek_client.py   # DeepSeekClient（流式/思考/JSON/续写/工具循环/重试）、工具实现、check_balance、is_peak_hour
├── exporters.py         # 会话导出扩展（HTML / JSONL，纯函数无状态）
├── permissions.py       # 权限模型（resolve/白名单/审批/审计，默认全关）
├── mdparse.py           # Markdown 块解析 + 内联解析 + 渲染（纯函数，无 Tk 依赖）
├── tokens.py            # tiktoken 估算（含回退 1.5 字符/token）
├── prompts.py           # 提示词库（默认模板 + 读写）
├── stats.py             # 用量统计（按天/模型累计 + 费用估算，官方定价）
├── backup.py            # 版本备份脚本（产物 WhaleTalk_vX.Y.Z_时间戳.zip）
├── config.json          # 用户配置（含 API Key，切勿提交）
└── start.bat / backup.bat / build_exe.bat
```

运行数据目录：`%USERPROFILE%\Documents\WhaleTalk\`（日志 `logs/assistant.log`，历史 `history/`，快照 `history/session_latest.json`，统计 `stats.json`，归档 `archives/`，自定义工具 `user_tools.json`，提示词库 `prompts.json`）。**模块加载时 `migrate_legacy_data()` 把旧目录 `Documents\DeepSeek_Assistant` 整体迁移到 WhaleTalk**：仅当旧目录存在时执行；新目录若只是空壳（`_is_empty_shell`：无任何文件）先移除再迁移；新目录含真实数据或删除失败则不迁移（不阻塞启动，失败原因 print 提示）。

## 3. 运行

```bash
python main.py                          # 运行 GUI
```

`main()` 启动流程：单实例锁 → 创建 root 并 `withdraw()` → 初始化 `SplashScreen`（无边框置顶 Toplevel）→ 构建 `AssistantApp` → `deiconify()` → `after(600, splash.fade_out)`（10 步透明度递减淡出）。**splash 构建失败不阻塞启动**（try/except 降级）。

## 4. 线程模型与 UI 队列协议

单 UI 线程 + 工作线程。**后台线程绝不直接碰 Tk 控件**：

- `send()`（主线程）：校验 → `_append_message_block` → `_set_busy(True)` → 创建 `threading.Event` 作为 `stop_event` → 启动 `_worker` 线程。
- `_worker(continue_mode=False)`（后台线程）：`ensure_client()` → 若需压缩则 `_compress_old_history()` → `client.chat(...)` 流式回调 → finally `put(("finish", None))`。
- `continue_generation()`：校验 Beta API 与末条 assistant 消息后，以 `continue_mode=True` 启动 `_worker`（不追加新 user 消息）。
- 回调 `_push_reasoning` / `_push_content` / `_push_tool` / `_push_usage`：只做 `self._ui_queue.put((kind, payload))`。
- 主线程轮询：`_poll_ui` 通过 `root.after` 自调度（busy 时 40ms，空闲 500ms）→ `_drain_ui_queue` → `_flush_pending` 批量渲染。

队列消息类型：`begin`（payload=resume 布尔）/ `reasoning` / `content` / `tool` / `tool_dur` / `usage` / `error` / `balance` / `balance_done` / `info` / `update` / `search_all_done` / `history_loaded` / `loop_guard` / `finish`。

**中断机制**：生成中再发消息 → `stop_event.set()` + 记录 `_pending_send`；`client._consume_stream` 每块检查 `stop_event`；`_finish` 结束后若 `_pending_send` 非空则自动 `send(text=...)` 继续。**发送按钮始终可用**（busy 时点击 = 打断并发送）。

## 5. 会话模型

- `self._sessions`：会话 dict 列表（多标签）；`self._current` 指向当前会话 dict。
- 会话 dict 键：`tab`/`text`（Tk Text）/`messages`/`usage_total`/`last_usage`/`assistant_answered`/`session_start`/`blocks`（CappedList，上限 8000）/`first_user`（首条用户消息缓存，列表搜索用）/`last_code_blocks`（**会话级**代码复制缓冲，切会话不串）/`top`（会话置顶标志）/`variants`（回复变体列表，懒创建）/`tags`/`stars`/`pinned`/`ephemeral`/`id`/`name`。
- `AssistantApp` 用属性代理把当前会话字段暴露为 `self.chat_text`、`self.messages`、`self.blocks`、`self.usage_total` 等——**对属性赋值会写入当前会话**。
- `_stream_start` / `_stream_block_start` / `_ctx_counts` 等是**应用级属性**（与当前会话绑定，切标签时通过属性代理访问会话级字段）。

## 6. blocks 协议（核心约束）

`blocks` 是渲染的"文档模型"，每个元素是元组：

| kind | 格式 | 渲染方式 |
|------|------|----------|
| `note` | `("note", text)` 或 `("note", text, tag)` | 用 `time`/指定 tag 插入 |
| `plain` | `("plain", text)` | 无 tag 原样插入 |
| `user` | `("user", text)` | `user` tag（右对齐） |
| `thinking` | `("thinking", text)` | 折叠卡片（默认展开） |
| `content` | `("content", payload, msg_idx)` | 经 mdparse 渲染（md_render 开启时） |
| `tool` | `("tool", (name, args, result, duration))` | 折叠卡片（默认收起） |
| `toolresult` | `("toolresult", text)` | `[工具结果] text`，`tool` tag |
| `error` | `("error", text)` | `error` tag |

**铁律：blocks 与文本插入必须完全同步**——任何直接修改 `self.messages` 后必须调 `rebuild_view_from_messages()`（重建 blocks + 全量重渲染），否则后续 `_re_render_stream` 错位。

**段落分隔约定**：块内先剥尾 `\n`，块间用 `("plain", "\n")` 补空行。

**流式 content 块合并**（历史修复）：`_flush_pending` 只拿到增量，若直接 append 新 content 块，`_finish` 重渲染时每块单独跑 md 解析导致伪换行/`**` 跨块/围栏拆断。必须合并相邻 content 块（`blocks[-1][0] == "content"` 时拼接 payload）。

## 7. 流式渲染链路

```
_begin_assistant(resume) →  _flush_pending → _append(...)     # 流式期间原样插入
_finish                →  _re_render_stream(start, block_start)  # 结束后重渲染
                        └→ _render_all()     # 有 thinking/tool 折叠时全量重建
```

- `_begin_assistant(resume=False)`：非 resume 插入 `[HH:MM] 助手\n`（note 块）；**resume 模式**（继续生成）不插头，`_stream_block_start = len(blocks) - 1`（定位到最后一个 content 块，续写直接并入，`_finish` 重渲染覆盖整个块为合并后完整 payload）。记录 `_stream_begin`（思考动画计时）与 `_thinking_received=False`。
- **思考动画**：`_poll_ui` 中 busy 且 `_stream_start` 非空、未收到 reasoning、`_stream_begin` 后 0.5s → 状态栏「🤔 思考中…」。
- **智能跟随滚动**：`_append` 仅在 `_is_at_bottom()`（`yview()[1] >= 0.99`）时 `see("end")`；`_re_render_stream`/`_render_all` 结束后强制 `see("end")`。
- **统一跟随入口 `_ensure_follow()`**：所有内容插入路径（`_append` 文本、`_flush_pending` 思考增量、`_append_tool` 工具卡片、`_paged_step` 分帧完成、`_re_render_stream`）统一调用——**新增插入路径必须调用 `_ensure_follow()`，否则 AI 输出期间界面停在旧位置**。
- **跟随意图显式状态跟踪 `_follow_bottom`**（根治"输出不自动滚动"）：不用插入后 `yview()[1]` 即时值判底——Tk 布局滞后会让 yview 返回旧值导致误判。规则：仅**手动滚动**置 False（滚轮向上 `_on_chat_wheel`、滚动条 `<Button-1>`/`<B1-Motion>`、折叠点击、搜索/消息定位），滚动回底部（yview ≥ 0.995）自动恢复 True；**发送消息/新一轮生成强制置 True**（`_append_message_block` 与 `_begin_assistant` 开头——发送=新交互，无条件贴底，这是用户明确要求的核心体验）；切换会话/全量重建置 True。`_ensure_follow` 只在 `_follow_bottom` 为 True 时 `see("end")`。
- `_insert_content`（md 模式）：`text.insert(pos, dtext, tag)` 后逐 span `tag_add`；**`self._link_ranges[text]` 必须 `setdefault().extend()` 而非覆盖**（历史 bug：覆盖导致只有最后一条消息的链接可点击）；code 块内容追加进当前会话 `last_code_blocks`。
- `_re_render_stream` 抛 `tk.TclError` 时兜底 `delete("1.0","end") + _render_all()`。

## 8. 折叠系统（共享 tag 方案）

- 折叠卡片正文**共用单一 tag `fold_hidden`**（`elide=True`），而非每块新建 tag（历史优化：旧实现每块一个 tag，长对话累积数千 tag 拖慢 Tk）。
- `_insert_fold`：正文以 `style` tag 插入；折叠态对正文范围 `tag_add("fold_hidden", t1, p1)`。`_fold_ranges[text]` 记录 `t0/t1/p1/visible/head/ttag`。
- `_toggle_fold`：`tag_remove/tag_add fold_hidden` + 替换箭头头（头文本长度恒定，索引不漂移）。
- 搜索前 `_set_all_folds_elide(text, False)` 全展开，搜索后 `_restore_fold_elides` 按 `visible` 恢复。
- 新增折叠样式必须在 `main._configure_tags` 注册（含 `fold_hidden`）。

## 9. Tk Text 排版规范

聊天区每个 tab：`ttk.Frame(tab)` 内放 `tk.Frame(col, bg=chat_bg)`（内容列）+ Text + 滚动条。**内容列用 `place` 定位居中**（网页风窄列，`max(560, min(900, tw - 96))`）。

**Tk place 陷阱**：`place` 是增量配置，rel\* 与 width 混用时 rel\* 优先——每次回调必须全量传绝对 width/height。

`_configure_tags(text, t, sizes)` 为每个会话的 Text 全量配置所有 tag（user 右对齐、正文统一 `lmargin1=lmargin2=8`、code/quote/table/headings 等）。**任何依赖 tag 配置的动态逻辑都要在 `_configure_tags` 内处理**，不要在外部用 `<Configure>` 回调。

**Tk Text 索引铁律**：`"end-1c"` 才是文本真实末尾；`text.search` 未命中返回 `''`；`tag_ranges()` 返回扁平索引序列（成对取）。

## 10. 主题系统

`THEMES = {"light": {...}, "dark": {...}}`。`apply_theme` 遍历 `self._restyle` 更新背景 + 重建 ttk 样式 + 每个会话 `_configure_tags`。**新增会变色的控件必须注册进 `self._restyle`**；`close_tab` 时必须从 `_restyle` 移除已销毁控件（`[w for w in ... if w is not session["col"]]`，否则泄漏）。

## 11. 配置系统

`config.json`（项目目录）：`load_config()` 用 `DEFAULT_CONFIG` 合并磁盘值（忽略未知键）+ `normalize_config` 钳制与枚举校验。

关键字段：api_key、base_url、model、scenario（通用/编程/Agent/自定义）、thinking（none/low/high/max/xhigh）、max_tokens(1024-65536)、seed、tools_enabled、enabled_tools、system_prompt、max_context_chars、max_context_tokens、min_kept_turns、timeout、font_size、theme、md_render、restore_session、**json_output**（response_format）、**beta_api**（base_url 自动加 /beta，开启前缀续写与 FIM）、**peak_warning**（高峰时段提示）。

- `save_widgets_to_config()`：**只在控件值与 cfg 不一致时落盘**（避免每轮发送都写文件）。
- `ensure_client()`：按 key/base_url/model/timeout 缓存 client；beta_api 开启时 base_url 拼接 `/beta`（已带则跳过）。
- 修改系统提示词时（`edit_system_prompt`）若内容变化且会话有历史 → 弹出**缓存警示**确认（前缀缓存从 0.02 元涨到 1 元/百万）。

## 12. 上下文管理

- `tokens.py`：`estimate_messages_tokens` 用 tiktoken `o200k_base`（失败回退 1.5 字符/token）；`message_token_counts` 按**对象身份缓存**每条消息估算（大上下文只重算新消息）。
- `send()` 追加 user 消息后立即 `self._ctx_counts = tokens.message_token_counts(self.messages)`，`_context_over_limit()` 与 worker 的 `_trim_context(counts=...)` **复用同一份计数**（避免重复全量编码）。
- `_trim_context`：双阈值（token+字符）从最早轮次硬裁剪，保留 `min_kept_turns` 轮；裁剪内容 `_archive_dropped` 归档（**隐私模式不写盘**）。
- `_compress_old_history`：先用 LLM 总结旧轮（`_call_summary` 非流式、思考关闭），失败回退硬裁剪。摘要消息固定插在索引 1（前缀稳定，缓存友好）。

## 13. 工具调用（Agent 循环）与 DeepSeek 特性

- `TOOLS`：get_date / get_weather / calculate / run_python / read_file / fetch_url；`TOOL_CALL_MAP` 映射实现。**新增工具需同时**：加 TOOLS 定义、加 TOOL_CALL_MAP（工具设置对话框自动遍历，无需改）。
- `chat()` 循环最多 `MAX_TOOL_ROUNDS=10` 轮：流式收集 → 空响应自动重试 `MAX_EMPTY_RETRIES=1` → 工具逐个执行并回传结果，参数解析失败把错误原文回传模型自主修正 → 同工具同参数连续 3 次触发循环防护。
- **`_sanitize_messages`（入口必调）**：原地过滤空 content 且无 tool_calls 的 assistant 消息（避免 400）；有 tool_calls 的消息 content None 归一为 `""`。
- 思考模式与采样参数互斥：`thinking=none` 时传 temperature/top_p，否则传 `reasoning_effort`（low→high、xhigh→max 映射）。
- **JSON 输出**（`json_output=True`）：`kwargs["response_format"] = {"type": "json_object"}`；构造 `work = [json_hint] + messages`（hint 为 system 消息，保证 prompt 含 json 字样），循环内所有 append 针对 `work`，`finally` 中 `messages[:] = [m for m in work if m is not json_hint]` 同步——**hint 绝不污染会话消息**。
- **前缀续写**（`continue_prefix=True`）：要求末条为 assistant，将其 `dict` 副本加 `prefix=True` 放入请求；流式内容合并进该副本（content/reasoning_content 拼接，不新增消息）；**`finally` 无条件 `m.pop("prefix")` 清理所有残留**（历史 bug：仅 json_output 分支清理导致 prefix 泄漏到会话）。`work` 与 `messages` 是同一列表对象（无 json_hint 时），`work[-1] = last` 直接修改会话末条，因此同步天然成立。
- **FIM 补全**（`fim_complete`）：`client.completions.create(prompt=, suffix=)`，base_url 自动补 `/beta`（不依赖全局开关），最大 4K。
- **峰谷定价**：`is_peak_hour()` 判断北京时间 9-12/14-18 高峰（价格 2 倍）；状态栏常显「⏰ 高峰时段」，发送前每日首次 `_flash_status` 提示一次（`_peak_notified` 记录日期）。

## 14. 快照与持久化

- `save_snapshot` 写 `session_latest.json`（messages/usage_total/stars/top/model/scenario/saved_at）；`_maybe_save_snapshot` 节流 2s；触发点 `_finish`/`send`/`on_close`。
- `save_session_to_file` 写 `sessions/<id>.json`（含 top/tags/stars/pinned）；`close_tab` 时保存（**ephemeral 或隐私模式跳过**）；`load_session_from_file` 按需载入并补 `first_user`。
- `_restore_snapshot` 启动恢复：消息 + usage_total + stars + **top**，补 `first_user`，随后 `update_status()` + `_update_context_bar()`。
- **用量统计**：`_apply_usage` 先累计到内存 `_pending_stats`（按模型），`_flush_stats` 批量写 stats.json——触发点：10 秒节流 / `_finish` / `on_close`（历史优化：原来每次 usage 回调读+写整个文件，Agent 多轮时 IO 频繁）。
- `_monthly_cost()` 结果缓存 30 秒（状态栏/预算共用）。

## 15. 对话操作与编辑

- 右键菜单（`_on_chat_menu`）：复制（选中/消息/代码块/全部/Markdown 原文）、编辑重发、重新生成、删除、收藏、固定、分叉、**引用此消息回复**（`_quote_message` 插入 `> 引用` 文本到输入框）、继续生成（Beta）。
- 变体：`regenerate_variant` 把当前回复存入会话 `variants`，以 `base_seed + len(variants)` 作为 `_variant_seed_override` 重新生成；`show_variants` 对话框查看/恢复（`_replace_last_reply` 替换末条 assistant content + rebuild）/复制。**`_variant_seed_override` 持久存在直到用户普通发送**，可连续生成多版。
- 引用定位：`_msg_index_at` 用 tag_ranges + content 比对（assistant 消息经 mdparse 反向渲染比对）；blocks 中 content 块的 `msg_idx` 是关联依据（重建时必须与 `self.messages.index(msg)` 一致）。
- 收藏跳转：`show_stars` 双击/「跳转」按 role+content 匹配 `_scroll_to_message`。
- 会话置顶：`session["top"]` 标志，列表排序 `visible.sort(key=lambda s: not bool(s.get("top")))`（稳定排序），显示名加 📌，随快照与历史文件持久化。
- 历史会话库（`show_history_sessions`）：Listbox 为 `selectmode="extended"` 支持多选；「全选/取消」切换全选（已全选时反选）；「批量删除」多选时确认框提示数量，`reversed(sel)` 遍历删除防索引漂移，删除后 `reload()` 原地后台重扫（不再关闭重开对话框）；「载入」仅限单选。
- 搜索：`_do_search` 上限 `MAX_SEARCH_MATCHES=2000`（防卡死），200ms 防抖；搜索前展开折叠、搜索后恢复。
- 输入框 token 估算：`<KeyRelease>` 300ms 防抖 → `_refresh_input_tokens` 更新 `input_hint_lbl`「约 X token」。

## 16. 增量演进模块（1.2.0 ~ 1.10.8）

### 1.2.0

- **exporters.py**：`export_html(messages, path, ...)` / `export_jsonl(messages, path)` 纯函数；`export_history` 末尾追加调用（失败仅日志，不影响 md/txt）。
- **输入历史**：`send()` 内 `session.setdefault("sent_history", []).append(text)`（上限 200 条，去重相邻重复）；`_input_history_nav(delta)` 绑定 `<Alt-Up>/<Alt-Down>`，首次按↑保存 `_hist_draft`，越过最早一条回到草稿。
- **Ctrl+Enter**：`<Control-Return>` → `send()`；**Ctrl+Shift+V**：`_paste_as_link` 剪贴板为 URL 时转 `[链接](url)`（有选区用选区文字），非 URL 时 `event_generate("<<Paste>>")` 放行。
- **惰性折叠**（`fold_early_threshold` 默认 0=关）：`_fold_early_view(blocks)` 只构造渲染视图、**不修改 self.blocks**；折叠前存入会话 `_early_snapshot`；点击 `fold_hint` 提示 → `_on_fold_early_click` 设 `_early_expanded`（本次会话不再折叠）并重渲染。⚠️ 展开后必须设标志，否则 `_render_all` 会重新折叠（历史教训）。
- **Profile**（`profiles.json`，`load_profiles`/`save_profiles`）：`manage_profiles` 对话框管理；`_apply_profile` 只同步 cfg + 控件（api_key/base_url/model/current_profile），**不改 ensure_client**——控件变化自然触发客户端重建，切回默认 Profile 时 `_apply_profile("", ...)` 用空 key（从 config 原配置恢复）。
- **自动更新**：`_handle_update_result` 确认下载前先 `import backup; backup.make_backup()` 备份当前源码。

### 1.3.0（智能体行动层，默认全部关闭）

- **permissions.py**：模块级 `init(path, workspace, audit_dir)`；`DEFAULT_PERMISSIONS` 全关闭；`resolve()` 规范化路径防 `..` 穿越；`check_filesystem(path, write)` 判定（允许目录内 + 系统目录/AppData 阻止列表 + 写开关）；`check_shell(cmd)` 解析 argv 并白名单/黑名单判定；`request_approval(name, args)` 按 approval_mode 放行（auto/confirm/deny）；`audit()` 写 actions.log（10MB 轮转，`set_audit_enabled` 关隐私模式）。
- **工具**（TOOLS 尾部新增 6 个，`TOOL_CALL_MAP` 同步）：`write_file`（原子写 .tmp→os.replace + 覆盖前 .bak + 大小上限）、`edit_file`（文本或正则替换 + .bak）、`list_dir`（只读）、`run_command`（argv 直传 subprocess，禁止 shell 拼接，超时/截断）、`search_local`（允许目录内文本检索，跳过常见噪音目录）、`create_doc`（md/html 原生，docx 需 python-docx 可选）。
- **审批闸门**：`chat(..., on_approval=None)`——工具执行前回调 `(name, raw_args) -> (allowed, reason)`；拒绝时 `args={}` 并把 reason 回传模型（利用现有"参数解析失败回传自主修正"机制）。main 侧 `_tool_approval` 仅对 `permissions.ACTION_TOOLS` 生效；confirm 模式 `_request_approval_dialog`（worker 线程 put `approval_req` + `threading.Event` 阻塞等待，超时自动拒绝），主线程 `_show_approval_dialog` 弹「允许/拒绝」。
- **队列**：`_drain_ui_queue` 仅新增 `approval_req` 分支（不改其他消息）。
- **默认启用边界**：`DEFAULT_CONFIG["enabled_tools"]` 与 `normalize_config` 的 else 分支均为 `BUILTIN_TOOL_NAMES`（6 个内建）——行动工具默认不进请求，须用户在「工具设置」勾选 +「权限设置」开启开关。
- **UI**：工具菜单「权限设置」对话框（写开关/允许目录/命令白名单/审批模式），保存走 `permissions.save()`（不落盘自动工作区目录）。
- ⚠️ 铁律：权限拒绝结果必须回传模型（不能静默丢弃）；`run_command` 永远默认 false；审批弹窗是唯一允许的跨线程 UI 通道（仍走队列）。

### 1.4.0（L2 浏览器 / L3 工程 / L4 草稿）

- **write_code_project(project_dir, files)**：批量写文件——`files` 为 `[{path, content}]`（≤50 个），相对路径防 `..`（`".." in rel.split("/")`）与越界（`startswith(base + os.sep)`）双重校验；逐文件 `_atomic_write`；审计 `write_code_project`。
- **browser_navigate / web_screenshot**：`_playwright_ready()` 检查可选依赖 playwright；未安装返回安装提示（`pip install playwright && playwright install chromium`）；`sync_playwright` 同步 API，headless chromium，动作 open/click/type/get_text；截图保存到 `WORKSPACE_DIR/screenshot_*.png`。
- **publish_draft(platform, title, content)**：写入 `WORKSPACE_DIR/drafts/<platform>_<title>_<ts>.md`，只建草稿**绝不发布**；已加入 `ACTION_TOOLS`（confirm 模式下双确认）；审计 `publish_draft`。
- **ACTION_TOOLS** 现含 7 个：write_file / edit_file / run_command / create_doc / write_code_project / publish_draft（browser 工具不列入，其动作经 playwright 沙箱）。
- **TASK_TEMPLATES** 扩至 6 个：新增 创建代码工程 / 生成本地文档 / 执行项目测试（引导模型走 write_code_project → run_command → edit_file 修复链路）。
- 所有新工具仍默认不进 `enabled_tools`（`BUILTIN_TOOL_NAMES` 仅 6 个内建）。

### 1.5.0（效率与智能体闭环）

- **crypto.py（DPAPI）**：`encrypt`/`decrypt`，`PREFIX="dpapi:"`；`load_config` 解密、`save_config` 用副本加密写盘（**不修改内存 cfg**，内存恒明文）；旧明文自动兼容；密文绑定 Windows 用户，跨机器无效。备份 zip 中 config.json 为密文。
- **输入草稿**：`_schedule_draft_save`（KeyRelease 4s 防抖）→ `DRAFT_PATH=draft.json`；启动 `_restore_draft`；`on_close` 保存；隐私模式跳过。
- **剪贴板即问**：Ctrl+Shift+Q / 编辑菜单 → `_paste_clipboard_ask`（剪贴板前 20000 字符进输入框）。
- **数据清理**：`show_cleanup` 勾选 12 类数据（sessions/快照/统计/归档/日志/工作区/提示词/自定义工具/Profile/权限/定时/记忆），`_delete_target` 逐个删除（日志被占用文件静默跳过）。
- **计划确认**：`permissions.plan_confirm`（默认 false）；`chat(on_plan=...)` 每轮拿到 tool_calls 后先整轮确认——拒绝时给每个 tool_call 回传 reason 并 `continue`（模型可见原因自主调整）；main 侧 `_plan_gate`（worker 线程，超时自动取消）+ `_show_plan_dialog`（plan_req 队列消息）。⚠️ `_plan_gate` 阻塞等待，**禁止在主线程调用**（会死锁）。
- **工作区文件树**：`show_workspace_tree`（Treeview 递归 workspace，双击注入文件内容到输入框，右键复制路径）。
- **长期记忆**：`MEMORY_PATH=memory.json`（enabled/facts）；`_memory_prompt_text` 构建固定文本（位置固定、内容固定 → 缓存友好）；`chat(memory_text=...)` 与 json_hint 同机制注入 work + finally 清理；管理对话框 `manage_memory`。
- **完成通知**：`notify_on_done` 配置；`_finish` 末尾 `_flash_taskbar`（FlashWindowEx）+ `bell()`；`_finish` 现在置 `assistant_answered=True`。
- **定时任务**：`SCHEDULES_PATH=schedules.json`；`_scheduler_loop` daemon 线程每 30s 检查 HH:MM + 当日未执行（`last` 字段）→ `put(("timer_task", text))` → 主线程 `send(text)`；`manage_schedules` 对话框。
- **语音输入**：🎤 按钮 → PowerShell `System.Speech` 同步听写（45s 超时）→ `put(("speech", text))` → `_insert_speech` 插入输入框。
- **剪贴板 OCR**：`_ocr_clipboard` → PIL `ImageGrab.grabclipboard` 存 png → PowerShell WinRT `Windows.Media.Ocr`（内置 Await/AsTask 定义，utf-8-sig 脚本）→ 结果经 speech 队列插入；Pillow 缺失时提示。
- **缓存统计增强**：`show_context_details` 顶部展示最近一轮输入/命中/未命中/占比。
- 新队列消息：`plan_req` / `speech` / `timer_task`（均为新增分支）。

### 1.6.0（开箱即用与产品化）

- **场景包**：`SCENARIO_PACKS`（办公/开发/创作）——`apply_scenario_pack(name)` 一次性写入 cfg（thinking/system_prompt/enabled_tools）+ permissions（allow_write/allow_run_command/approval_mode）+ 保存 + 刷新控件；`pack_combo` 在设置面板模型组（`setup_widgets_from_config` 恢复）。⚠️ 修改 system_prompt 会破坏前缀缓存（主动切换场景的可接受代价）。
- **试玩任务库**：`PLAYGROUND_TASKS`（10 个）→ 工具菜单 cascade；`_run_playground` 确认后 `send(prompt)`；欢迎页新增"先体验一个试玩任务"入口（done() 后 300ms 触发）。
- **项目上下文**：`project_context` 配置（默认关）；`_project_context_text()` 扫描 WORKSPACE_DIR 顶层（≤30 项）生成概览，60 秒缓存（`_proj_ctx_cache/_proj_ctx_time`）；与长期记忆合并进 `_memory_prompt_text`（经 memory_text 通道注入，固定内容缓存友好）。
- **任务报告**：`_finish` 中若 `_agent_tool_count > 0`，插入 `[任务报告]` note（次数/耗时/输入输出 token）。
- **工具进度**：`_drain_ui_queue` 的 tool 分支状态栏显示 `⚙ 正在执行「工具名」（第 N 个）…`。
- **一键回滚**：`_restore_bak(path)` 用 `path.bak` 覆盖还原（askyesno 确认）；最近产物对话框新增「还原 .bak」按钮。
- **省钱报告**：`show_stats` 计算累计缓存节省 = Σ cache_hit × (prompt 价 − cache_hit 价)。
- **反馈收集**：`FEEDBACK_PATH=feedback.json`；右键消息「👍 有用 / 👎 没用」→ `_feedback` 记录（上限 500）；`show_feedback` 统计与查看。
- **对话分享**：`copy_share_text` 复制 `build_markdown()` 到剪贴板。
- ⚠️ 新 UI 控件必须注册 `_restyle`；场景包相关弹窗均走现有队列/对话框模式。

### 1.6.1（完全智能模式）

- **permissions.py**：`FULL_AUTO` 全局 + `set_full_auto()`/`is_full_auto()`；三处放行——`check_filesystem` 跳过 allow_write、`check_shell` 跳过 allow_run_command、`request_approval` 直接 True。**blocked_dirs 阻止列表仍生效**（系统目录永不碰），审计照常。
- **main.py**：`full_auto` 配置（默认 false，normalize/启动 `set_full_auto` 同步）；设置面板「自主模式」组 checkbox → `_on_full_auto_toggle`（开启时 askyesno 确认，同步 permissions + cfg + save + update_status）；`_plan_gate` 在 full_auto 时直接放行；状态栏 `🤖 完全智能` 标识；权限设置对话框显示当前模式提示。
- 安全语义：完全智能 = 授权范围放大（允许目录内全自动），**不是**权限边界消失——resolve 防穿越、blocked_dirs、审计日志、隐私模式全部保留。

### 1.7.0（任务执行可见性）

- **taskpanel.py**：`TaskPanel` 悬浮窗（overrideredirect + topmost + 可拖动，右下角定位）。接口（**均须主线程调用**）：`begin()`（重置计数+计时+显示）/ `add_tool(name, result)`（✅❌ 统计 + 产物计数 + 最近 5 条滚动）/ `finish(summary)`（标题更新 + 3s 后自动隐藏）/ `hide()` / `destroy()`；`_tick` 300ms 刷新耗时。
- **main 集成**：`_begin_assistant` 懒创建并 `begin()`；`_drain_ui_queue` tool 分支 `add_tool`（失败同时状态栏 flash「⚠ 工具失败，AI 正在修正…」）；`_finish` `finish(summary)`；`on_close` `destroy()`。
- **工具卡片摘要**：`_render_blocks` tool 分支标题 = `✅/❌ [工具] name · duration · 结果首行(44 字)`；`failed` 时 `_insert_fold(..., visible=True)` **失败自动展开**。
- **任务报告升级**：`[任务完成] ✅/⚠ 工具 X 成功 / Y 失败 · 耗时 · token`（遍历 blocks 统计 tool 结果）。
- ⚠️ 面板所有更新必须在主线程（队列驱动）；`finish` 的自动隐藏用 `root.after`，`begin` 会 `_cancel_hide` 防止旧隐藏任务干扰。

### 1.7.1（进程终端）

- **deepseek_client.py 后台进程管理**：`PROCESSES`（name → entry，lines 用 `deque(maxlen=2000)`）；`start_process(command, name)`——`check_shell` 校验 + **python 命令自动插入 `-u` 无缓冲**（关键：管道输出全缓冲会导致服务器日志不实时）+ `Popen(stdout=PIPE, stderr=STDOUT, CREATE_NO_WINDOW)` + reader 线程逐行读并 `_emit_process`；`stop_process(target)`（名或 pid）；`list_processes()`；`stop_all_processes()`（退出清理）；上限 `MAX_PROCESSES=8`，启动前清理已退出条目。
- **processpanel.py**：`ProcessPanel` 独立终端窗口（进程下拉切换/停止/清空/自动跟随），`open/hide/close/reload_processes/process_started/append_line/process_exited`；缓冲每进程 2000 行。
- **main 集成**：`set_process_output_callback(self._process_output)`（reader 线程 → `put(("proc_out", ...))`）；`_proc_panel_append` 解析「── 进程启动/退出」标记驱动面板生命周期；工具菜单「进程终端」；`on_close` 关闭面板 + `stop_all_processes()`（防孤儿进程）。
- **工具**：start_process/stop_process/list_processes 加入 TOOLS 尾部与 `ACTION_TOOLS`；开发场景包默认启用。
- 输出**不回传模型**（防污染上下文），模型经 `list_processes` 查状态；文件路径不出现在面板文本的 filelink 逻辑中（面板独立于聊天区，无 link 处理）。

### 1.7.2（工作目录机制）

- **config `active_dir`**（默认 "" → 回落 `WORKSPACE_DIR`）；`_get_active_dir()` / `_set_active_dir(path, add_perm=True)`（校验存在 + 自动加入 `permissions.allowed_dirs` 并落盘 + save_config + update_status）。
- **注入**：`_working_dir_prompt()` 生成「[当前工作目录] …新任务请在该目录下创建独立子目录（按任务名命名）…」——并入 `_memory_prompt_text`（与记忆/项目上下文同通道，固定内容缓存友好），**不改系统提示词**（不破坏前缀缓存）。
- **UI**：输入框旁「📁 目录」按钮 + 工具菜单「工作目录…」→ `choose_working_dir` 对话框（当前显示 / 路径输入 + 浏览 / 常用目录列表（workspace + allowed_dirs）/ 新建子目录 / 设为工作目录）；状态栏常显 `📁 <active_dir>`（超 28 字符省略号截断）。
- **场景包**：`apply_scenario_pack` 将 active_dir 重置为 WORKSPACE_DIR。
- ⚠️ 工作目录提示是固定文本注入（位置稳定），切目录后文本变化会损失一次前缀缓存——可接受（用户主动切换）。

### 1.8.0（任务质量闭环）

- **TASK_QUALITY_GUIDE**（常驻行为指令）：先输出执行计划再执行 / 完成度自检（产物、进程、验证）/ 网页必须 start_process + fetch_url 验证——随 memory_text 通道注入（固定内容缓存友好）。
- **相关文件自动注入**：`_relevant_files_text(text)` 从用户消息提取路径（PATH_RE + 裸文件名正则，工作目录内优先）→ 读取前 6000 字符 → 注入；`send()` 设置 `self._current_inject_text`（worker 经 `_memory_prompt_text` 消费）。⚠️ 注入内容随任务变化会损失一次前缀缓存（主动提及文件时才有，可接受）。
- **项目关键文件摘要**：`_project_context_text` 增强——顶层 README/package.json/pyproject.toml/requirements.txt 前 1500 字符摘要附加。
- **成功模式记忆**：`PATTERNS_PATH=patterns.json`；`_finish` 全成功时 `_record_success_pattern()` 记录工具链（上限 10 条）；`_patterns_text()` 最近 3 条注入（提示复用）。
- **environment_info 工具**：Python 版本 + 常见包检测（importlib.util.find_spec）+ 工作区磁盘空间；`TOOL_CALL_MAP` 同步；开发场景包默认启用。
- **验证强制化**：开发场景包提示词 + 试玩「创建迷你网站」模板要求 start_process → fetch_url 验证 → web_screenshot 截图。

### 1.9.0（自我进化）

- **deepseek_client.py**：`PROJECT_DIR`（= 模块目录，即项目根）；`_current_version()`（正则提取 main.py VERSION）；`project_info()`（版本 + 文件清单 + py 统计，只读）；`read_project_file(path)`（仅 PROJECT_DIR 内 + 白名单扩展名，80K 截断）；`create_evolution(name, files)`（写入 `evolutions/<name>_<ts>/` 分支——`..` 越界校验 + 类型白名单 + EVOLUTION.md 缺失自动生成；**绝不写项目原文件**）；`EVOLUTIONS_DIR` 导出。
- **main.py**：`EVOLUTIONS_DIR`；`TASK_QUALITY_GUIDE` 追加第 4 条自我进化行为指令（注入通道）；工具菜单「自我进化」→ `show_evolutions`（提案列表 + EVOLUTION.md 查看 + 文件清单）；`_show_evolution_diff`（difflib.unified_diff 预览）；`_apply_evolution(name)`（**先备份原文件为 .evobak 再复制分支文件** + 目录改名 `_applied` 标记，避免重复采纳）；忽略 = 删除分支；`list_evolutions` 排除 `_applied`。
- **backup.py**：`EXCLUDE_DIRS` 加入 `evolutions`（备份不含提案分支）。
- ⚠️ 采纳操作**只能由用户手动触发**（UI 按钮），AI 无法直接改原文件——自我进化的最终决定权永远在用户。

### 1.9.1（产物核验闭环）

- **写工具真实核验**：`_atomic_write` 返回 `(created, real_size)`（os.path.getsize 实测）；`write_file`/`edit_file` 返回追加"已核验存在 + 实际 N 字节"，写入后 `os.path.exists` 失败则返回错误。
- **write_code_project**：不再遇错即停——逐文件收集 `created`/`failed`，返回完整明细（成功清单 + 失败原因 + 全部核验存在）。
- **verify_files(paths)**：新工具，批量核验存在性/大小（相对路径基于 WORKSPACE_DIR 解析，只读，≤30 个），返回 ✅/❌ 逐项 + 汇总；开发场景包默认启用。
- **行为指令**：`TASK_QUALITY_GUIDE` 新增第 5、6 条——写文件后必须 verify_files/list_dir 自检，缺失立即修正**不得继续后续步骤**；任务完成前核验全部声明产物。
- **任务报告自动核验**：`_finish` 统计本轮 `write_file`/`create_doc` 结果中声明路径的实存性，追加 `✅ 产物核验：N 个文件均真实存在` 或 `⚠ 产物核验：N/M 未找到：…`。
- ⚠️ 该闭环解决的核心问题：模型"幻觉式声明"（回复里说已创建但实际没调用工具/写入失败被忽略）——工具结果、行为指令、任务报告三层共同杜绝。

### 1.9.2（自我进化主动化）

- **主动发起**：`show_evolution_audit` 对话框（5 个重点：全面/性能/安全/体验/代码质量）→ 构造审查指令 `send()`——project_info → read_project_file 关键模块 → 定位问题（≥2 个）→ create_evolution 提案（EVOLUTION.md 含改动/原因/风险/验证）→ 总结。菜单「🧬 立即自我审查…」。
- **督促提醒**：`evolution_reminder_days` 配置（默认 7，0=关闭）；`_last_evolution_time()` 扫描 evolutions/ 目录最新分支 mtime；`_maybe_remind_evolution()` 启动 5 秒后检查——无提案或超期则状态栏 flash 提示。
- 与被动进化的区别：无需用户构思需求，一键即触发完整"感知→分析→提案"流程。

### 1.9.3（自我进化工具无条件可用）

- **根因修复**：自我进化工具（project_info/read_project_file/create_evolution/verify_files）此前依赖 `enabled_tools`——未勾选时模型根本看不到它们，审查任务退化为拿工作区工具瞎找代码（工作区是空的）。
- **`SELF_EVOLUTION_TOOLS`**（deepseek_client.py）：chat() 工具过滤逻辑——`tools_enabled` 开启时豁免（无论 enabled_tools）；**tools_enabled 关闭时仍附带**（自我进化是"安全只读 + 分支提案"，不涉及工作区写操作）。普通工具过滤不受影响。
- **审查指令强化**：明确"项目位于程序安装目录而非工作区；必须用 project_info/read_project_file；禁止用 list_dir/read_file/search_local 分析自身"。
- **TASK_QUALITY_GUIDE 第 4 条**同步强化（专用工具 + 项目位置）。

### 1.9.4（采纳鲸语自我进化提案）

首次由鲸语自我审查产出的提案（workspace/code-review/鲸语代码审查与改进提案.md），全部 9 项采纳：

1. **循环防护补 tool 结果**（高）：`chat()` 循环防护触发时为本轮全部 tool_calls 补齐 `role="tool"` 响应消息，杜绝历史残留悬空 tool_call（下一轮请求 400）。
2. **stats.py 加锁**（中）：`record_usage` 读-改-写全程持 `_LOCK`（并发 2×50 次验证 == 100）。
3. **read_file 纳入权限模型**（中·安全）：与 list_dir/write_file 一致先 `check_filesystem`；描述同步标注"须在允许目录内"（默认工作区；需要读其他目录请在权限设置加 allowed_dirs）。
4. **tokens.py 真 LRU**（低·性能）：`move_to_end` + `popitem(last=False)` 淘汰最旧一条，替代整表 clear。
5. **exporters 行内代码**（低）：`str.replace` 正则样式 → `re.sub`，`code` 样式真实生效。
6. **crypto 解密失败**（低）：返回 `""` + logging.exception（防密文当明文用）。
7. **save_config 原子写**（低）：写 `.tmp` → `os.replace`。
8. **隐私模式彻底移除文件日志**（低）：`_apply_privacy_logging` 移除 RotatingFileHandler（WARNING/ERROR 也不落盘），关闭时恢复。
9. **关键异常补日志**（低）：processpanel reload、_emit_process 等 `except: pass` 改为 logger.debug。

**验证**：14 项全部通过（悬空 tool_call 的 sanitize 行为、并发统计、read_file 三态、LRU 命中与结果一致、行内代码、解密失败、隐私日志移除/恢复）。此版本是"鲸语审查自己的代码并修复自己"的首个完整闭环。

### 1.9.5（自我进化工作流重构：审查出报告，开发 AI 实施）

- **工作流变更**（用户引导的优雅分工）：鲸语只做诊断——审查指令改为**用 create_doc 在工作区 code-review/ 生成《鲸语代码审查报告_日期时间.md》**（问题总览表 / 每项【现状代码/替换代码/验证方式】/ 低危观察项 / 实施步骤 / 风险回滚），**不再要求 create_evolution 提交代码分支**。
- **审查指令重写**：至少 3 个问题按严重度排序；替换代码必须是完整可直接使用的补丁；完成后回复报告路径与核心摘要。
- **菜单**：「🧬 自我审查（生成报告）…」+「🧬 打开审查报告目录」（`open_review_reports` 自动创建 code-review/ 并打开）。
- **行为指令第 4 条**：改为"审查产出是报告文档（写入工作区 code-review/），供开发 AI 实施，不要直接修改代码"。
- create_evolution / evolutions/ 分支机制保留（用户显式要求代码提案时仍可用）。
- ⚠️ 职责边界：鲸语=诊断报告（只读+写报告）；开发 AI=实施（读报告改代码）；人类=把关。报告路径在回复中给出（可点击打开）。

### 1.10.0（采纳第二份审查报告）

由鲸语第二次自我审查产出（code-review/鲸语代码审查报告_2026-08-02_181220.md），6 核心 + 3 观察项全部实施：

1. **run_python 安全门**（🔴）：`_RUN_PY_FORBIDDEN` 静态黑名单（os.system/remove/subprocess/eval(/socket/urllib.request 等）+ `_RUN_PY_WRITE_OPEN` 写模式 open 拦截 + 审计 run_python_blocked。⚠️ **实施中修正了提案的两处正则缺陷**：① `|a|x` 分支未分组导致误拦含字母 a/x 的合法代码；② 未支持 `r`/`b`/`u` 字符串前缀导致 `open(r'...','w')` 漏拦。
2. **calculate DoS 防护**（🟠）：移除 ast.Pow（契约本不支持 `**`）+ 深度 32/字面量 16384bit/结果 10 万位限制。
3. **publish_draft 路径穿越**（🟠）：platform 清洗 + `check_filesystem` + normpath 二次兜底（实现采用清洗方案，穿越参数写入草稿箱内而非显式拒绝，同样安全且更友好）。
4. **crypto fail-closed**（🟡）：`CryptError` 异常 + save_config 加密失败跳过 api_key（明文永不落盘）。
5. **stats 原子写**（🟡）：`.tmp` + `os.replace`。
6. **首启误报**（🟢）：`_is_empty_shell(DATA_DIR)` 区分首次运行与真崩溃。
7. **L7** exporters 粗体 `re.sub` 成对替换；**L6** stop_process `wait(timeout=3)`；**L13** read_project_file 支持 offset/limit 分页（大文件自我审查能力提升）。

**验证**：22 项全过（真实攻击用例：os.remove/写模式/urllib/链式幂/路径穿越/加密失败；合法回归：sum/读 open/粗体/分页）。

### 1.10.1（第二轮审查新增项）

第二轮报告（1.9.5 基线）中 1.10.0 已修 1-6 项（核对确认），本版本实施新增项与顺手项：

- **问题 7（🟡）Profile 明文落盘**：`save_profiles` 全部 api_key 经 `crypto.encrypt`（DPAPI），`load_profiles` 经 `crypto.decrypt`；旧版明文自动兼容；加密失败（CryptError）整次保存失败（fail-closed，磁盘保持旧文件）；写入改原子。
- **问题 8（🟢）产物核验名单**：`_finish` 核验覆盖 `write_code_project` / `edit_file`（+ 原 write_file/create_doc）。
- **L15**：`run_python` 加入 `permissions.ACTION_TOOLS`（confirm 模式也需审批，与静态拦截双保险）。
- **L17**：stats 新增公共只读接口 `empty_day()` / `pricing()`；show_stats 不再访问私有成员。
- **L18**：`_update_context_bar` 局部变量 `tokens` → `n_tokens`（消除模块遮蔽）。
- **L19**：`_atomic_json_write` 统一原子 JSON 写（.tmp+os.replace），覆盖 _save_recent/_save_patterns/_save_schedules/_save_memory/_save_feedback + 快照 + 会话文件。
- **L22**：`_apply_dark_titlebar` 合并为单次 after(400)。
- **L23**：`_call_summary` 显式 timeout=30（失败走硬裁剪兜底）。

**验证**：13 项全过（Profile 加密落盘/往返/明文兼容/故障注入磁盘不变、核验名单、ACTION_TOOLS、原子写）。

### 1.10.2（自我进化第三维度：功能建议）

- **`show_feature_suggestions`**：菜单「🧬 功能建议（升级方向）…」→ 构造建议指令 `send()`——project_info 全貌 → read_project_file 关键模块（含分页）→ 结合用户场景 + DeepSeek 能力特性 + 业界趋势 → 提出 6-10 个建议（名称/一句话描述/价值/实现思路/复杂度/优先级）→ create_doc 写入工作区 code-review/《鲸语功能建议_日期时间.md》→ 回复路径 + Top 3 摘要。
- 与审查的定位区分：**审查=找问题（报告补丁）；建议=找方向（功能蓝图）**；与代码提案（create_evolution 分支）并行，三者构成自我进化的完整能力面。
- 产出文档与审查报告同目录（code-review/），「打开审查报告目录」统一入口。

### 1.10.3（采纳功能建议一期）

鲸语功能建议文档（code-review/鲸语功能建议_20260802_184858.md）10 项中的一期+低风险项：

- **建议 3 智能思考档 auto**：`THINKING_MODES` 增加 "auto"；`_auto_effort(work)` 启发式路由（长度 >300 / 代码围栏 / 复杂意图词 → max；单一特征 → high；寒暄 → none）；chat() 中 auto 时动态选 effort。设置面板下拉自动包含。
- **建议 9 主动建议引擎**：`suggestions_enabled` 配置（默认开）；`_suggest()` 在 `_finish` 末尾启发式判断（代码块→开发场景包 / 翻译润色词→指令模板 / 路径→工作目录）；`_show_suggestion_bar` 右下角非模态建议条（6 秒自动消失，「采纳」直接调用对应功能）。
- **建议 10 项目任务记录**：`<工作目录>/.whaletalk/tasklog.json`；`_record_tasklog` 在任务完成时记录（工具链 + 产物路径 + 标题，上限 20）；`_tasklog_prompt` 注入最近 3 条（与记忆/项目上下文同通道，缓存友好）；工具菜单「项目任务记录…」查看/清空。
- **未实施（后续）**：建议 1 知识库 RAG、建议 4 对照回答、建议 5 断点续跑、建议 6 后台任务、建议 7 省钱调度（中-高风险/大工程）；建议 2 上下文地图/建议 8 缓存前缀已有基础（show_context_details 分组 + 缓存警示）。

**验证**：15+7 项全过（auto 四态路由 + 真实请求 effort 断言、tasklog 记录/产物/注入/记忆合并）。

### 1.10.4（任务面板懒启动）

- **体验修复**：此前 `_begin_assistant` 无条件 `task_panel.begin()` 导致纯对话也弹悬浮面板。
- **TaskPanel**：`prepare()`（重置计数/计时/最近列表，不显示，`_started=False`）；`add_tool()` 内首个工具调用时自动 `begin()`（懒启动）；`finish()` 若从未启动（纯对话）直接复位返回，不弹窗不调度隐藏。
- **main**：`_begin_assistant` 改调 `prepare()`；tool 事件经 `add_tool` 触发显示。
- 行为：纯对话全程无面板；Agent 任务首个工具调用瞬间弹出。

### 1.10.5（对话/任务分离：纯对话模式）

- **背景**：任务指令注入（TASK_QUALITY_GUIDE / 成功模式 / 任务记录 / 24 个工具 schema）污染了纯对话场景——连"你好"都被引导成任务化。
- **config `pure_chat`**（默认 false）：设置面板「自主模式」组开关（`_on_pure_chat_toggle`），状态栏 `💬 纯对话` 标识。
- **注入分级**：`_memory_prompt_text` 纯对话分支仅注入 长期记忆（用户偏好）+ 工作目录；任务模式保持全量（记忆/项目上下文/工作目录/行为指令/成功模式/任务记录/相关文件）。
- **工具隔离**：`chat(pure_chat=True)` 完全不传 tools schema（包括 SELF_EVOLUTION_TOOLS 豁免分支）；worker 按 cfg 传递。
- 行为：纯对话时 AI 无任何工具语义引导，回归纯粹对话/写作/查询能力。

### 1.10.6（纯对话人格重写）

- **用户反馈修正**：旧版 DIALOG_SYSTEM_PROMPT 含"不提及工具/工作目录/任务流程，不要主动推销功能"——否定式指令本身强化了这些概念（"此地无银三百两"），且过度渲染。
- **新人格**（纯正向，零禁忌词）："你是一位博学、友善、富有文采的 AI 对话伙伴。请以自然、真诚、温暖的方式与人交流：认真倾听、深入思考、坦诚回答。写作时言之有物、表达优美；讨论时观点清晰、有理有据；闲聊时轻松亲切。"——不含工具/任务/工作目录/功能/否定词。
- **记忆引导语自然化**：pure_chat 分支"以下为你的长期记忆（用户手动维护…）" → "请记住以下关于用户的背景信息，在相关回答中自然参考"。
- 纯净度自动化检查：DIALOG_SYSTEM_PROMPT 对 15 个禁忌词（工具/任务/工作目录/功能/推销/不要/执行计划/验证/自检…）零命中。

### 1.10.7（建议区固定停靠）

- **用户反馈修正**：右下角 Toplevel 建议条每次对话弹出打扰 → 改为**菜单栏右侧固定停靠区**（suggestion_frame：label + 采纳 + ✕），平时隐藏、有建议时就地显示，不弹窗不遮挡。
- `_show_suggestion`（60 秒自动隐藏）/ `_hide_suggestion` / `_suggestion_apply`（执行后隐藏）；`_restyle` 注册（主题跟随）；长文本 60 字符截断。
- 验证：无新增 Toplevel、显示/采纳/关闭、发送后建议出现在固定区而非弹窗（12 项全过）。

### 1.10.8（模式三态单选）

- **冲突修复**：完全智能（工具全自动）与纯对话（不用工具）此前可同时勾选，语义互斥导致一个失效、体验混乱。
- **UI**：`mode_var` 三态 Radiobutton（标准 / 🤖完全智能 / 💬纯对话），天然互斥；`_on_mode_change` 统一处理（完全智能保留开启确认，取消时 `_sync_mode_var` 回退显示）。
- **同步**：`setup_widgets_from_config` 经 `_sync_mode_var` 恢复；`save_widgets_to_config` 由 mode 派生 full_auto/pure_chat（config 字段保留向后兼容）；permissions.set_full_auto 联动。
- 验证：三态互斥切换、取消确认回退、重启恢复、save 一致性（6+19 项）。

### 1.10.11（全代码库审查修复：安全 / 线程 / 性能 / 健壮性）

由外部审查（3 路并行）+ 逐项验证实施，共 30+ 项修复：

**高危 Bug**
- `import subprocess` 补漏（语音输入 / 剪贴板 OCR 此前 NameError 被 except 吞掉，功能静默失效）。
- `ensure_client` 线程安全：新增 `_capture_client_params()` 在主线程捕获 key/model，send/继续生成/FIM/纪要/摘要等 worker 线程一律用快照（Tk 变量非线程安全，可能偶发 TclError/崩溃）。
- 会话文件路径穿越：`_safe_sid()` 净化 `[^0-9a-zA-Z_-]`（快照/历史文件中的恶意 id 无法逃逸 SESSIONS_DIR）。
- 分帧渲染挂起项丢消息：`_paged_step` 的 TclError 分支（渲染目标被销毁的竞态）此前永久丢弃 `_send_when_ready`/`_search_when_ready`/`_pending_appends`，抽出 `_flush_paged_pending()` 成功/异常路径共用。
- `_quick_action` 忙时覆盖 `_pending_send`（打断+快速动作连点丢消息）→ 追加拼接。

**权限模型（permissions.py）**
- `_under` 判定改为 Windows 大小写不敏感（resolve 内 normcase）——C:/Windows 阻止列表此前可用 `c:\windows\...` 绕过。
- `resolve` 增加 `os.path.realpath`（junction/符号链接逃逸）+ 相对路径锚定 WORKSPACE_DIR（此前锚定进程 CWD）+ normcase。
- `check_shell` 改用 `shlex.split(posix=False)`（Windows 反斜杠路径此前被当转义符吞掉，`C:\Users\me\a.py` → `C:Usersmea.py`）。
- `FULL_AUTO` 模块默认 True → False（由 main 按 config 同步；默认不静默绕过用户审批设置）。
- 目录判定列表缓存 `_cached_dirs`（内容签名，白名单运行期增删不错乱）。
- 审计日志字段净化（换行转义 + 截断，防模型参数伪造日志行）；`add_to_whitelist` 命令只取 basename 且与 check_shell 对齐。

**Agent 工具（deepseek_client.py）**
- SSRF 防护：`_safe_url`/`_is_private_host`（回环/内网/169.254.169.254 元数据地址）应用于 fetch_url / browser_navigate / web_screenshot；UI 链接点击仅放行 http(s)。
- 数据库只读校验强化：`_readonly_stmt` 拒绝 `INTO OUTFILE/DUMPFILE/LOAD_FILE/lo_export/pg_read_file/pg_sleep/SLEEP(/BENCHMARK(` 与内部分号（SQLite 查询同步接入）。
- `_kill_tree`：Windows 超时/停止用 `taskkill /T` 杀进程树（此前 kill 只杀直接子进程，pip/pytest/服务器派生进程残留）。
- `start_process` 命名+插入同临界区（并发启动同名进程此前互相覆盖 → 进程失管）；`stop_process` 立即回收条目。
- 工具停止竞态：stop 后给已提交工具 `_STOP_TOOL_GRACE_S=1.5s` 宽限期如实记录真实结果（此前写"已中断"导致模型下轮重试 → 发信/写文件重复执行）。
- `_create_with_retry` 停止改抛 `_StopRequested` 内部信号，chat() 干净 return False（此前 RuntimeError 逃逸到 UI）。
- `run_python` 静态黑名单恢复（`_RUN_PY_FORBIDDEN`，词边界正则避免误拦；完全智能模式放行——README 承诺的沙箱自由不变）。
- `edit_file` 20MB 读入上限 + 正则长度上限；`read_file` start_line 上限 100 万行；`_atomic_write` `newline=""`（Windows CRLF 不再改变换行风格与字节账目）。
- `pip_install`/`run_tests` 改 SpooledTemporaryFile（capture_output 全量进内存可 OOM）+ 超时杀进程树。
- `tts_save` COM 成对释放（CoUninitialize + 流 Close 进 finally）+ Speak 文本截断。
- `send_email` 收件人 parseaddr 严格校验（防 CRLF 注入）；`create_evolution` 50MB 总上限 + 微秒时间戳防同秒合并。
- `_http_client` shutdown 加锁；`list_processes`/reader 的 deque 读写持 `_PROCESSES_LOCK`（防 "deque mutated during iteration"）。

**UI 与渲染**
- 右键「快速动作」子菜单泄漏：`_destroy_menu` 递归销毁 cascade 子菜单（Tk 不自动销毁子菜单），qa 不再加入 `_menus`。
- `_trim_context` O(n²) → 前缀和单遍裁剪（1M token 长会话）。
- 切思考档不再主线程全量 tiktoken（复用 `_ctx_counts`，无缓存时字符估算兜底）。
- 全局搜索跳转会话：补 `_ctx_counts=None`/`_snapshot_dirty=True` + 旧会话落盘。
- `save_session_to_file` 线程安全化（cfg 取值替代控件读取 + 消息浅拷贝快照 + 后台写盘）——切会话不再冻结 UI。
- `export_history` 后台线程导出（md/txt/html/jsonl 四文件 + Markdown 构建移出主线程），新增 `export_done` 队列消息与 `_show_export_done`；`build_markdown` 提炼为模块级纯函数 `_build_markdown(msgs, usage, start, cfg)`。
- `_scroll_to_message` 逐命中校验 `_msg_index_at`（多条消息同开头不再跳错）。
- `_on_fold_click` 数值键缓存 `_fold_nums`（与 `_fold_ranges` 同步维护，点击免重建 O(n) 列表）。
- cron 校验死代码 → `_cron_field_ok` 真实语法校验；`_save_draft` 唯一临时文件（防定时/退出并发截断）。

**辅助模块**
- stats：单条脏数据跳过不再丢弃整份统计；`pricing()` 深拷贝。
- tokens：非字符串内容防护。
- mdparse：有序列表统一渲染为 •；`_inline` 超长行（>4096 字符）快速路径防 O(n²)；渲染缓存体积账目修正。
- exporters：父目录自动创建 + 不可序列化消息跳过。
- splash：`fade_out` 对未构建进度条守卫；crypto 去重复导入。
- backup：微秒时间戳防同秒覆盖、compresslevel=1（CPU 省 2-3 倍）、异常清理半成品。
- taskpanel/processpanel：`_destroyed` 守卫（关闭后残留队列事件不再 TclError）；进程面板超长行按字符截断。

**测试**：新增 30+ 用例（SSRF/只读 SQL/run_python 黑名单/路径大小写/shlex 路径/相对路径锚定）；test_whitelist 的 FULL_AUTO 全局污染还原；TestDataTools 适配新的 FULL_AUTO 默认值；test_perf 等待循环加超时；pip_install 测试改 mock Popen。**全量 120 通过**。

### 1.10.9（长会话渲染分帧）——性能修复

- **根因**：Tk 对长 Text（数万行/几十万字符）的**映射与布局是一次性的**——隐藏窗口里 insert 很快，窗口首次映射时一次性布局全部内容（实测 601 条消息 5.7s），界面完全卡死。
- **方案**：`_render_all(paged=True)` 当 blocks > `PAGED_RENDER_THRESHOLD`(200) 时走分帧（`_render_blocks_paged` + `_paged_step`，每帧 `PAGED_RENDER_SIZE`(250) 块、间隔 `PAGED_RENDER_MS`(25)ms）。映射时 text 内容少 → 布局便宜（实测 5.7s → 0.57s）；后续每帧增量插入+布局，事件循环保持响应。
- **渲染逻辑拆分**：原 `_render_blocks` 的大 if/elif 提炼为 `_render_block(text, block, last_code_blocks, pos) -> pos`（单块渲染），`_render_blocks` 与分帧共用，杜绝逻辑漂移。
- ⚠️ **分帧一致性铁律**：分帧期间 text 处于"部分内容"中间态，**任何修改 text 内容的入口必须先行取消分帧**（`_cancel_paged_render()`），否则游标错位：
  - `_render_all`（开头，新渲染取代旧的）；`_append` / `_re_render_stream` / `_do_search`（cancel 后同步 `_render_all(paged=False)` 补全再继续，保证 text 与 blocks 一致）；`new_conversation` / `close_tab` / `on_close`（cancel 即可）。
  - `_re_render_stream` 的 TclError 兜底（`_render_all`）天然安全。
  - `paged=False` 强制同步渲染（分帧被打断后需要完整内容的场景）。
- **滚动条联动**：聊天区/会话列表滚动条用 `place` 叠加（relx=1.0, x=-10）而非 pack——pack 在 text 请求宽度（受最长行影响）超过列宽时会把后 pack 的滚动条压缩到 0 宽（长 URL/代码导致滚动条消失的根因）。
- 验证：601 条消息启动（映射 0.57s）、分帧中断插入一致性、76 项全过。

### 1.10.10（性能/UI/创意三线增强）

**性能**
- `mdparse.render_markdown` 内容级 LRU 缓存（`_RENDER_CACHE`，4096 条 / 64MB 双上限，>2MB 单条不入缓存）；`_inline` 纯文本行快速路径（`_INLINE_HINT_RE` 无标记即短路，纯文本行占绝大多数）。
- `DeepSeekClient.chat` 同轮多工具并行：`ThreadPoolExecutor(max_workers=min(4,n))` 并行执行非交互工具（ask_user/request_permission 保持串行——弹窗不能并发）；循环防护提前按顺序预判；结果按原始 tool_calls 顺序回传，`work.append` 顺序稳定。
- 快照惰性落盘：`_maybe_save_snapshot` 只调度 `after(10s)`（`_save_snapshot_now`），连续触发只写一次；`on_close` 立即写。长会话每轮不再反复全量序列化。
- `_atomic_json_write` 支持 `compact=True`（快照/会话文件分隔符压缩，体积减半）。
- `_on_fold_click` 用 `tag_prevrange` 快速定位 toggle 头（一次 Tcl 调用 + 线性匹配），替代逐卡片 compare。

**UI/修复**
- ⚠️ 修复隐藏 bug：折叠卡片插入用 `{style}_toggle`（`thinking_toggle`/`tool_toggle`），但样式/绑定注册的是 `think_toggle`——思考卡片头无样式且点击无效。统一为 `thinking_toggle` 并加下划线（可点击视觉）。
- 深色主题对比度：surface #1c1c1c / border #3a3a3a / code_bg #161616 / disabled #4a4d55 / selection #2058d8。
- 设置面板「高级参数」折叠（`_on_adv_toggle`，温度/top_p/JSON/Beta 收纳进 adv_frame）。
- 弹窗 ESC 关闭（`_dialog_shell` 统一 bind）；Ctrl+W 关会话、F1 帮助、Ctrl+K 命令面板。
- TaskPanel/ProcessPanel 增加 `apply_theme(t)`，`apply_theme` 末尾联动刷新。

**新功能**
- `generate_session_summary`：会话纪要（worker 线程非流式调用，`summary_done` 队列消息 → 写工作区 summaries/）。
- `show_session_timeline`：会话结构导航（消息时间线，双击 `_scroll_to_message` 定位）。
- `show_recipes`：配方管理（patterns.json 命名/执行注入）。
- `show_roles` / `apply_role`：角色库（`ROLES` 常量 7 角色，应用走与 edit_system_prompt 同款缓存警示）。
- `show_batch_task`：批量任务（多选文件 + {file} 模板 → 拼接提示词发送）。
- `show_command_palette`：Ctrl+K 命令面板（overrideredirect 无边框 + 过滤 + Enter 执行）。
- 菜单：工具菜单新增上述 6 项。
- ⚠️ 命令面板修复（用户反馈"点开就卡死"）：**无边框窗口禁用 `grab_set`**——overrideredirect + grab 会拦截主窗口全部事件、无可见关闭按钮，表现为程序卡死（Windows 上与输入法/焦点组合还可能锁死事件循环）。非模态 + Esc 关闭 + 再次 Ctrl+K 先销毁旧面板。
- ⚠️ 移除「上次未正常退出」启动弹窗（用户明确体验决策：任何情况下不弹窗）。`previous_run_crashed()` 仍被调用以维护 `.clean_exit` 标记状态（on_close 写、启动清），但不再消费返回值；快照恢复始终静默进行。README 同步更新。

## 17. 已知陷阱与修改清单（务必先读）

1. **不要并行运行 GUI 实例**；单实例锁 + splash 均由 `main()` 管理。
2. blocks ↔ Text 双写必须同步；改 messages 后调 `rebuild_view_from_messages()`。
3. 流式 content 块必须合并（§6），否则 md 解析按分块错乱。
4. mdparse 新增 span tag 必须同步到 `main._configure_tags`。
5. `place` 布局禁止混用 rel*/绝对尺寸。
6. 后台线程禁止直接操作 Tk；一律 `_ui_queue.put`（对话框内可用 `widget.after(0, ...)` 回主线程）。
7. `apply_theme` 后所有 tag 配置重建——动态样式逻辑必须挂在 `_configure_tags` 内。
8. `text.search` 未命中返回 `''`；`tag_ranges` 是扁平序列。
9. `end-1c` 才是文本真实末尾。
10. 段落换行：块内剥尾 `\n`、块间补 `("plain", "\n")`。
11. `last_code_blocks` 是**会话级**（`session["last_code_blocks"]`），复制代码取当前会话 `[-1]`。
12. 空响应/停止状态（`stop_event`）必须在 `_finish` 后检查并输出提示，`_pending_send` 延续机制不能丢。
13. 新增 UI 控件要注册 `_restyle`；**close_tab 必须从 `_restyle` 移除销毁控件**。
14. API Key 安全：config.json 含 key，禁止提交/分享；打包前清空。
15. `SCENARIOS`/`THINKING_MODES`/`EFFORT_BY_THINKING` 在 deepseek_client.py 顶部，改场景/思考档位时同步 `main.py` 的 `SCENARIO_DEFAULT_THINKING` 与 `setup_widgets_from_config`。
16. JSON hint 与 prefix 字段：hint 用对象身份过滤（`m is not json_hint`），**prefix 必须无条件清理**（§13）。
17. 统计走内存批量（`_pending_stats`/`_flush_stats`），**不要在 usage 回调里直接 `stats.record_usage`**。
18. 变体 seed：`_variant_seed_override` 不随 send 清除，需生成下一版时依赖其延续。
19. 隐私模式：快照/统计/日志/归档/历史文件**全部跳过**（`_archive_dropped`/`close_tab`/`new_conversation` 导出均已检查）。
20. `_ctx_counts` 在 send/continue_generation 时刷新，其他路径用 `tokens.message_token_counts` 兜底（`_context_over_limit` 内 None 时自算）。

### 1.10.12（全库加固与体验完善）

外部深度审查第二轮（30+ 项）实施：

**安全加固**
- `run_python` ast 深度检查（`_run_python_ast_blocked`）：修复正则黑名单的全部已知绕过——`from os import system` 别名导入、`importlib.import_module('sub'+'process')` 动态导入、`os['system']` / `getattr(os,'system')` 反射调用、写模式 `open`（`w/a/x/+` 字面量 mode）、`pathlib.Path().write_text/write_bytes`；`import ast` 补入模块顶部。完全智能模式仍整体放行（README 承诺不变）。附带把沙箱 `cwd` 锚定 `WORKSPACE_DIR`（写文件落点受限）。
- SSRF DNS 重绑定防护：`_is_private_host` 对非 IP 主机名执行 `socket.getaddrinfo`，任一解析结果落内网/回环即拦截（DNS 解析失败维持放行防误杀）。
- `image_understand` URL 图片改为 stream 边读边断（8MB 上限），不再全量 `resp.content` 进内存。
- 自定义工具 `_run_custom_tool` 的 endpoint 增加 `_safe_url` 校验。
- `create_evolution` 白名单移除 `.bat`（防提案与 run_command 联动写可执行文件）。
- Webhook 接收端 token 改 `hmac.compare_digest` 恒定时间比较 + 请求体 1MB 上限。

**线程/竞态**
- `self._messages_lock`（RLock）：`_trim_context` 整流程、`_compress_old_history` 的收集-删除-插入（摘要调用移出锁外，防 30s 阻塞主线程快照）、`_snapshot_assets`、`_finish` 的 msg_idx 回填、`rebuild_view_from_messages` 遍历均持锁。
- 弹窗超时残留：新增 `_watch_dialog_timeout`，审批/询问/白名单/计划四类弹窗在 worker 侧超时或停止后自动销毁。
- 快照并发写：`_snapshot_writing` 标志，上一轮写盘未完成时延后重试，防两个写盘线程覆盖同一文件。
- `_call_summary` 改内部线程 + 0.5s 切片轮询：停止生成或 30s 超时立即返回，不再阻塞 worker。
- `run_workflow` 检查-置位原子化（`_WORKFLOW_LOCK`），校验失败不再占位。
- `tts_save` 合成移入内部线程（60s 超时返回），`Speak()` 不再占住共享工具池。
- `show_context_details` 计数长度不一致自动重算 + 越界保护；`update_status` 整体 try（Tk 销毁期间静默失败）。

**健壮性/一致性**
- 工具失败前缀统一为 `deepseek_client.TOOL_RESULT_FAIL_PREFIXES`（main 6 处 + taskpanel 魔法字符串全部替换）。
- `notify_desktop`/`ocr_image` 的 PowerShell 占位符改 `@TITLE@`/`@BODY@`/`@PATH@`（用户内容含字面 `$body` 不再污染脚本）。
- 定时任务/Webhook 触发的 `send(silent=True)`：无 Key/预算拦截降级为状态栏+note，不再弹阻塞框。
- `manage_workflows`/`show_external_config` 全部改 `_atomic_json_write`（唯一临时文件，防并发互截）。
- `search_local` 去掉 `raise StopIteration` 控制流，统一 `_search_local_result` 出口。
- `speech_to_text` Whisper 模型按名缓存（large-v3 首次加载数十秒，复用后秒级）。

**体验完善**
- 输入框编辑器：Tab 4 空格缩进 / Shift+Tab 反缩进（多行选区）/ 括号引号自动配对（含选区包裹与配对退格）/ Ctrl+Backspace 删词。
- 聊天：右键「🔊 朗读此消息」（指定消息，非仅最后回复）；快速动作扩至 8 项（新增重构/代码审查）；F5 重新生成快捷键。

**验证**：新增 tests/test_fixes.py（24 项：ast 拦截/合法回归、DNS 重绑定、自定义工具 SSRF、.bat 拒绝、共享常量、search_local），全量 181 项通过。
### 1.10.13（68 个工具逐一精修）

对 deepseek_client.py 全部工具逐项审查（schema 对照实现 + 参数校验 + 边界 + 返回值质量），修复真实缺陷并完善：

**Bug 修复**
1. **send_email 多收件人**：`sendmail(from_addr, [to], ...)` 把逗号拼接串当单个收件人（对方收不到信）→ 传 `recipients` 列表。
2. **get_weather date 摆设**：date 参数此前只拼进显示文本、从未用于查询 → 传给 wttr.in `date=YYYY-MM-DD`（仅今天与近 3 天，schema 描述同步）。
3. **read_email SINCE 中文月份**：`strftime("%b")` 在中文系统输出"8月"，IMAP 服务器返回 BAD → 英文月份表 `d-MMM-yyyy`。
4. **environment_info pillow 漏检**：pip 包名 pillow 的导入名是 PIL，`find_spec("pillow")` 永远失败 → 包名→导入名映射（pillow→PIL 等）。
5. **read_file 按行模式超长行 OOM**：minified JSON/日志单行可达数百 MB，`f.readline()` 整行进内存 → `readline(102400)` + 截断标记。
6. **image_process 参数错误被笼统吞掉**：`crop=0,0`/`resize=abc` 抛 ValueError 走"处理失败" → 逐操作独立校验，报出具体操作与正确格式。
7. **write_csv/write_excel 混合行崩溃**：dict 行中混入标量 → 按空字典兜底。
8. **search_web 危险链接**：恶意站点可注入 `javascript:` 等 → 结果经 `_safe_url` 过滤（只留 http(s) 公网）。
9. **fetch_url GBK 乱码**：`iter_text` 按响应头解码，GBK 网页乱码 → bytes 下载 + charset 优先、utf-8→gb18030→latin-1 多编码兜底。
10. **PostgreSQL 慢查询占池**：只读查询无语句超时 → `options="-c statement_timeout=15000"`。
11. **run_command/start_process 无 cwd**：相对路径漂移到程序目录 → `WORKING_DIR`（main `_set_active_dir` 注入）或工作区作为 cwd。
12. **cron 无值域校验**：`99 99 * * *` 合法通过 → 分(0-59)/时(0-23)/日(1-31)/月(1-12)/周(1-7) 范围校验；main 的 `_cron_field_ok` 同步对齐。

**功能完善**
- read_csv：单列 100 字符截断（超长 JSON/URL 不撑爆上下文）；delimiter 必须单字符（支持 `\t` 转义）。
- read_excel / database_query / MySQL / PG 查询结果：单元格 100 字符截断。
- chart_data：非数值数据、NaN/inf、非法 kind、饼图全零/超 20 点 全部明确报错（替代笼统"生成失败"）。
- image_generate：size 白名单（256-2048 的 NxN）；URL 下载 20MB 流式限流（防写满磁盘）。
- image_process：操作计数（"3 项操作生效"）、未知操作提示、quality 独立解析。
- run_python：返回末尾注明沙箱工作目录与 with_site 状态（AI 知道文件落在哪）。
- write_code_project：大小校验统一按 UTF-8 字节（与 write_file 同规则，中文 3 字节/字）。
- verify_output：失败判定改用共享常量 TOOL_RESULT_FAIL_PREFIXES。
- pip_install：`--disable-pip-version-check`（静音版本提示）。
- Schema 类型修正：search_local.max_results / read_project_file.offset·limit / web_screenshot.width·height / tts_save.rate 由 number → integer。

**验证**：新增 tests/test_tools_polish.py（26 项：天气 date、收件人列表、CSV 截断与分隔符、GBK/UTF-8 解码、包映射、PG 超时、图片 size、chart 校验、cron 值域、命令 cwd、超长行截断、image_process 参数、搜索链接过滤），全量 207 项通过。
### 1.10.14（新工具需求文档落地：9 个新工具）

按鲸语自产 PRD（工作区 tools-requirements/鲸语_新工具需求文档.md）实现 9 个工具，全部遵循注册三步曲 + 可选依赖 + 权限模型：

**注册**
- TOOLS schema 追加 9 项（工具总数 68 → 77）；TOOL_CALL_MAP 同步。
- `config.json` enabled_tools 追加 9 名（默认启用，工具设置对话框自动渲染）。
- permissions.ACTION_TOOLS 追加 pdf_create / qrcode / media_ffmpeg / webdav（写入类走审批）。
- requirements.txt 追加可选依赖（PyMuPDF/reportlab/python-pptx/feedparser/qrcode/pyzbar/diskcache/imageio-ffmpeg）。
- main.py 注入 `RSS_SOURCES_FILE` / `KV_CACHE_DIR` / `WEBDAV_CONFIG_FILE`；deepseek_client 顶部 `import crypto`（WebDAV 凭据解密）。

**实现要点**
- `pdf_extract`：PyMuPDF；text/table/meta 三模式；`_parse_page_range` 页码范围（去重保序 + 越界拒绝）；加密 PDF 明确提示；扫描件（无文本层）提示改用 ocr_image；输出 60KB 截断。
- `pdf_create`：reportlab Platypus；复用项目自有 mdparse 解析 Markdown（标题/列表/代码块/表格）；`_register_cjk_font` 自动探测 msyh.ttc/simhei.ttf 并注册（TTC 用 subfontIndex，失败回退 Helvetica 不崩溃）；修复 Paragraph 换行丢失（\n → <br/>）；与 pdf_extract 闭环自测。
- `docx_read`：python-docx 按 element 顺序遍历（段落/表格交错不失序）；style 映射标题/列表；`.doc` 明确提示；max_chars 截断（下限 200）。
- `pptx_read`：python-pptx；占位符 idx==0 识别标题；要点逐行；表格转 Markdown；图片占位计数；备注开关。
- `rss_fetch`：feedparser；订阅持久化 rss_sources.json（SCHEDULES 同款 tmp+replace 原子写）；add/list/remove/fetch；`(link,title)` 去重；时间过滤（按 published/updated 倒序 break）；摘要 300 字截断；抓取 10s 超时。
- `qrcode`：qrcode[pil] 生成（size/纠错级）；pyzbar 识别多码；**Windows 缺 zbar DLL 时 import 即抛异常 → except Exception 统一降级提示**。
- `kv_store`：diskcache（TTL/线程安全/持久化）；set/get/delete/keys/search；value 1MB 上限；key 256 上限。
- `media_ffmpeg`：imageio-ffmpeg 自带二进制；`_ffmpeg_run` argv 直传禁 shell 拼接 + 超时杀进程树；time 正则白名单、width 16-7680、format 白名单；输入 2GB/输出 300s 限制；extract_audio 强制转码（copy 与容器不兼容）。
- `webdav`：httpx 原生 PROPFIND/GET/PUT/DELETE；配置 webdav_config.json（password 支持明文与 dpapi: 密文，crypto.decrypt 兼容）；PROPFIND XML 解析（displayname/大小/时间/目录）；upload 大小校验、download 白名单写。

**验证**：新增 tests/test_tools_docs.py（42 项：PDF 闭环含中文、页码范围、加密、docx 结构/截断/.doc 提示、pptx 备注开关、RSS 全链路与无效源、二维码生成/降级、KV 全动作+TTL+1MB 上限、ffmpeg 真实视频 info/截图/提音频/参数白名单、WebDAV 列表解析/上传下载/删除/未配置），全量 249 项通过。
### 1.10.14 补充（新工具检查/修复/真实核验）

对新工具全量检查与真实环境核验（self_test_new_tools.py，35 项全过）后的修复：

1. **rss_fetch 兼容性（真实核验发现）**：新版 feedparser 6.0.12 的 `parse()` 已移除 `timeout` 关键字（旧版支持）→ 改为内部线程 + `join(RSS_FETCH_TIMEOUT)` 实现可靠超时（兼容所有版本），线程内 `_parse` 捕获异常回传。
2. **rss_fetch `since_hours=0` 语义 bug**：`int(since_hours or 24)` 把 0（=全部）误吞成 24 → 改为 `since_hours not in (None, "")` 判空。
3. **rss_fetch 乱序源**：时间过滤由 `break` 改 `continue`（feedparser 不保证条目时间排序，break 会丢条目）。
4. **pptx_read 组合形状**：GROUP 形状内的文本框/图片此前被忽略 → `_walk_shapes` 递归遍历（深度上限 10 防畸形文件死循环）。
5. **pdf_extract 表格模式版本防护**：`find_tables` 需 PyMuPDF 1.23+ → `hasattr` 检测，旧版返回可操作升级提示。
6. **pdf_create 默认标题**（PRD 规范）：title 为空时取内容首行作为 PDF 元数据标题。
7. **webdav 大小限制**：upload/download 单文件 200MB 上限（防全量进内存）。
8. **TOOL_GROUPS 分组**：9 个新工具归入 信息检索/文件管理/数据处理/文档创作/媒体感知 分组（不再落"其他"）。

**真实核验（非 mock）**：PDF 闭环（中文生成→文本/表格/元数据/页码提取）、docx 结构、pptx（含组合形状与备注开关）、RSS 真实抓取阮一峰源（标题/链接/摘要/去重）、二维码生成 PNG 魔数校验、KV 持久化+TTL 过期、ffmpeg 真实视频（info/截图/转音频/转码 webm）、webdav 配置缺失提示、可选依赖降级（fake import 缺 PyMuPDF）——35 项全过；核验脚本同步至工作区 tools-requirements/self-test/。
### 1.10.15（公众号自动写作 WeChat Writer）

按鲸语自产方案（workspace/code-review/wechat_writer_公众号自动写作工具方案.md）实现：

**新增独立包 `wechat_writer/`**（零 GUI 依赖，标准库 + httpx + feedparser + markdown；LLM 走 DeepSeek API）：
- `config.py`：默认配置（schedule 0 9 * * * / 风格 / 信源 / 质检参数）+ 用户配置合并 + 值域钳制。
- `llm.py`：读鲸语 config.json 并 **DPAPI 解密 api_key**（crypto.decrypt，明文兼容）；chat 3 次重试指数退避；chat_json 提取 JSON 块。
- `sources.py`：RSS 并发采集（线程池 ≤8，单源失败跳过；`since_hours` 过滤；feedparser 6.x 兼容——内部线程 + join 超时）；Bing 搜索兜底（RSS <3 条时）；全文抓取（多编码解码 + 去噪标签）；统一 Item。
- `topic.py`：LLM 提炼 3-5 候选 → **双通道去重**（通道 A：bigram Jaccard >0.70 粗筛零成本；通道 B：LLM 精判"换汤不换药"，解决同义改写漏网）→ 评分（新鲜度+素材覆盖度）→ 全部剔除降级"今日 AI 资讯盘点"。
- `writer.py`：大纲（标题×3/导语/小节/结语 JSON）→ 正文（素材事实约束 + 公众号文风 + 参考资料）→ 润色（错别字/字数压缩）三阶段；rewrite_fix 按质检原因重写。
- `quality.py`：字数（[min, max*1.3]）/ 来源标注 / 敏感词 / 完整性（标题+≥2 小节）/ 双通道查重，返回 QualityReport（passed/reasons/score）。
- `history.py`：history.json 索引（date/topic/title/keywords/path），recent(n)/topics/titles。
- `output.py`：草稿箱（`# 标题\n\n正文`，与 publish_draft 兼容）+ 存档 articles/YYYY/MM/DD.md（含元数据头）+ HTML（markdown 库渲染）；dry_run 零文件。
- `main.py`：run_once 编排（失败任何一步不写草稿不记历史）+ CLI（`python -m wechat_writer --dry-run/--run/--topic`，默认 dry-run 安全）。

**注册**：TOOLS schema（run_wechat_writer，dry_run/topic 参数）+ TOOL_CALL_MAP + config.json enabled_tools + TOOL_GROUPS「文档创作」。定时触发复用现有 schedule_task（cron `0 9 * * *` + action=message）。

**真实核验（真实网络 + 真实 LLM，约 7 次调用/轮）**：
- 采集：5 个默认信源 0.6s 取 15 条（IT之家等可达）。
- `--dry-run`：选题"任天堂 Switch 2 中国版发布"→ 初稿 1743 字 → 质检拦截（缺参考资料）→ 自动重写 → 通过（score 100，1867 字），零落盘。
- `--run`：选题自动换题（历史查重生效）"谷歌 3.5 Pro 发布"→ 1849 字 → 草稿/存档/HTML 落盘 + history.json 记录；草稿格式与 publish_draft 兼容、文末含参考资料。

**顺手修复**：knowledge_index 增量复用误判（秒级 st_mtime 在快速改写 + size 相同时漏更新 → 改 st_mtime_ns，旧索引缺字段自动重索引）。

**验证**：tests/test_wechat_writer.py（29 项：config 默认/损坏/钳制、RSS 空/单源失败/解析、全文失败降级、选题降级/粗筛剔除/**同义改写 LLM 精判回归**、质检五关、writer 三阶段/重写、output dry_run/格式、history、run_once 成功/失败/落盘、工具注册与封装），全量 278 项通过。
### 1.10.16（WeChat Writer 信源扩充与素材深度）

用户需求：更多信源、写得更有深度。

**信源扩充（实测可达性筛选，36氪/虎嗅/V2EX/arXiv 国内不可达被排除）**
- 信源按组组织（config.py `sources.rss_groups`，默认 9 源）：
  - `ai_media`：机器之心 / 量子位 / InfoQ / 雷锋网（AI 垂直首选）
  - `tech`：IT之家 / 开源中国 / Solidot（科技与开发者综合）
  - `life_tech`：少数派（效率工具与数字生活）
  - `dev_global`：Hacker News（英文国际视角）
- 用户可用 `rss` 列表直接覆盖，或用 `rss_groups` 自定义组（`expand_rss` 展开）。

**素材纯净度：主题相关性过滤**
- `sources.topic_keywords` 默认 60+ 词：命中标题/摘要任一关键词才保留；英文纯字母词用词边界（`\bai\b` 不误中 "said"/"email"），中文包含匹配。
- 过滤后不足 5 条时放行全部（宁多勿缺，防误杀边缘价值内容）。
- 真实效果：48 条原始 → 26 条相关（54%），IT之家手机/游戏/显示器新闻被大量过滤。
- 教训：首版关键词含"发布/评测/大厂/科技公司"等宽泛词导致过滤失效（手机新品带"发布"全部漏进），已移除并加注释警示。

**深度写作**
- `fetch_full_text` 默认开启（全文上限 30KB，6 并发）；writer 的 `_material_block` 对已抓全文素材附加"全文节选"（前 1500 字）——LLM 可引用正文细节而非只有标题摘要。
- 真实 dry-run 验证：选题"Claude 文本水印嵌入不可见水印"（全文素材支撑）、标题"复制粘贴也洗不掉？每一条 Claude 文本都藏着看不见的指纹"、2340 字一次通过质检。

**顺手修复（真实事故）**：`sources.collect_rss` 单源抓取无超时——feedparser 的 urllib 请求本身无超时，慢源/DNS 挂起会卡死整轮采集（探测脚本 5 分钟超时暴露）。改为内部线程 + join(timeout)（与 rss_fetch 工具同款），单源 10s 兜底。

**验证**：新增 6 项单测（挂起源超时回归、关键词过滤/小语料放行/词边界、rss_groups 展开/用户覆盖、钳制上限），全量 284 项通过。
### 1.10.17（产物可见性：产物条 + 📂 文件面板）

用户需求：工具生成的文件要能直接打开，要有独立明显的打开区域，不能去代码目录翻找。

**📦 产物条（输入框上方，最明显）**
- `_input_area` 输入卡片顶部新增 `recent_bar`：`📦 最近产物：<文件名>` + [打开][所在文件夹][复制路径][✕]，默认隐藏。
- `_record_recent_output` 提取到真实存在路径后自动 `_update_recent_bar()` 显示（工具结果出现即更新为最新产物）。
- 方法：`_update_recent_bar` / `_hide_recent_bar` / `_open_recent_bar` / `_open_recent_bar_dir` / `_copy_recent_bar` / `_recent_bar_path`。
- 注意 pack 顺序：`before=self.input_text` 保证产物条在输入框上方（后 pack 默认在下方）。

**📂 文件面板（右侧面板双 Tab）**
- `_side_panel` 头部改双 Tab：`⚙ 设置` / `📂 文件`；`_switch_side_tab` 切换 body 并自动加宽面板（252 → 430px，用 `configure(width=)`——pack_propagate(False) 下生效；实测 `pack_info()` 不返回 width 键）。
- `_build_files_panel`：ttk.Treeview（Files.Treeview 样式注册于 `_apply_ttk_styles`，主题适配）+ 独立滚动条样式。
- 根节点：📁 工作区 / 📄 草稿箱 / ⭐ 最近产物 / 📁 数据目录；目录懒加载（占位节点 `…` → `_on_files_open` 展开时 `_fill_files_dir` 填充，跳过 `.`/`__pycache__`/`.venv`/`node_modules`，目录优先排序，单目录 ≤300 项）。
- 最近产物节点懒加载真实存在的 20 个文件（recent 节点 path=None 也插占位——首版漏插导致展开无内容，已修）。
- 双击：文件打开 / 目录展开收起；右键菜单：打开 / 所在文件夹 / 复制路径 / 注入输入框（`_inject_file_to_input` 读文件入输入框）/ 查看全部最近产物 / 刷新；`_files_entry_path` 沿父链递归解析真实路径。

**草稿目录统一**
- `run_wechat_writer` 工具调用 `run_once(drafts_dir=WORKSPACE_DIR/drafts, archive_dir=WORKSPACE_DIR/wechat_articles)`——公众号草稿与 publish_draft 同目录，产物面板/草稿箱直达。
- wechat_writer `run_once` 新增 drafts_dir/archive_dir 参数（None 走包内默认，CLI 独立运行不受影响）。

**验证**：新增 tests/test_ui_products.py（13 项，真实 Tk 实例：产物条默认隐藏/记录即显示/更新最新/隐藏/复制/无效路径忽略；Tab 默认 settings/切换 files/宽度 430↔252/根节点/工作区懒加载/recent 节点填充/路径解析），全量 297 项通过。
### 1.11.0（UI 定版大版本：从 demo 走向正式产品）

用户需求：最终定版前最后一次大版本设计——多轮迭代导致初始设计意图偏离，需要整体规范化。

**品牌一致性（demo 感最大来源）**
- 导出 MD 头 `# DeepSeek Assistant 会话记录`（旧品牌残留）→ `# 鲸语 WhaleTalk 会话记录`（`_build_markdown`）。
- 窗口标题 `鲸语 WhaleTalk · AI 对话助手` → `鲸语 WhaleTalk v1.11.0`（带版本号）。
- `show_about`：messagebox → `_dialog_shell` 品牌对话框（🐋 大标题 + 版本 + 能力一览 + 独立产品声明）。
- `show_help`：messagebox → 结构化速查表（操作 | 说明 双列排版）。
- 欢迎页文案更新（90+ 工具/产物面板/预算控制等现版本能力）。

**菜单栏重构（工具菜单 30 项乱堆 → 分组化）**
- 顶级菜单由 5 个扩为 6 个：新增「视图(V)」（切换主题/增大减小字号/Markdown 渲染/设置面板显隐/会话列表显隐/主动建议开关——原"设置"菜单的外观相关项全部迁入）。
- 工具(T) 按 6 组功能分区（add_separator 分组）：账户与用量 / 任务与模板 / 能力管理 / 数据与文件 / 自我进化 / 系统；「试玩任务（一键体验）」更名为「示例任务（一键体验）」；「朗读最后回复」从编辑菜单迁入任务组。
- 设置(S) 收敛：系统提示词 / 完成通知 / 项目上下文 / 隐私模式 / 保存配置（提示词库管理迁至工具→能力管理，主动建议迁至视图）。
- 新增 `toggle_sidebar()`：视图菜单可显隐左侧会话列表（`_manual_hidden["sidebar"]` 防紧凑模式干扰）。

**状态栏三段式信息分级**
- 左段（status_label）：模式标识 + 工作目录 + 本轮/累计统计 + 预算 + 高峰。
- 右段（新增 `status_right`）：模型 · 场景 · 思考档位——从单行超长文本拆出，信息密度降低、可读性提升。
- `_update_status_inner` 同步重写（左右两 label 分别 configure，flash 代数防回退逻辑不变）。

**侧栏与字号规范**
- 侧栏标题「会话」→「对话」；会话计数 label 8→9pt；底部按钮文案统一（查余额→余额、⚙ 设置/＋ 新建 9pt）。

**验证**：全量 299 项通过（test_ui_products 真实 Tk 实例覆盖菜单/状态栏/面板构建无回归）。
### 1.11.0 补充（定版清单补全：此前跳过的项全部落地）

上一轮定版遗漏项逐一补齐：

1. **splash.py 品牌化**：副标题「为 DeepSeek API 深度优化 · 极致对话体验」→「深海蓝鲸 · 专业桌面 AI 工作台」；加载文案「正在启动 DeepSeek 引擎」→「正在启动鲸语引擎」（品牌残留清除）。
2. **主题 token 定版**（THEMES 两套主题各新增 5 token）：
   - `hover`（菜单/列表悬停，浅色带品牌蓝倾向 #e6ecf7 / 深色 #232323）——13 处 `activebackground=t["surface"]` 全部接入 `t.get("hover", t["surface"])`；
   - `note`（时间戳专用，`_configure_tags` 的 time tag 由 thinking 色改为 note）；
   - `quote_bg`（引用块背景 #f2f6fd/#101418，替代 surface）；
   - `input_placeholder`（输入框占位符颜色，`_set_placeholder` 接入）。
3. **字号规范**：`fsz=8` 20 处 + `FONT_FAMILY, 8)` 53 处 + taskpanel/processpanel 4 处 → 全量 9pt（最小可读字号，消除 demo 感小字）。
4. **菜单 Alt 快捷键**：`Alt+F/E/V/T/S/H` 打开对应菜单（`_open_menu_alt`，按钮索引映射），正式桌面应用惯例。
5. **品牌对话框扩容**（messagebox → `_dialog_shell`）：
   - `_show_balance`：余额卡片式展示（总余额大字 + 赠送/充值明细 + 状态色）；
   - `show_stats`：今日/本月/累计分区 + 缓存节省高亮 + 各模型明细；
   - `_show_export_done`：导出成功 ✅ + 路径明细（失败仍用 showerror）；
   - 确认类（askyesno：更新/删除/清空等）保留系统对话框（模态语义正确）。
6. **设置面板**：外观组补「字号」标签（裸下拉框缺说明）。
7. 冒烟验证（真实 Tk 实例）：6 顶级菜单按钮、状态栏右段（模型·场景·思考）、主题往返切换、余额/统计/帮助/关于对话框构建、导出完成对话框——全部通过。

**验证**：全量 299 项通过（test_ui_products 覆盖核心 UI 构建无回归）。
### 1.11.0 布局定版（Layout Specification v1.0）

用户核心诉求：各模块长宽占比无规划、demo 感强。建立尺寸常量系统并全量实施：

**① LAYOUT 常量系统**（main.py 顶部，禁止散落魔法数字）：
窗口 1280x820 / minsize 880x620；菜单 34px；状态栏 30px；侧栏 260(200-420)；面板 280(240-480)；文件视图 460；内容列 560-860（margin 120）；紧凑阈值 ≤1120 收侧栏 / ≤1000 收面板；对话框三档 420/520/640 + 高档 300/420/460/540/620。

**② 窗口几何记忆**：config 新增 `window_geometry`；on_close 保存、build_ui 恢复（正则校验 + 屏幕内校验 + minsize 校验，分辨率变化后不跑丢）。

**③ 核心修复：输入区与聊天内容列对齐**：
- 根因：输入区 `padx=28` 横跨整个聊天区，而聊天列 `place` 居中 —— 1280 窗口下输入 798px vs 聊天列 702px，错位 96px。
- 修复：`_layout_all` 计算列宽后调用 `_layout_input(tw)` 同步输入 padx；padx 下限 4（此前 16 会在 tw=574 时产生 18px 错位）；列宽物理让步 `min(cw, max(360, tw-40))` 防窄聊天区列越界（真实冒烟发现 454px 容器放 560px 列被截断）。
- 冒烟验证四档：1280/1120/1000/880 全部 `|col-inp|≤2` 且 col≤chat。

**④ 紧凑模式重校准**：阈值由 <900/<700 改为 ≤1120（侧栏）/≤1000（面板），且 minsize 880 < 面板阈值——任意可达窗口尺寸紧凑逻辑都可触发；语义修正（边界值 1000/1120 也收）。

**⑤ 对话框三档规范化**：`_dialog_shell` 内建 `_DIALOG_W_SNAP`/`_DIALOG_H_SNAP` 就近吸附——26 个既有对话框（原 10 种宽 × 12 种高）全部归一，未来新增自动对齐。

**⑥ 状态栏**：context 条 140→120px、label 34→32（右段不挤压）。

**验证**：新增 tests/test_ui_layout.py（13 项：LAYOUT 常量完整/有序、窗口几何记忆、输入对齐 padx 一致性、对话框档位吸附（min 逻辑 + 真实 Toplevel geometry）、紧凑阈值关系、minsize<紧凑阈值）；全量 312 项通过；四档真机冒烟全部对齐。