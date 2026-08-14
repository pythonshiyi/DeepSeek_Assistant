# 🐋 鲸语 WhaleTalk · AI 对话助手 / AI Desktop Assistant

[![CI](https://github.com/pythonshiyi/WhaleTalk/actions/workflows/ci.yml/badge.svg)](https://github.com/pythonshiyi/WhaleTalk/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/pythonshiyi/WhaleTalk?color=blue)](https://github.com/pythonshiyi/WhaleTalk/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)

> **中文为主 · English follows**（完整中文介绍 + 英文简介）

**鲸语 WhaleTalk** 是一款为 DeepSeek V4 API 深度优化的 Windows 桌面 AI 对话助手——不只是聊天窗口，而是一个**能自主完成任务、能扩展能力、能自我进化**的 AI 工作台。**鲸语是独立产品品牌，与 DeepSeek 官方无任何关联**。

*WhaleTalk is a Windows desktop AI assistant deeply optimized for the DeepSeek V4 API — not just a chat window, but an AI workbench that executes tasks autonomously, extends itself with plugins, and evolves its own codebase. WhaleTalk is an independent product brand with no affiliation to DeepSeek.*

## 📋 目录

- [项目介绍](#项目介绍)
- [核心亮点](#-核心亮点)
- [功能特性](#-功能特性)
- [快速开始](#-快速开始)
- [常用快捷键](#-常用快捷键)
- [安全设计](#-安全设计)
- [数据与隐私](#-数据与隐私)
- [常见问题](#-常见问题)
- [文档](#-文档)
- [English Introduction](#english-introduction)
- [品牌与关注](#-品牌与关注)

## 项目介绍

鲸语以「**深度适配 DeepSeek V4**」为设计原点，把 V4 的 Agent 能力、1M 上下文、峰谷定价、前缀缓存等特性转化为真正可用的桌面体验：

- **对话层**：流式思考与回答、Markdown 实时渲染、多会话管理、上下文智能压缩
- **执行层**：100+ 内置工具（搜索/代码/文件/数据/浏览器/邮件/多媒体），权限模型与审批流
- **扩展层**：插件体系（工具/技能/流程/场景），AI 造插件，零代码扩展能力
- **进化层**：自我审查、改进提案、经验复盘——越用越聪明

技术底座：Python + Tkinter，仅核心依赖 openai/httpx，其余能力按需可选。

## ✨ 核心亮点

| | 亮点 | 说明 |
|---|---|---|
| 🧩 | **插件体系（.wtplugin）** | 工具/技能/流程/场景组合成单个 JSON 文件，导入即用、可分享；**插件工坊**里描述需求，AI 自动生成并安装插件 |
| 🤖 | **100+ Agent 工具** | 联网搜索（多引擎聚合）、代码执行、文件操作、数据库、浏览器、邮件、多媒体……自动编排、失败自愈 |
| 🧬 | **自我进化** | 鲸语能阅读自己的代码、提交改进提案、自我审查并产出报告——由你决定是否采纳 |
| ✍ | **公众号自动写作** | 多信源采集 → 选题 → 三阶段写作 → 质量门禁 → 存草稿箱（只产草稿，发布权在你） |
| 💰 | **成本工程** | 前缀缓存命中率 ~99%、思考成本剥离、峰谷错峰执行、预算控制——用最少的钱干最多的活 |

## 📦 功能特性

### 💬 对话体验

- 流式输出：思考过程与回答实时显示（40ms 批量渲染），思考/工具调用折叠卡片
- 流式 Markdown 渲染：粗体/代码/链接/表格即时呈现，未闭合标记智能暂缓
- 思考模式全档位（none/low/medium/high/xhigh/max + auto 智能路由）
- 多会话管理：置顶/标签/搜索/收藏/分支对话/临时会话/历史会话库懒加载
- 会话导入导出：JSON/JSONL 导入，MD/TXT/HTML/JSONL 四格式导出
- 回复变体、继续生成（Beta）、FIM 补全、JSON 结构化输出
- 1M 上下文：双阈值自动压缩 + LLM 摘要 + 裁剪内容归档，长对话不卡不贵
- 输入体验：token 实时估算、草稿持久化、剪贴板即问、输入历史、快捷键

### 🤖 Agent 智能体

- **100+ 内置工具**，按 14 组分类管理，可单独启停
  - 信息：多引擎联网搜索（num/翻页/时间/站点过滤，引擎健康度自动降级）、GitHub 仓库搜索（org:/language: 原生语法）、实时热点（Hacker News）、网页全文抓取（含被墙站点代理通道）、RSS 订阅（精选预置源）
  - 执行：沙箱 Python、终端命令、进程管理、pip 安装、代码工程创建
  - 数据：SQLite/MySQL/PostgreSQL、CSV/Excel、图表、KV 存储、WebDAV
  - 文档：PDF 提取/生成、Word/PPT 读取、二维码、音视频处理、图片生成/理解/OCR
  - 通讯：邮件收发、桌面通知、Webhook 推送/接收
  - 协作：并行子代理、任务检查点（断点续跑）、流程编排、工作流
  - 环境：系统资源自检（CPU/内存/磁盘/网络连通性）
- **通用 API 调用**（call_api）：对接任意开放 API，支持自定义请求头/JSON 体（超时 180s/响应 500KB，内网白名单配置）
- 任务质量闭环：先计划再执行、产物核验（杜绝"声称已建但目录为空"）、失败模式库、成功模式记忆、**自动经验复盘**（任务失败自动沉淀经验到长期记忆，跨会话规避已知坑）
- 自主模式三态：🤖 完全智能（全自动）/ 💬 纯对话（零工具）/ 标准（按配置）
- 权限模型：默认全关，白名单 + 审批流（auto/confirm/deny）+ 审计日志，路径防穿越、命令白名单、SSRF 防护

### 🧩 插件与扩展

- **.wtplugin 插件格式**：工具 + 技能（提示词模板）+ 流程 + 一键场景的组合包
- 插件中心（Ctrl+Shift+P）：导入（拖拽/文件）、导出分享、启停、卸载（精确移除，不影响手动添加的同名能力）
- 插件画廊：内置 3 个示例插件（小红书文案/周报生成器/会议纪要助手）
- **插件工坊**：自然语言描述需求 → AI 生成并安装插件，立即生效
- 依赖状态检查：17 项可选能力一键体检（已装 ✅/缺失 ⚠ + 安装命令）
- 自定义工具 SDK：注册自己的 HTTP 工具，Agent 自动调用

### ⚡ 自动化与常驻

- 定时任务：cron 表达式 / HH:MM / 每 N 分钟，错过自动补跑，峰谷错峰执行（省一半费用）
- 流程管理：多步骤流程模板，可引用已验证的成功工具链（配方）
- 每日简报：一键生成当日 AI/科技资讯简报，可定时晨报
- 系统托盘常驻 + 开机自启：24 小时无人值守，Webhook 接收端远程下达任务
- 任务执行面板：实时显示工具进度/统计/产物，失败自动展开

### 📄 文档与数据

- 工作区机制：AI 明确的任务"家"，产物集中管理，应用内预览/打开/回滚
- 长期记忆 + 知识库（本地 RAG 语义检索）+ 项目任务记录
- 用量统计与预算控制：按天/模型累计 token 与费用、缓存命中省钱报告

### 🎙️ 多媒体

- 语音：TTS 朗读、语音转文字（离线 whisper）、图片理解、屏幕截图/OCR
- 图像：生成（OpenAI 兼容）、处理（缩放/裁剪/水印）、多模态理解

### 🎨 个性化与体验

- 主题：浅色 / 纯黑；字号/行距/面板布局可调，屏幕自适应（1080p/2K/4K）
- 角色库：人格预设 + 自定义角色，三层职责分离（人格/任务能力/模型参数互不干扰）
- 命令面板（Ctrl+K）、菜单精确分类、F11 全屏、启动默认最大化
- 隐私模式：不保存快照/统计/日志，状态栏 🔒 标识

## 🚀 快速开始

### 安装

```bash
# 方式一：双击 start.bat（推荐，自动创建虚拟环境并安装依赖）
# 方式二：
pip install -r requirements.txt
python main.py
# 方式三：双击 build_exe.bat 打包为 dist\WhaleTalk.exe
```

要求：**Python 3.9+，Windows 10/11**。

### 配置

1. 在 https://platform.deepseek.com 申请 API Key
2. 启动后在顶部 "API Key" 输入框粘贴并保存（或编辑 `config.json`）

主要配置项（config.json）：`model`（deepseek-v4-flash / deepseek-v4-pro，亦支持任意 OpenAI 兼容模型）、`scenario`、`thinking`、`max_tokens`、`system_prompt`（保持固定可最大化缓存命中）、上下文压缩阈值、`monthly_budget`、`privacy_mode`、`theme`、`call_api_allowed_hosts`（内网白名单）等；权限/推送/数据库/邮件为独立配置文件（permissions.json / webhooks.json / db_config.json / email_config.json）。

## ⌨️ 常用快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+N / Ctrl+W | 新会话 / 关闭会话 |
| Ctrl+F / Ctrl+Shift+F | 对话内搜索 / 全局搜索 |
| Ctrl+E / Ctrl+Shift+E | 导出历史 / 导出会话 JSON |
| Ctrl+K | 命令面板 |
| Ctrl+Shift+T / Ctrl+Shift+P | 工具中心 / 插件中心 |
| Ctrl+Shift+Q | 剪贴板即问 |
| Ctrl+↑/↓ | 调整输入框高度 |
| F11 | 全屏 |
| Alt+F/E/V/T/A/S/H | 顶级菜单直达 |

## 🔒 安全设计

- **API Key**：Windows DPAPI 加密存储（fail-closed，明文永不落盘）
- **权限模型**：行动能力默认全关；目录白名单 + 系统目录阻止列表；路径 resolve 防穿越；命令白名单/黑名单；审批流 + 审计日志（10MB 轮转）
- **SSRF 防护**：URL 抓取/API 调用拦截内网与云元数据地址，DNS 重绑定防护；本地服务可通过显式白名单放行
- **沙箱执行**：run_python 静态 AST 检查 + 隔离模式运行
- **搜索结果过滤**：危险链接（javascript:/file:/回环）一律剔除
- **隐私模式**：一键开启后不保存任何快照/统计/日志

## 📁 数据与隐私

- 日志：`%USERPROFILE%\Documents\WhaleTalk\logs\assistant.log`
- 历史会话：`%USERPROFILE%\Documents\WhaleTalk\history\`
- 归档/统计/提示词/自定义工具：`%USERPROFILE%\Documents\WhaleTalk\` 下
- 旧版数据目录自动迁移；`config.json` 含 API Key，**切勿外传或提交**

## ❓ 常见问题

| 错误 | 原因与解决 |
|------|-----------|
| 401 认证失败 | API Key 错误，检查 key |
| 402 余额不足 | 前往充值页面充值 |
| 429 限流 | 工具已自动重试 3 次，仍失败请降低请求频率 |
| Agent 循环 | 同工具重复调用 3 次自动终止；可调低 max_tool_rounds |
| 深色标题栏不变黑 | Win10 需系统「应用模式」为深色；Win11 22H2+ 自动生效 |
| 可选功能不可用 | 工具中心 → 依赖状态查看缺失项与安装命令 |

## 📚 文档

- [更新记录](CHANGELOG.md)（完整版本历史）
- [贡献指南](CONTRIBUTING.md)（开发环境/代码规范）
- [安全策略](SECURITY.md)（漏洞报告）
- [技术文档](TECH_NOTES.md)（架构与约定，面向维护者/AI）

---

## English Introduction

### 🐋 WhaleTalk — AI Desktop Assistant (DeepSeek V4)

WhaleTalk (Chinese: 鲸语, "Whale Song") is a **Windows desktop AI chat assistant** built and deeply optimized around the **DeepSeek V4 API** — an AI workbench that executes tasks, extends itself, and evolves. **WhaleTalk is an independent product brand with no affiliation to DeepSeek.**

### Highlights

- **🧩 Plugin system (`.wtplugin`)**: tools, skills, workflows, and scenarios bundled into a single shareable JSON file. The **Plugin Workshop** lets the AI generate and install a plugin from a natural-language request. Built-in gallery with 3 sample plugins.
- **🤖 100+ Agent tools**: multi-engine web search (num/paging/time/site filters, engine health auto-degradation), GitHub search (org:/language: syntax), Hacker News realtime, page fetching (incl. blocked-site proxy channel), RSS presets, sandboxed Python, terminals, files, databases, browser automation, email, media, system self-check (CPU/memory/disk/network), and more — orchestrated automatically with self-healing on failure. A universal **call_api** tool connects to any public API (custom headers/JSON, 180s timeout, 500KB response, explicit intranet whitelist). Auto-reflection persists failed-task lessons into long-term memory so the AI avoids known pitfalls across sessions.
- **🧬 Self-evolution**: reads its own codebase, writes improvement proposals (never touching original files), self-review reports with one-click adoption and rollback.
- **✍ WeChat Writer**: multi-source collection → topic selection → 3-stage writing → quality gate → local drafts (publication stays in your hands).
- **💰 Cost engineering**: ~99% prefix-cache hit rate, thinking-cost stripping, off-peak scheduling, budget control.
- **Plugins, automation**: cron scheduling with catch-up, workflows, system tray, auto-start, daily briefings.
- **Security**: DPAPI-encrypted API keys, default-deny permission model with approval flow and audit logs, SSRF protection with explicit local-service whitelist, sandboxed Python, privacy mode.
- **UI**: light/pure-black themes, screen-adaptive layout, command palette, F11 fullscreen, startup maximized.

### Quick Start

```bash
pip install -r requirements.txt
python main.py
```

Requires **Python 3.9+ and Windows 10/11**. Get an API Key at https://platform.deepseek.com and paste it into the top field on first launch.

### Tech Stack

Python (Tkinter) · DeepSeek V4 API (OpenAI-compatible streaming) · tiktoken · PyInstaller · pystray · httpx · optional: playwright, faster-whisper, PyMuPDF, reportlab, psutil, curl_cffi

---

## 📱 品牌与关注

**鲸语 WhaleTalk** 与 **微墨 WeMark** 由 **十一AIGC** 出品——专注 AI 工具与效率应用的独立创作者。

更多 AI 玩法、工具教程与新品动态，欢迎关注公众号：

> **📱 微信公众号：十一AIGC**

*WhaleTalk and WeMark are crafted by **ShiYi AIGC (十一AIGC)**, an independent creator focused on AI tools and productivity apps. Follow our official WeChat account for more AI tips, tutorials, and product news.*

如果你喜欢这个项目，欢迎 ⭐ Star、分享给朋友，或在评论区留下你的建议 —— 你的支持是我们持续更新的最大动力！

*If you like this project, please ⭐ Star it, share it, and leave your feedback — your support keeps us shipping!*
