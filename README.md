# 鲸语 WhaleTalk · AI 对话助手

基于 DeepSeek V4 API 深度优化的 Windows 桌面 AI 对话助手，充分发挥 V4 的 Agent 能力、1M 上下文与性价比优势。**鲸语是独立产品品牌，与 DeepSeek 官方无任何关联**。

## 品牌

- 中文名：鲸语（寓意"深海鲸歌对话"），英文名：WhaleTalk
- 品牌视觉：深海蓝渐变 + 蓝鲸徽标（启动界面、应用图标同一视觉家族）
- 技术底座：DeepSeek V4 API（官方公开接口），鲸语只负责做深做透对话体验

## 功能特性

- 商用级三栏布局：左侧会话列表 + 中间聊天区 + 右侧可折叠设置面板（左栏「⚙ 设置」按钮收放）
- 自定义菜单栏：文件 / 编辑 / 工具 / 设置 / 帮助（纯色按钮式菜单栏，深色/浅色主题完整适配）
- 主题系统：浅色 / 纯黑两套配色（深色为纯正黑色 #000000），标题栏跟随主题（Win11 或系统深色模式下自动变黑）
- 输入框高度可拖拽：聊天区与输入框之间的分隔条上下拖动（悬停高亮，双击恢复默认），或 Ctrl+↑/↓ 键盘调整，随配置保存
- 聊天区宽度可拖拽：侧栏与会话区之间的分隔条左右拖动（双击恢复默认），宽度随配置保存；聊天记录区细窄滚动条（无箭头、主题适配、可拖动，悬停/拖动时加深）
- 会话列表管理：新建 / 双击重命名 / 右键菜单 / 按名称或首条消息搜索，自动按首条消息命名
- 流式输出：思考过程与最终回答实时显示（40ms 批量渲染，避免高频 UI 更新）
- **流式 Markdown 渲染**：生成过程中粗体/行内代码/链接/代码围栏即时渲染（跨 chunk 的未闭合标记自动暂缓，生成结束强制兜底；关闭 md_render 时回退纯文本）
- 自定义模型：支持任意 OpenAI 兼容模型名与端点（Profile 自定义 base_url + 模型，不再强制回退内置列表）
- 会话导入：文件菜单「导入会话」支持 JSON 数组 / 含 messages 的对象 / JSONL 三种格式
- 快速动作：聊天区右键「⚡ 快速动作」一键对消息发起解释代码 / 总结要点 / 中英互译 / 润色 / 生成单元测试
- 高性能长对话：思考/工具在流式期间即渲染为折叠卡片，生成结束零全量重渲染——每轮耗时与对话长度无关（实测 40 轮会话单轮结束 <0.5s，150 轮全量刷新 <0.15s）
- 思考过程与工具调用可折叠卡片：流式实时折叠结构，点击标题展开/收起
- 思考模式选择器：禁用 (none) / 低思考 (low) / 高思考 (high) / 最大思考 (max) / 极高思考 (xhigh)
- JSON 输出开关：response_format 结构化输出，自动注入 JSON 指令且不污染会话上下文
- Beta API 开关：开启后使用 /beta 端点，启用对话前缀续写与 FIM 补全
- 继续生成：停止/回答后可从当前回复末尾续写（Beta 前缀续写，编辑菜单或聊天区右键）
- FIM 代码补全：前缀+后缀中间补全（工具菜单，最大 4K）
- 回复变体：同一问题以不同 seed 多次生成，保存/浏览/恢复任意版本
- 引用回复：聊天区右键「引用此消息回复」，原文带引用插入输入框
- 峰谷定价感知：状态栏实时显示高峰时段（9-12 / 14-18 价格 2 倍），每日首次发送前提示一次
- 缓存警示：修改系统提示词时提示将破坏前缀缓存（命中 0.02 元 vs 未命中 1 元/百万）
- 输入框实时 token 估算：输入时显示「约 X token」
- 输入历史：Alt+↑/↓ 浏览当前会话已发送的输入（首次按↑自动保存草稿，回到草稿处可继续编辑）
- Ctrl+Enter 快速发送；Ctrl+Shift+V 将剪贴板 URL 粘贴为 Markdown 链接（非 URL 时放行普通粘贴）
- 会话导出扩展：导出历史同时生成 MD / TXT / HTML / JSONL 四种格式
- Profile 多账号：工具菜单「Profile 管理」保存多组 API Key / Base URL / 模型，一键切换
- 长会话惰性折叠：早期消息自动折叠为一行提示（config 中 fold_early_threshold 开启，默认 0 关闭）
- 自动更新增强：发现新版本时先自动备份当前源码再前往下载
- 思考流式动画：模型思考期间状态栏显示「🤔 思考中…」
- 会话置顶：右键会话列表置顶/取消，列表优先展示，随快照与历史库持久化
- 收藏跳转：收藏列表中双击或「跳转」定位到对应消息
- 流式智能跟随：生成中上翻历史时不再被强制拉回底部
- 场景快速切换：通用 / 编程 / Agent / 自定义（自定义温度与 top_p 采样参数）
- 多模型：deepseek-v4-flash / deepseek-v4-pro，上下文进度条跟随模型窗口
- 工具调用（Agent 模式）：内置工具，自动多轮调用并回传 reasoning_content；参数解析失败时自动把错误回传模型自主修正
  - 信息类：get_date（日期+时间+时区）/ get_weather / search_web（联网搜索，Bing 主源 + DuckDuckGo 兜底）/ fetch_url（抓取全文）
  - 交互与记忆：ask_user（Agent 主动询问用户，弹窗等待回答）/ request_permission（权限被拒时请求一键加入白名单，弹窗同意/拒绝）/ read_memory / write_memory（长期记忆，与手动维护的记忆库同文件）
  - 执行类：run_python（沙箱执行，支持 with_site 加载已装第三方库）/ read_file（支持按行读取）/ write_file / edit_file 等
  - 数据类：database_query（SQLite 只读查询，SELECT/PRAGMA，路径白名单）
  - 高风险（默认不启用，需权限设置）：send_email（需配置 email_config.json）/ pip_install（完全体模式全开放行，白名单可配置）
  - "工具设置"对话框可单独启停每个工具，未选中的工具不会出现在请求中
