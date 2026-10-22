---
name: prompt-manager
description: >-
  Google Antigravity dynamic workspace AGENTS.md rule manager and modular prompt composer. Automatically matches and injects workspace-specific rules, supports visual preset & snippet editing, manual custom mode, and preserves developer power flags across sessions.
---

# Antigravity 提示词与工作区规则管理器 (Prompt & Rule Manager)

本插件为 Google Antigravity 提供动态提示词积木编排、工作区规则绑定、可视化方案定制与开发者特性持久化守护能力。

## 🎯 核心特性
- **随工作区自动匹配与注入**：根据 Antigravity 当前活动会话所在工程目录，自动读取并注入对应的 `AGENTS.md`。
- **自定义手动编辑模式 (优先保留)**：
  - 手动模式下，编辑区文本优先保存生效，不会被预设规则重置覆盖。
  - 支持直接在编辑器中撰写规则，或从积木库中一键追加特定段落。
- **预设方案与积木可视化管理**：
  - 支持直接在界面上新建、编辑与删除预设方案（包含名称、描述与关联积木）。
  - 支持编辑任意积木片段的正文 Markdown 内容，并支持随时一键恢复出厂默认。
- **模块化积木库**：
  - 👤 角色定位（通用助手 / 资深全栈工程师 / 系统架构师 / 极简模式 / 安全审计）
  - 💻 技术栈规范（Python & TypeScript / Rust & C++ 系统编程）
  - 🛡️ 交付标准（代码完整性保证 / 本地文件超链接化 / 自动化测试配套）
  - ✏️ 自定义积木（支持自由新增与分类管理）
- **开发者特性守护 (DevFlags Guardian)**：
  - 后台自动守护 Antigravity 开发者模式与实验性高级功能，防止因语言服务端口变动导致开关掉落。
- **安全备份保障**：写入前自动备份旧版为 `AGENTS.md.bak`，支持一键还原。

## 🚀 侧边栏面板入口
在对话中点击以下链接即可在 Antigravity 辅助侧边栏中打开管理界面：
[提示词管理](sidecar://antigravity-prompt-manager/prompt_manager/ui.html)
