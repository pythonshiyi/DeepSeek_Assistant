# 模块拆分说明

本文件记录从 `main.py` / `deepseek_client.py` 中拆出的可复用模块，便于后续维护与继续重构。

## 已拆分模块

| 模块 | 内容 |
|---|---|
| `uiutils.py` | `CappedList` / `MAX_BLOCKS` / `index_num` |
| `security.py` | SSRF 校验、`_safe_url`、`_is_private_host`、信任白名单 |
| `db_utils.py` | 只读 SQL 校验、变更预览、表格格式化 |
| `persistence.py` | 原子 JSON 写入 |
| `pdf_utils.py` | PDF 页码范围、中文字体、Markdown→PDF 片段 |
| `proc_utils.py` | 进程树终止 |
| `net_utils.py` | 共享 HTTP 客户端、重定向校验 |
| `search_utils.py` | 搜索解析、去重、安全过滤 |
| `layout.py` | 布局常量 |
| `themes.py` | 主题 token |
| `deps.py` | 可选依赖清单 |
| `config_defaults.py` | 默认配置、系统提示词、内建工具、行为指令、更新源、场景思考默认值 |
| `roles.py` | 内置角色 |
| `templates.py` | 任务模板、试玩任务 |
| `app_utils.py` | 布尔转换、空壳目录判断、清理、干净退出、隐私日志 |
| `render_utils.py` | 流式 Markdown 切分、代码块切分 |
| `ui_utils.py` | 菜单销毁 |
| `migration.py` | 旧数据目录迁移 |
| `user_tools.py` | 自定义工具加载与缓存 |
| `profiles.py` | Profile 配置读写 |
| `config_utils.py` | 配置加载、规范化、保存 |
| `stores.py` | 最近产物、模式、失败、任务日志、记忆、调度等 JSON 存取 |
| `session_utils.py` | 会话 ID 工具 |
| `dialogs/` | 对话框包（`about_help.py`：关于/帮助/欢迎/插件引导；`data_stats.py`：用量/依赖/失败/任务记录/检查点/最近产物/收藏/功能建议/进化审查；`workspace.py`：工作目录/清理/文件树；`session.py`：历史会话/命令面板/上下文详情/会话轨迹；`productivity.py`：批量任务/FIM/变体/进化提案） |

## 后续建议

- 继续拆分 `main.py` 中的面板、消息渲染、长流程等业务模块（对话框已拆为 `dialogs/` 包）。
- 继续拆分 `deepseek_client.py` 中的媒体/文档/数据库/进程管理等业务模块。
- 保持“先纯函数/工具模块，再业务模块”的顺序，每步运行全量测试。
