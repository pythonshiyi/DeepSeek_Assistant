# 贡献指南 / Contributing

感谢你对 **鲸语 WhaleTalk** 的兴趣！无论是修 bug、加功能、改进文档，还是提 issue，都非常欢迎。

*Thanks for your interest in WhaleTalk! Bug fixes, new features, docs, and issue reports are all welcome.*

## 开发环境 / Dev Setup

- Python 3.9+（开发环境 3.12）
- 安装依赖：`pip install -r requirements.txt`（核心依赖 openai / httpx 即可运行；其余为可选能力）
- 运行：`python main.py`
- 打包：`python build_exe.bat`（产物 `dist\WhaleTalk.exe`）

## 运行测试 / Running Tests

```bash
python -m pytest tests -q
```

提交前请确保测试全部通过（当前基线 522 个用例）。

## 代码规范 / Code Style

- 纯标准库 + Tkinter，不引入重型 UI 框架
- 模块职责：
  - `main.py`：GUI 与 UI 逻辑（`AssistantApp`）
  - `deepseek_client.py`：API 客户端 + 工具实现
  - `permissions.py`：权限模型（默认全关，新增行动类工具必须接入审批流）
  - 其余小模块见 README「文件结构」
- 所有用户可控输入（路径 / 命令 / 工具参数）必须经过校验：路径走 `permissions.resolve()`，命令走 `permissions.check_shell()`，SSRF / 路径穿越 / 命令注入防护不可回退
- 写文件类工具必须返回真实核验结果（字节数 / 存在性），防幻觉产物
- 新增工具遵循可选依赖模式：缺库时返回可操作提示，不硬崩溃
- 日志用 `logging`，异常不要裸吞（`except Exception` 内至少 `logging.exception`）
- 中文字符串文案 + 必要英文注释，注释解释「为什么」而非「是什么」

## 提交信息 / Commit Messages

- 简短描述 + 可选补充，建议前缀：`fix:` / `feat:` / `docs:` / `chore:` / `refactor:` / `test:`
- 示例：`fix: run_python 沙箱补充 ast 深度检查`

## 分支与 PR / Branches & PRs

- 主分支 `main`，PR 请基于 `main` 开分支
- 新功能请同时补测试；行为变更请在 PR 描述中说明影响

## 安全提示 / Security Notes

- `config.json` 含 DPAPI 加密的 API Key，**任何情况下不得提交或外传**
- 发现安全问题请优先通过 issue 私信或邮件联系，避免公开漏洞细节（敏感信息请勿直接贴在 issue 里）
