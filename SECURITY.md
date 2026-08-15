# 安全策略 / Security Policy

## 支持的版本 / Supported Versions

| 版本 | 支持状态 |
|------|---------|
| 2.16.x（main 分支） | ✅ 积极维护 |

## 报告漏洞 / Reporting a Vulnerability

**请不要在公开 issue 中披露安全漏洞细节。**

请通过以下方式之一私密报告：

1. **GitHub Security Advisory**：仓库页面 → Security → Report a vulnerability（推荐）
2. **邮件**：发送至仓库维护者（见 GitHub 主页联系方式），标题注明 `[SECURITY]`

收到报告后我们会在 **7 天内** 回复确认，并在修复发布后统一披露。

## 已知安全设计（供审阅者参考）

- **API Key 保护**：`config.json` 中的 Key 以 Windows DPAPI 加密存储（`crypto.py`），明文永不落盘（fail-closed）
- **权限模型**：v2 默认 `blacklist`（默认放行 + 用户黑名单，黑名单默认空；用户可自行增删/清空），旧 `whitelist` 模式可回退；路径经 `resolve()` 规范化防穿越；审计日志只记不拦
- **沙箱**：`run_python` 静态 AST 检查 + `-I -S` 隔离执行
- **提交规范**：`.gitignore` 强制排除 `config.json` / 密钥文件；`CONTRIBUTING.md` 要求新增行动工具接入审批流
