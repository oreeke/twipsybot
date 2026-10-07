---
title: TwipsyBot
description: TwipsyBot 的部署、配置、功能、插件与开发文档。
---

<p>
    <img src="/twipsybot.png" alt="TwipsyBot" width="200" height="200">
</p>

<br>

# TwipsyBot

一只轻量、可扩展的 [Misskey](https://misskey-hub.net/) AI 机器人。

## 文档

| 页面 | 内容 |
| --- | --- |
| [**快速开始**](user-guide/getting-started.md) | 部署与验证 |
| [**配置**](user-guide/configuration.md) | TUI、YAML、生效方式与模型 |
| [**功能**](/user-guide/features/) | 对话、发帖、时间线与管理命令 |
| [**插件**](/user-guide/plugins/) | 内置插件与启用方式 |
| [**运维**](user-guide/operations.md) | 状态、备份与升级 |
| [**故障排查**](user-guide/troubleshooting.md) | 常见问题定位 |
| [**配置参考**](user-guide/reference/configuration.md) | 全部字段与默认值 |
| [**开发**](/dev-guide/) | 架构、插件 API 与测试 |

## 能力

- 实时回复提及、私聊与群聊，保留有限上下文，可永久记录用户资料卡与关系。
- 轮转或定时自动发帖，管理员还可以手动指示 AI 发帖和生图。
- 订阅时间线与天线、关键词回复、天线贴互动、自定义主题或 RSS 发帖、识图、联网检索。
- 关注者与管理员可用的互动命令：查状态、亲密度、切模型、功能开关、清理旧帖等。
- 白名单、黑名单、对话间隔与次数限制。
- SQLite 保存状态，配置热更新，无需额外数据库。

## 模型

理论支持所有 OpenAI 兼容端点，以下仅列出常用：

| 提供商 | 兼容性 | 多模态 |
| --- | :---: | :---: |
| [OpenAI](https://platform.openai.com/docs/api-reference/introduction) | ✅ | 💬 👁️ 🎨 |
| [DeepSeek](https://api-docs.deepseek.com/) | ✅ | 💬 👁️ |
| [GLM](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction) | ✅ | 💬 👁️ 🎨 |
| [Kimi](https://platform.kimi.com/docs/api/overview) | ✅ | 💬 👁️ |
| [Qwen](https://www.alibabacloud.com/help/zh/model-studio/compatibility-of-openai-with-dashscope) | ✅ | 💬 👁️ 🎨 |
| [Gemini](https://ai.google.dev/gemini-api/docs/openai) | ✅ | 💬 👁️ 🎨 |
| [Grok](https://docs.x.ai/developers/quickstart) | ✅ | 💬 👁️ 🎨 |
| [Perplexity](https://docs.perplexity.ai/docs/agent-api/openai-compatibility) | ✅ | 💬 👁️ |
| [Ollama](https://docs.ollama.com/api/openai-compatibility) | ✅ | 💬 👁️ 🎨 |

## 须知

- 仅依赖 Misskey 访问令牌与 OpenAI 兼容 API，推荐 Docker Compose 部署。
- 使用独立机器人账号，令牌与密钥切勿提交或公开。
- 自动发帖、Radar、Iincho 会主动参与联邦互动，先用低频、窄天线与 `local_only` 验证。
- “OpenAI 兼容”仅指协议，图片、视觉与审核能力取决于所选模型和服务端。
- 请遵守所在实例规则与联邦规范。