- 智能上下文管理：
  - 字符数 + Token 数双阈值（tiktoken 精确估算，1.5 字符/token 回退）
  - 超限时自动用 LLM 压缩旧轮次为摘要（失败回退硬裁剪），保留最近轮次（min_kept_turns）
- 多会话管理：左侧列表新建/关闭/双击重命名会话，各会话独立消息、独立 Token 统计、互不干扰
- 对话内搜索：Ctrl+F 打开搜索栏，实时高亮全部匹配，Enter/Shift+Enter 循环上/下一个
- 字号调节：工具栏字号下拉（8-18），对所有会话与输入框即时生效，随配置保存
- 上下文占用进度条：实时估算当前消息占 1M 窗口的 token 比例
- 发送即打断：生成中发送新消息，自动停止当前生成并在结束后立即发送
- 编辑重发 / 重新生成：修改上一条提问重新发送，或重跑上一条回复
- 会话快照：关闭后自动保存，下次启动自动恢复上次会话（可配置 restore_session）
- 空响应自愈：模型返回空内容时自动重试一次
- 余额查询：官方 /user/balance 接口，工具栏"查余额"按钮；余额不足(402)时自动提示
- 用量统计：按天/模型自动累计 token 与预估费用（工具菜单「用量统计」），缓存命中占比状态栏实时着色提示
- 预算控制：设置月预算，状态栏实时显示本月费用，接近上限变黄/超限变红，可开启超限阻止发送
- 工具调用防护：可配置调用轮数上限（max_tool_rounds），同一工具相同参数连续调用 3 次自动终止并提示；工具卡片显示执行耗时
- 自定义工具 SDK：工具菜单「自定义工具」注册自己的 HTTP 工具（name/描述/endpoint/参数），Agent 自动调用
- **智能体行动层（默认全部关闭）**：工具菜单「权限设置」开启后可用，白名单 + 审批流 + 审计日志：
  - L1 文件操作：write_file（原子写 + 自动 .bak）/ edit_file（文本/正则替换）/ list_dir（只读）
  - L1 终端：run_command（命令白名单 python/pip/pytest/git，禁止 shell 拼接）
  - L2 检索：search_local（允许目录内文本内容检索，只读）
  - L3 文档：create_doc（.md/.html 原生，.docx 需 python-docx）
  - L3 代码工程：write_code_project（一次创建多文件工程，逐文件原子写 + 路径越界防护）
  - L2 浏览器：browser_navigate（open/click/type/get_text）/ web_screenshot（截图到工作区），需安装 playwright（可选）
  - L4 草稿：publish_draft（保存到本地草稿箱，只建草稿不发布，发布权始终在用户手中）
  - 权限模型 permissions.json：allow_write / allow_run_command 默认 false，目录白名单 + 系统目录阻止列表，路径 resolve 防穿越；审批模式 auto / confirm（弹窗确认）/ deny；所有行动写审计日志 actions.log（隐私模式跳过）
- Agent 任务模板：写代码并运行 / 网页调研 / 数据分析 / 创建代码工程 / 生成本地文档 / 执行项目测试（一键注入）
- 弱网提示：生成中 10 秒无响应时状态栏显示等待时长
- 历史会话库：所有会话按需落盘（sessions/ 目录），启动只恢复最近会话，其余通过「历史会话库」懒加载，支持全局搜索；支持多选（Ctrl/Shift/框选）与「全选/取消」批量删除
- 上下文压缩归档：被压缩/裁剪的旧轮次自动归档为本地 Markdown，可随时找回；右键"固定此消息"可让关键内容保留进摘要
- 临时会话：文件菜单「新建临时会话」，关闭后彻底清除不落盘
- 紧凑模式：窗口 <900px 自动隐藏侧栏，<700px 自动收起设置面板（防抖处理）
- 提示词库：预置 8 个模板（翻译/代码审查/测试/润色等），输入区「⚡ 指令」一键插入，支持 {{TEXT}} 占位；输入时自动推荐匹配模板
- 会话标签：右键会话设置标签（#标签 过滤），会话列表显示标签前缀
- 消息收藏：聊天区右键收藏/取消收藏，编辑菜单「查看收藏」集中查看
- 分支对话：从任意消息分叉为新会话，原会话保持不变
- 拖拽文件：直接拖文件到输入框自动附加内容（需 tkinterdnd2，可选）
- 输入区 Markdown 快捷输入：Ctrl+B 加粗 / Ctrl+I 斜体 / Ctrl+K 链接
- 回复朗读：Windows TTS 朗读最后回复（需 pywin32，可选）
- 首次启动引导向导：欢迎页 + API Key 配置
- 浏览器可见模式：设置面板「🖥 浏览器可见（有头预览）」一键切换——开启后 AI 操作浏览器（browser_navigate/web_screenshot）会弹出真实窗口，可实时观看操作过程；关闭为无头静默运行
- 隐私模式：设置菜单开启后不保存快照/统计/日志，状态栏显示 🔒
- 崩溃兜底：异常退出后启动时自动恢复最近会话快照（静默恢复，无弹窗打扰）
- 单实例锁：重复启动自动提示，应用图标 app.ico 自动生成
- 自动重试：限流（429）与网络错误自动重试 3 次（指数退避）
- 可配置请求超时（timeout，连接 10s / 读取可配）
- 实时 Token 统计：本轮输入/输出、缓存命中/未命中、会话累计
- 对话历史导出：Markdown / TXT 双格式
- 一键复制回复、复制代码块、重置会话、自定义系统提示词
- 配置校验与归一化（非法值自动回退默认值），日志自动轮转（10MB x 5）
- 配置持久化至 config.json（**API Key 以 Windows DPAPI 加密存储**，日志与历史保存在 `%USERPROFILE%\Documents\WhaleTalk\`（首次运行旧版本数据目录 DeepSeek_Assistant 会自动迁移）
- 启动界面：深海蓝鲸主题（鲸语品牌视觉）
- 输入草稿持久化：未发送的输入自动保存，重启后恢复（隐私模式跳过）
- 剪贴板即问：Ctrl+Shift+Q 一键把剪贴板内容放入输入框
- 数据清理：工具菜单「数据清理」勾选即清（历史/统计/日志/工作区/记忆等）
- 任务计划确认：每轮工具调用前先弹窗确认整轮计划（权限设置可开）
- 工作区文件树：工具菜单浏览 AI 产物，双击文件注入对话，右键复制路径，「打开 / 打开所在文件夹」一键直达
- **📂 产物面板**：右侧面板双 Tab（⚙ 设置 / 📂 文件）——文件视图树形浏览工作区/草稿箱/最近产物/数据目录（懒加载，双击打开文件或展开目录，右键打开/所在文件夹/复制路径/注入输入框/刷新），自动加宽面板
- **📦 产物条**：输入框上方常驻显示最近生成的产物（打开 / 所在文件夹 / 复制路径 / 关闭），工具产出即出现，无需到目录翻找
- 最近产物：工具菜单「最近产物」列出 AI 最近创建/修改的文件（前 20 条），双击或按钮直接打开、打开所在文件夹、复制路径
- 路径一键打开：聊天区工具卡片中的本地路径自动变为可点击（下划线），点击直接打开文件或所在目录
- 长期记忆：手动维护的事实/偏好库，注入每次请求（可开关）
- 完成通知：生成结束后任务栏闪烁 + 提示音（可配置）
- 定时任务：到点自动发送指令（工具菜单「定时任务」）
- 定时/周期调度（cron）：支持标准 5 字段 cron 表达式、每 N 分钟周期、三种动作（发指令 / 项目备份 / 状态提醒+推送）
- 持续记忆知识图谱：记忆支持类型/实体/关系三元组，语义相似度检索（TF-IDF + bigram，不含关键词也能匹配），图谱查询工具
- 多模态：文本转语音保存 WAV（tts_save）、图像处理（缩放/裁剪/旋转/水印，image_process）、图片文件 OCR（ocr_image）
- 数据工具：CSV/Excel 读写（read_csv/write_csv/read_excel/write_excel）、数据可视化图表（chart_data，matplotlib）、MySQL/PostgreSQL 只读查询（database_query_mysql/postgres，db_config.json 配置）
- Webhook 推送：钉钉/ServerChan/Slack/通用通道（send_webhook，webhooks.json 配置），定时任务可联动提醒
- 并行子代理：subagent_run 把大任务拆给多个并发 AI 子代理（1-4 并行）汇总结果
- 自我验证闭环：run_tests（自动发现并运行 pytest/unittest）+ verify_output（对照标准答案计算 F1/覆盖率/缺失要点）
- 浏览器自动化增强：实例复用（连续操作共享页面与登录态，browser_profile 持久化）、表单动作（fill/submit/select）、多步操作不重复导航
- 沙箱自由执行：run_python(with_site=true) 加载第三方库并访问外网，pip_install 完全体放行任意包
- 语音输入：🎤 按钮用系统语音识别听写（Windows 语音识别）
- 剪贴板 OCR：工具菜单「从剪贴板图片提取文字」（Windows OCR）
- **场景包**：设置面板一键启用「办公 / 开发 / 创作」——自动配好权限、工具、提示词与思考档位，替代手动配置
- **试玩任务库**：工具菜单 10 个一键试玩任务（写周报/建网站/翻译/检索/测试…），30 秒理解 AI 全流程
- **项目上下文**：开启后自动把工作区内容概览注入每次请求（可开关，60 秒缓存）
- **任务报告**：工具链执行结束自动生成摘要（调用次数/耗时/token）
- **一键回滚**：最近产物「还原 .bak」把 AI 改过的文件还原到改动前
- **省钱报告**：用量统计显示缓存命中节省的估算金额
- **反馈收集**：消息右键 👍/👎 记录偏好，编辑菜单「反馈记录」查看
- **对话分享**：编辑菜单「复制分享文本」一键复制 Markdown 分享
- **🤖 完全智能模式**：设置面板「自主模式」一键开启——允许目录内的写文件 / 运行命令 / 工具链全部自动执行，不再弹任何审批，AI 全自主完成任务；系统目录阻止列表与审计日志仍生效，随时可关闭恢复审批
- **任务执行面板**：工具链执行时右下角悬浮窗实时显示——当前工具、总耗时、✅/❌ 统计、最近 5 条结果、产物计数，可停止/收起/拖动，结束后自动隐藏
- **工具卡片结果摘要**：标题升级为 `✅/❌ [工具] write_file · 已写入 ok.txt · 1.2s`；**失败的工具自动展开**显示错误，一目了然
- **任务完成报告**：`[任务完成] ✅ 工具 2 成功 / 1 失败 · 耗时 12.3s · token 统计`
- **进程终端**：AI 启动的服务器/长驻进程实时输出到独立终端窗口（工具菜单「进程终端」，启动时自动弹出）——进程下拉切换、停止、清空、自动跟随滚动；配套工具 start_process / stop_process / list_processes（开发场景包默认启用），退出程序自动终止所有后台进程
- **工作目录机制**：明确 AI 执行任务的"家"——输入框旁「📁 目录」或工具菜单一键指定（自动加入权限允许目录）；状态栏常显当前目录；AI 每次请求都会收到工作目录提示，新任务自动在目录下创建独立子目录；场景包自动重置到工作区
- **任务质量闭环**：常驻行为指令（先计划再执行 / 完成度自检 / 网页必须启动验证）；任务提到文件时自动读取注入上下文；项目 README/配置自动摘要注入；成功任务工具链自动记忆复用（patterns.json）；environment_info 环境感知工具（Python 版本/已装包/磁盘空间）
- **🧬 自我进化**：鲸语可感知自身代码库（project_info / read_project_file）并提交改进提案（create_evolution 写入 `evolutions/` 分支，**绝不修改原文件**）；工具菜单「自我进化」查看提案说明 / 差异预览 / **采纳**（自动备份 .evobak 后应用，重启生效）/ **忽略**；EVOLUTION.md 记录改动内容、原因、风险与验证方式
- **🧬 主动发起与督促**：工具菜单「自我审查（生成报告）」——管理员一键发起，可选重点（全面/性能/安全/体验/代码质量），鲸语自动分析自身并**产出审查报告 MD**（问题总览 / 现状代码 / 替换代码 / 验证方式，写入工作区 code-review/），供开发 AI 直接实施；启动时检测距上次审查超过 `evolution_reminder_days` 天（默认 7）自动提示；「打开审查报告目录」一键直达
- **🧬 能力扩展层（v2）**：工具按 14 组全新分类（基础系统 / 信息检索 / 长期记忆 / 文件管理 / 代码与终端 / 数据处理 / 文档创作 / 媒体感知 / 浏览器 / 通信通知 / 任务调度 / 进程管理 / 协作与进化 / 洞察与断点），工具设置对话框按组展示；新增 22 个工具：
  - **任务调度**：schedule_task（cron/HH:MM/每N分钟，复用定时任务引擎）/ list_schedules / cancel_schedule——AI 可主动安排"每周五生成周报"这类周期性任务
  - **桌面通知**：notify_desktop（Windows Toast，离线可用）
  - **剪贴板**：clipboard_get（敏感，走审批）/ clipboard_set
  - **文件闭环**：delete_file（默认回收站可恢复，高危走审批）/ archive_files / extract_archive（zip-slip 防护）/ batch_rename（dry_run 预览）
  - **媒体感知**：image_understand（多模态看图，需视觉模型端点）/ screen_capture（截屏，走审批）/ speech_to_text（faster-whisper 本地转写）
  - **本地知识库 RAG**：knowledge_index / knowledge_search（TF-IDF+bigram 语义检索，措辞不同也能命中）
  - **数据写入**：database_execute（SQLite/MySQL/PG，高危走审批 + 变更预览 + SQLite 自动备份）
  - **收邮件**：read_email（IMAP，email_config.json 的 imap 段）
  - **断点续跑**：task_checkpoint_save / task_checkpoint_load（长任务进度持久化，启动时自动注入未完成任务提示）
  - **流程编排**：run_workflow（workflows.json 步骤模板，逐条自动执行）
  - **图片生成**：image_generate（OpenAI 兼容 images API，config 配置 image_api_key/image_base_url/image_model）
  - **洞察报告**：usage_report（近 N 天 token/费用/缓存命中报告）
  - **长结果自动落盘**：工具结果超 40KB 自动保存到工作区 long_results/，上下文只留路径+首尾摘要（省 token 不丢信息）
- **Webhook 接收端**：config 配置 inbound_port + inbound_token 后，外部 POST `{"token":…, "text":…}` 即可远程下达任务（手机/脚本触发）
- **📄 文档处理**：pdf_extract（PDF 文本/表格/元数据提取，按页/范围，扫描件提示 OCR）/ pdf_create（文本/Markdown 生成 PDF，自动嵌入中文字体，标题/代码块/表格排版）/ docx_read（Word → Markdown 结构）/ pptx_read（PPT 标题/要点/备注提取）
- **📰 资讯聚合**：rss_fetch（RSS 订阅管理 list/add/remove + 抓取最新条目，可配合 schedule_task 定时生成简报）
- **🔳 二维码**：qrcode（生成 PNG / 识别本地图片多码，pyzbar 缺失时降级提示）
- **🗄 嵌入式 KV**：kv_store（set/get/delete/keys/search + TTL，diskcache，Redis 零部署替代）
- **🎬 音视频**：media_ffmpeg（信息/截图/转码/提取音频，参数白名单 + 2GB/300s 限制）
- **☁ WebDAV 云盘**：webdav（list/upload/download/delete，坚果云/Nextcloud/群晖，凭据 DPAPI 加密）
- **✍ 公众号自动写作**：run_wechat_writer（WeChat Writer 独立包：9 组信源（AI 垂直：机器之心/量子位/InfoQ/雷锋网 + 科技综合：IT之家/开源中国/Solidot + 效率工具：少数派 + 国际开发者：HN）→ 主题相关性过滤（AI 关键词词边界匹配，过滤后素材纯净度 ~50%+）→ LLM 选题（历史双通道去重）→ 三阶段写作（已抓全文素材自动附加全文节选，写作深度显著提升）→ 质量门禁（不达标自动重写）→ 存草稿箱（只产草稿不发布）；信源可按 `rss_groups` 分组自定义；可配合 schedule_task 每日定时触发；`python -m wechat_writer --dry-run/--run`）
- **提案自检**：自我进化面板新增「提案自检」按钮——采纳前对提案 .py 文件做语法编译验证
- **🧬 功能建议（升级方向）**：工具菜单一键发起——鲸语基于对自身架构、用户场景与 DeepSeek 能力特性的理解，提出 6-10 个新功能/升级建议（名称/价值/实现思路/复杂度/优先级），写入工作区 code-review/ 建议文档，供你挑选实施
- **✅ 产物核验闭环**：写文件工具返回**真实核验结果**（实际字节数 + 已核验存在）；write_code_project 逐文件报告成功/失败明细；verify_files 工具批量核验（写后必须自检，防幻觉）；任务完成报告自动核验 AI 声明创建的每个文件——**缺失立即标记 ⚠，杜绝"声称已建但目录为空"**
- **📄 会话纪要**：工具菜单一键把当前会话总结为结构化纪要（主题/要点/决策/待办/产物），写入工作区 summaries/
- **🗺 会话结构导航**：工具菜单查看会话消息时间线（用户/助手/工具），双击一键定位到聊天区原文
- **🧪 配方管理**：历史成功工具链（patterns.json）可视化管理，命名保存/一键注入复用
- **🎭 角色库**：7 个预设人格套装（翻译官/代码评审/面试官/写作润色/心理陪伴/周报），一键应用（含缓存警示）
- **📦 批量任务**：多选文件 + 指令模板（{file} 占位），AI 逐个处理逐个汇报
- **⌨ 命令面板**：Ctrl+K 唤起，输入过滤全部常用操作，Enter 执行
- 性能：Markdown 渲染内容级缓存（重渲染零解析成本）、纯文本行快速路径、同轮多工具并行执行、快照惰性落盘（10s 空闲）、大 JSON 紧凑序列化（体积减半）
- 体验：设置面板「高级参数」折叠（温度/JSON/Beta）、弹窗 ESC 关闭、Ctrl+W 关会话、F1 帮助、思考卡片头可点击（修复 + 下划线提示）、任务/进程面板主题跟随

## 安装与启动

### 方式一：一键启动（推荐）

双击 `start.bat`。首次运行会自动创建虚拟环境并安装依赖。

### 方式二：手动安装

```bash
pip install -r requirements.txt
python main.py
```

### 方式三：打包为独立 exe

双击 `build_exe.bat`，产物为 `dist\WhaleTalk.exe`（PyInstaller 单文件模式）。

要求：Python 3.9+，Windows 10/11。

## 配置

1. 在 https://platform.deepseek.com 申请 API Key
2. 启动后在顶部 "API Key" 输入框粘贴，点击 "保存配置"（或直接编辑 `config.json`）

### config.json 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| api_key | "" | DeepSeek API Key |
| base_url | https://api.deepseek.com | API 端点 |
| model | deepseek-v4-flash | 模型名（deepseek-v4-flash / deepseek-v4-pro） |
| scenario | 通用 | 通用 / 编程 / Agent / 自定义 |
| thinking | high | none / low / high / max / xhigh |
| max_tokens | 16384 | 最大输出长度（1024-65536） |
| seed | "" | 固定种子，确定性输出 |
| tools_enabled | true | 启用工具调用 |
| enabled_tools | [...] | 启用的工具名列表（含自定义工具，工具设置对话框可改） |
| system_prompt | ... | 系统提示词（保持固定可最大化缓存命中） |
| max_context_chars | 500000 | 上下文自动压缩阈值（字符数） |
| max_context_tokens | 400000 | 上下文自动压缩阈值（token 数） |
| min_kept_turns | 8 | 压缩时最少保留轮数（下限 3） |
| timeout | 120 | API 读取超时秒数（连接固定 10s） |
| restore_session | true | 启动时恢复上次会话 |
| font_size | 10 | 界面字号（8-18） |
| input_height | 4 | 输入框高度（行数 2-14，可拖拽或 Ctrl+↑/↓ 调整） |
| sidebar_width | 224 | 侧栏宽度（px，拖动侧栏与聊天区之间的分隔条调整，160-480，双击恢复默认） |
| max_tool_rounds | 10 | Agent 工具调用轮数上限（1-50） |
| custom_temperature | 1.0 | 自定义场景采样温度（0.0-2.0） |
| custom_top_p | 1.0 | 自定义场景 top_p（0.0-1.0） |
| monthly_budget | 0.0 | 月度预算（元，0=不限） |
| block_on_budget | false | 达到预算后阻止发送 |
| privacy_mode | false | 隐私模式：不保存快照/统计/日志 |
| theme | light | 主题：light / dark（纯黑） |
| json_output | false | JSON 输出（response_format） |
| beta_api | false | Beta API：启用前缀续写 / FIM 补全（base_url 自动加 /beta） |
| peak_warning | true | 高峰时段发送前提示 |
| fold_early_threshold | 0 | 长会话惰性折叠阈值（消息块数，0=关闭） |
| current_profile | "" | 当前 Profile 名称（Profile 管理对话框切换） |
| 权限（permissions.json） | — | 智能体行动权限：filesystem.allow_write / shell.allow_run_command 默认 false，allowed_dirs / blocked_dirs / approval_mode（auto/confirm/deny），独立配置文件 |
| webhooks.json | {} | Webhook 推送通道：`{"dingtalk": "https://oapi.dingtalk.com/robot/send?access_token=…", "serverchan": "https://sctapi.ftqq.com/KEY.send", "slack": "https://hooks.slack.com/services/…", "generic": "https://…"}`（工具菜单「推送与数据库配置」可视化编辑） |
| db_config.json | {} | 数据库连接：`{"mysql": {"default": {"host","port","user","password","database"}}, "postgres": {...}}`（只读查询与 database_execute 写操作使用） |
| email_config.json | {} | 邮件配置：`{"smtp": {...}, "imap": {"host","port","user","password","ssl"}}`（send_email 用 smtp，read_email 用 imap） |
| inbound_port / inbound_token | 0 / "" | Webhook 接收端：本地监听端口与鉴权 token（0=关闭），外部 POST `{"token":…, "text":…}` 远程下达任务 |
| image_api_key / image_base_url / image_model | "" / "" / gpt-image-1 | 图片生成配置（OpenAI 兼容 images API） |

> 安全提示：`config.json` 中包含你的 API Key，请勿将此文件或项目目录分享/提交到公开仓库；打包 exe 前请先清空 key。

## 场景参数速查

| 场景 | temperature | top_p | 思考强度 |
|------|-------------|-------|----------|
| 通用 | 1.0 | 1.0 | high |
| 编程 | 0.15 | 0.95 | max |
| Agent | 1.0 | 0.95 | max |
| 自定义 | 可配置 | 可配置 | high |

说明：开启思考模式后，API 会忽略 temperature / top_p / presence_penalty / frequency_penalty，工具已自动处理该互斥关系（禁用思考时自动传回采样参数）；自定义场景的温度与 top_p 在设置面板中配置（仅禁用思考时生效）。

## 使用提示

- Enter 发送，Shift+Enter 换行；Ctrl+F 搜索对话内容；Ctrl+N 新会话；Ctrl+E 导出历史
- 输入框高度：拖动聊天区下方的分隔条（双击恢复默认），或聚焦输入框后按 Ctrl+↑/↓，高度随配置保存
- 双击左侧会话列表可重命名；右键会话可置顶/设置标签/删除/导出；聊天区右键可编辑/重新生成/删除/收藏/分叉/引用消息
- 右侧设置面板收纳模型、场景、思考模式、输出上限、seed、JSON 输出、Beta API、API Key、字号与主题等参数
- 思考/工具调用卡片点击标题可展开收起；折叠内容仍参与搜索与复制
- 思考模式选 "max" 时，建议将上下文窗口保持在 384K 以上（上下文占用过高时状态栏会提示）
- 保持系统提示词固定，缓存命中率实测可达 99%，状态栏缓存占比为绿色表示健康；修改提示词前会弹出缓存警示
- 继续生成：回答结束后「编辑 → 继续生成」或聊天区右键，从回复末尾续写（需开启 Beta API）
- 回复变体：「编辑 → 生成变体」以新 seed 再生成一版并自动存档；「编辑 → 浏览变体」可查看/恢复/复制任意版本
- FIM 补全：「工具 → FIM 代码补全」，输入前缀+可选后缀补全中间代码，结果可一键插入输入框（自动使用 /beta 端点）
- JSON 输出：设置面板开启后请求携带 response_format，自动注入 JSON 指令（不污染会话上下文），适合结构化输出场景
- 输入框底部实时显示「约 X token」，发送前即可估算本轮输入占用
- 状态栏显示「⏰ 高峰时段」表示当前为 DeepSeek 峰谷定价高峰（9:00-12:00 / 14:00-18:00，价格 2 倍），每日首次发送前提示一次
- 生成中上翻历史不会被拉回底部；模型思考期间状态栏显示「🤔 思考中…」
- 收藏消息可在「编辑 → 查看收藏」中双击或「跳转」定位到原文位置
- 输入历史：输入框按 Alt+↑/↓ 浏览本会话已发送内容（再次按↑到最早一条后返回草稿）
- 粘贴链接：Ctrl+Shift+V 把剪贴板中的网址直接粘成 [链接](url)（非网址按普通粘贴处理）
- Profile：工具菜单「Profile 管理」可保存多组账号配置并一键切换，适合多端点/多模型场景
- 长会话惰性折叠：在 config.json 设置 fold_early_threshold（如 1200）后，早期消息自动折叠为提示行，点击展开
- 导出历史：文件菜单「导出历史」选择目录，生成 session_时间戳 的 .md / .txt / .html / .jsonl 四份文件
- 提示词模板中 {{TEXT}} 会被当前输入框内容替换
- 检查更新：帮助菜单手动检查（需在 main.py 中配置 UPDATE_URL 发布源）
- 深色主题下标题栏变黑需要 Win11 22H2+，或 Win10 系统「应用模式」为深色；否则标题栏保持系统默认色

## 版本备份

每次大版本更新前运行 `python backup.py`（或双击 `backup.bat`），会在 `backups/` 生成完整源码快照压缩包（排除 .venv/缓存/日志），自动保留最近 20 个备份（`--prune N` 可调整）。
> 注意：备份包含 config.json（含 API Key），请勿外传备份文件。

## 数据位置

- 日志：`%USERPROFILE%\Documents\WhaleTalk\logs\assistant.log`
- 历史：`%USERPROFILE%\Documents\WhaleTalk\history\`
  - `session_latest.json`：最近会话快照（启动自动恢复）
  - `sessions\`：全部会话按需落盘（历史会话库懒加载的数据源）
- 归档：`%USERPROFILE%\Documents\WhaleTalk\archives\`（上下文压缩被裁剪内容的 Markdown 归档）
- 统计：`%USERPROFILE%\Documents\WhaleTalk\stats.json`（用量统计）
- 自定义：`%USERPROFILE%\Documents\WhaleTalk\user_tools.json`（自定义工具）、`prompts.json`（提示词库）

> 升级自旧版（DeepSeek_Assistant 数据目录）时，首次启动会自动将整个数据目录迁移到 WhaleTalk，历史会话/统计/归档完整保留。

## 版本记录

| 版本 | 说明 |
|------|------|
| 1.11.0 | **UI 布局定版（Layout Specification v1.0）**：统一 LAYOUT 尺寸常量系统（窗口 1280x820/minsize 880x620、侧栏 260(200-420)、面板 280(240-480)/文件视图 460、菜单 34/状态栏 30、内容列 560-860）；**核心修复输入区与聊天内容列同宽对齐**（1280 窗口错位 96px→0，四档窗口全部对齐）；窗口几何记忆（config window_geometry 恢复+屏幕内校验）；紧凑模式重校准（≤1120 收侧栏、≤1000 收面板，窄窗优先保聊天，内容列物理容器让步防越界）；对话框三档规范化（420/520/640 + 高 300/420/460/540/620 自动吸附，26 个既有对话框全部归一）；状态栏右段收窄（context 条 120px）；全量测试 312 通过（新增 test_ui_layout 13 项） |
| 1.11.0 | **UI 定版大版本**（完整清单）：①品牌一致性——导出 MD 头/窗口标题/splash 启动界面统一鲸语品牌（清除 DeepSeek Assistant/DeepSeek 引擎残留），关于/帮助/余额查询/用量统计/导出成功 5 类弹窗从系统 messagebox 升级为品牌对话框，欢迎页更新；②菜单栏——新增「视图」菜单（主题/字号/Markdown/面板显隐/建议开关），工具菜单 6 组功能分区，试玩→示例任务，新增 Alt+F/E/V/T/S/H 菜单快捷键；③状态栏三段式信息分级（左=模式/目录/统计/预算，右=模型/场景/思考）；④主题 token 定版——新增 hover/note/mention/quote_bg/input_placeholder 五色（菜单悬停、时间戳、引用块、占位符全部接入）；⑤字号规范——fsz=8 与 8pt label 全量统一 9pt（main 73 处 + 面板 4 处）；⑥侧栏「会话」→「对话」+ 按钮文案统一；⑦设置面板外观组补字号标签；全量测试 299 通过 |
| 1.11.0 | **UI 定版大版本**：品牌一致性（导出 MD 头/窗口标题统一鲸语品牌、关于/帮助升级为品牌对话框、欢迎页更新）；菜单栏重构（新增「视图」菜单：主题/字号/Markdown/面板显隐/建议开关；工具菜单按 6 组功能分区：账户与用量/任务与模板/能力管理/数据与文件/自我进化/系统；「试玩任务」更名为「示例任务」）；状态栏三段式信息分级（左=模式/目录/统计/预算，右=模型/场景/思考）；侧栏命名与字号规范（「会话」→「对话」、8pt 小字统一 9pt、新增 toggle_sidebar 左侧栏显隐）；全量测试 299 通过 |
| 1.10.17 | 产物可见性全面升级：①📦 产物条——输入框上方常驻显示最近产物（打开/所在文件夹/复制路径/关闭），工具结果出现即更新；②📂 文件面板——右侧设置面板改双 Tab，文件视图树形浏览（工作区/草稿箱/最近产物/数据目录，懒加载、双击打开、右键菜单含注入输入框），自动加宽 430px；③公众号草稿统一写入工作区 drafts/（与 publish_draft 同目录，产物面板直达）；全量测试 297 通过 |
| 1.10.15 | 公众号自动写作工具（按《wechat_writer_公众号自动写作工具方案.md》PRD）：新增独立包 wechat_writer/（config/sources/topic/writer/quality/history/output/llm/main，9 模块零 GUI 依赖）+ 注册为 run_wechat_writer 工具（文档创作组）；真实核验通过：真实 RSS 采集→LLM 选题（两次运行主题自动去重）→三阶段写作（初稿缺来源标注自动重写）→质检 100 分→草稿箱/存档/HTML 落盘；修复 knowledge_index 增量复用秒级 mtime 误判（改纳秒）；全量测试 278 通过 |
| 1.10.14 | 新增 9 个工具（按 PRD 需求文档）：pdf_extract（PyMuPDF 文本/表格/元数据/页码范围/扫描件提示/加密提示）/ pdf_create（reportlab + Markdown 渲染 + 中文字体自动嵌入 + 与 pdf_extract 闭环）/ docx_read（标题层级/列表/表格→Markdown，保持文档顺序）/ pptx_read（标题/要点/备注/图片占位）/ rss_fetch（订阅管理 + 抓取去重 + 时间过滤）/ qrcode（生成 PNG + 识别多码，pyzbar 缺失降级）/ kv_store（diskcache 持久化 + TTL + 模糊检索）/ media_ffmpeg（info/截图/转码/提音频，参数白名单 + 2GB/300s 限制）/ webdav（httpx 原生 PROPFIND/GET/PUT/DELETE，凭据 DPAPI 加密）；全部遵循可选依赖模式（缺库返回可操作提示）、权限白名单、写入类工具进审批流；config.json 已注册默认启用；全量测试 249 通过 |
| 1.10.13 | 68 个工具逐一精修：send_email 多收件人修复（sendmail 传列表）；get_weather 的 date 真正生效（传 wttr.in，近 3 天预报）；read_email IMAP SINCE 英文月份（中文系统不再 BAD）；environment_info 包名映射（pillow→PIL）；read_file 按行模式单行截断（防数百 MB 单行 OOM）；image_process 逐操作明确报错 + 操作计数；write_csv/excel 混合行兜底；search_web 结果链接 SSRF/危险 URL 过滤；fetch_url 编码自适应（GBK 网页不再乱码）；PostgreSQL 查询 15s 语句超时；run_command/start_process 跟随工作目录执行（📁 传导）；cron 值域校验（分时日月周，main 与调度工具同步）；read_csv/excel/DB 查询单元格截断；chart_data 数据校验（非数值/NaN/饼图全零/kind 白名单）；image_generate size 白名单 + URL 20MB 限流；run_python 返回注明工作目录；write_code_project 字节级大小校验；schema 类型修正（integer/number）；pip 静音版本检查 |
| 1.10.12 | 全库加固与体验完善：run_python 沙箱 ast 深度检查（拦截 from-import 别名/importlib 动态导入/getattr·下标反射/写模式 open/pathlib 写，修复正则可绕过）；SSRF 加 DNS 重绑定防护（域名解析落内网即拦）；image_understand URL 图片 8MB 流式限流；自定义工具 endpoint SSRF 校验；自我进化提案移除 .bat 白名单；Webhook token 恒定时间比较 + 请求体上限；messages 复合读写加锁（worker 压缩/裁剪 vs 主线程快照/回填）；审批/询问/计划弹窗超时自动销毁；快照写盘防并发（_snapshot_writing）；摘要请求响应停止；失败判定前缀统一常量；run_python 沙箱 cwd 锚定工作区；流程编排检查-置位原子化；语音合成异步化防阻塞工具池；桌面通知/OCR 占位符替换修复；定时任务弹窗降级状态栏；外部配置原子写统一；编辑器增强（Tab/Shift+Tab 缩进、括号自动配对、配对退格、Ctrl+Backspace 删词）；聊天增强（右键朗读指定消息、快速动作扩充至 8 项、F5 重新生成）；Whisper 模型实例缓存；search_local 控制流清理；全量测试 181 通过 |
| 1.10.11 | 全代码库审查修复（30+ 项）：语音/OCR 失效修复（subprocess 补漏）、worker 线程安全（Tk 变量捕获）、会话 ID 路径穿越防护、SSRF 防护（内网/回环/元数据地址）、数据库只读校验强化（INTO OUTFILE/pg_read_file 等拦截）、run_python 静态黑名单恢复、Windows 进程树终止、权限模型大小写/符号链接/shlex 修复、工具停止竞态（副作用如实记录）、后台导出/落盘不再冻结 UI、右键菜单子菜单泄漏、_trim_context O(n²) 修复、分帧渲染挂起项丢失修复、面板 destroy 后守卫等；全量测试 120 通过 |
| 1.10.8 | 模式三态单选：标准 / 🤖完全智能 / 💬纯对话（互斥，消除同时开启的语义冲突），切换即时生效并持久化 |
| 1.10.7 | 建议展示改固定停靠：菜单栏右侧独立建议区（不弹窗不遮挡），采纳/关闭按钮，60 秒自动隐藏 |
| 1.10.6 | 纯对话模式人格重写：纯正向设定（博学友善的对话伙伴），零工具/任务/否定式词汇（消除"此地无银三百两"），记忆引导语自然化 |
| 1.10.5 | 对话/任务分离：纯对话模式（不注入工具提示词/行为指令/成功模式/任务记录，不传工具 schema，AI 回归纯粹对话写作能力），状态栏 💬 标识 |
| 1.10.4 | 任务面板懒启动：纯对话不再弹出悬浮面板（首个工具调用时才显示），观感大幅改善 |
| 1.10.3 | 采纳功能建议一期：智能思考档 auto（按复杂度路由 none/high/max）、主动建议引擎（代码块/模板/工作目录启发式 + 右下角建议条一键采纳）、项目任务记录（tasklog 跨会话交接，按工作目录） |
| 1.10.2 | 自我进化第三维度：功能建议——鲸语基于自我认知提出新功能/升级方向（名称/价值/实现思路/复杂度/优先级），产出建议 MD 文档 |
| 1.10.1 | 采纳第二轮审查新增项：Profile API Key DPAPI 加密（明文不再落盘）、产物核验覆盖 write_code_project/edit_file、run_python 入审批名单、stats 公共只读接口、统一原子 JSON 写（快照/会话/状态文件）、摘要显式超时 |
| 1.10.0 | 采纳第二份审查报告（6 核心 + 3 观察）：run_python 静态危险拦截（防任意代码执行，修正提案正则误拦）、calculate 移除幂+深度/位数限制（防 DoS）、publish_draft 路径穿越防护、crypto 加密 fail-closed（明文不落盘）、stats 原子写、首启误报修复、exporters 粗体成对替换、stop_process 等待回收、read_project_file 分页读取 |
| 1.9.5 | 自我进化工作流重构：审查产出改为**报告 MD 文档**（鲸语做诊断、开发 AI 做实施，职责分离），审查指令强制报告结构（问题总览/现状代码/替换代码/验证方式），新增「打开审查报告目录」 |
| 1.9.4 | 采纳自我进化提案（9 项）：循环防护补齐 tool 结果（防悬空 400）、stats 加锁、read_file 纳入权限模型（安全收紧）、tokens 真 LRU、exporters 行内代码修复、crypto 解密失败处理、save_config 原子写、隐私模式彻底移除文件日志、关键异常补日志 |
| 1.9.3 | 自我进化工具无条件可用（不受 enabled_tools/工具开关限制）、审查指令明确专用工具与项目位置（禁止用工作区工具分析自身） |
| 1.9.2 | 自我进化主动化：管理员一键发起自我审查（5 个重点可选）、定期督促提醒（evolution_reminder_days 默认 7 天） |
| 1.9.1 | 产物核验闭环：写工具返回真实核验结果（实际字节/存在性）、write_code_project 逐文件明细、verify_files 核验工具、行为指令强制写后自检、任务报告自动核验产物（缺失标记 ⚠） |
| 1.9.0 | 自我进化：感知自身代码（project_info/read_project_file）+ 分支提案（create_evolution）+ 提案查看/差异预览/采纳（.evobak 备份）/忽略 |
| 1.8.0 | 任务质量闭环：行为指令注入（先计划/自检/验证）、相关文件自动注入、项目关键文件摘要、成功模式记忆、environment_info 环境感知、验证步骤强制化 |
| 1.7.2 | 工作目录机制：AI 明确任务执行位置（active_dir），用户一键指定/新建子目录（自动加入权限），状态栏常显，工作目录提示注入每次请求，场景包自动重置 |
| 1.7.1 | 进程终端：后台服务器/长驻进程实时输出终端（独立窗口），start_process/stop_process/list_processes 工具，Python 自动 -u 无缓冲，退出自动清理进程 |
| 1.7.0 | 任务执行可见性：悬浮任务面板（实时工具状态/统计/产物）、工具卡片结果摘要与失败自动展开、任务完成报告升级、失败即时提示 |
| 1.6.1 | 完全智能模式：允许目录内全自动免审批（系统阻止列表仍生效），设置面板一键开启/关闭，状态栏 🤖 标识 |
| 1.6.0 | 开箱即用与产品化：场景包（办公/开发/创作一键配置）、试玩任务库（10 个）、项目上下文注入、任务报告、一键回滚 .bak、省钱报告、反馈收集、对话分享、工具进度名称显示 |
| 1.5.0 | 效率与智能体闭环：API Key DPAPI 加密、输入草稿持久化、剪贴板即问、数据清理、任务计划确认、工作区文件树、长期记忆、完成通知、定时任务、语音输入、剪贴板 OCR、缓存统计增强 |
| 1.4.0 | 智能体 L2-L4：write_code_project（多文件工程）/ browser_navigate / web_screenshot（Playwright 可选）/ publish_draft（本地草稿箱，只建草稿）+ Agent 任务模板扩展至 6 个 |
| 1.3.0 | 智能体化 L1-L3：权限模型（permissions.py，默认全关）+ 审批流（confirm 弹窗）+ 审计日志 + 6 个行动工具（write_file / edit_file / list_dir / run_command / search_local / create_doc），全部走现有工具循环与队列协议 |
| 1.2.0 | 非破坏性演进：导出 HTML/JSONL（exporters.py）、输入历史 Alt+↑/↓、Ctrl+Enter 发送、Ctrl+Shift+V 链接粘贴、Profile 多账号、长会话惰性折叠（默认关）、自动更新前自动备份 |
| 1.1.0 | 品牌化：更名「鲸语 WhaleTalk」，数据目录迁移，深海蓝鲸启动界面；新增 JSON 输出 / Beta API（前缀续写 + FIM）/ 回复变体 / 引用回复 / 会话置顶 / 收藏跳转 / 峰谷定价感知 / 缓存警示 / 输入 token 估算 / 思考动画 / 流式智能跟随 / 历史库批量删除；大量性能与健壮性优化 |
| 1.0.0 | 初版：流式对话、思考模式、Agent 工具调用、多会话、上下文压缩、统计预算、历史会话库等核心能力 |

## 常见问题

| 错误 | 原因与解决 |
|------|-----------|
| 401 认证失败 | API Key 错误，检查 key |
| 402 余额不足 | 前往充值页面充值 |
| 429 限流 | 工具已自动重试 3 次，仍失败请降低请求频率 |
| 500/503 | 服务端故障，工具已自动重试，稍后再试 |
| Agent 循环 | 同工具重复调用 3 次自动终止并提示；可调低 max_tool_rounds |
| 超时无响应 | 状态栏会提示等待时长；「■ 停止」可随时中止 |
| 深色标题栏不变黑 | Win10 需要系统「应用模式」为深色（设置→个性化→颜色）；Win11 22H2+ 自动生效 |
| 输入框调不动 | 拖动聊天区下方带 ⋮ 的分隔条；或聚焦输入框按 Ctrl+↑/↓ |
| 继续生成/变体无反应 | 「继续生成」需开启 Beta API（设置面板）；「生成变体」需存在助手回复 |
| JSON 输出返回空内容 | API 概率性空响应，工具已自动重试；可在提问中附 JSON 格式示例缓解 |
| FIM 补全失败 | FIM 为 Beta 能力，会自动使用 /beta 端点；提示 400 请确认前缀/后缀格式合法 |

## 文件结构

```
WhaleTalk/（项目目录名可自行更改，程序不依赖目录名）
├── main.py              # GUI 入口
├── splash.py            # 启动界面（深海蓝鲸主题，淡出动画）
├── deepseek_client.py   # API 客户端（流式、思考模式、JSON、续写、FIM、工具调用）
├── exporters.py         # 会话导出扩展（HTML / JSONL）
├── permissions.py       # 权限模型（白名单/审批/审计，默认全关）
├── mdparse.py           # 轻量 Markdown 渲染器
├── tokens.py            # tiktoken 估算（含回退策略）
├── prompts.py           # 提示词库（默认模板 + 读写）
├── stats.py             # 用量统计（按天/模型累计 + 费用估算）
├── backup.py            # 版本备份脚本（backups/ 目录）
├── config.json          # 配置文件
├── requirements.txt     # 依赖清单
├── start.bat            # 一键启动脚本
├── backup.bat           # 一键备份脚本
├── build_exe.bat        # PyInstaller 打包脚本（产物 dist\WhaleTalk.exe）
├── app.ico              # 应用图标（自动生成）
└── README.md
```
