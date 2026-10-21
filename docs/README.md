---
title: TwipsyBot
description: TwipsyBot 的部署、配置、功能、插件与开发文档。
---

<div align="center" style="text-align: center">

<p align="center">
    <img src="./public/twipsybot.png" alt="TwipsyBot" width="200" height="200" style="display: block; margin: 0 auto">
</p>

<br>

# TwipsyBot

**一只轻量、插件化的 [Misskey](https://misskey-hub.net/) AI 机器人**

</div>

<div class="side-by-side">

<div>

## 文档

| 页面 | 内容 |
| --- | --- |
| [**快速开始**](user-guide/getting-started.md) | 部署与功能验证 |
| [**配置**](user-guide/configuration.md) | TUI、YAML、生效方式与模型 |
| [**功能**](user-guide/features/) | 对话、发帖、时间线与管理命令 |
| [**插件**](user-guide/plugins/) | 内置插件与启用方式 |
| [**运维**](user-guide/operations.md) | 状态、备份与升级 |
| [**故障排查**](user-guide/troubleshooting.md) | 常见问题定位 |
| [**配置参考**](user-guide/reference/configuration.md) | 全部字段与默认值 |
| [**开发**](dev-guide/) | 架构、插件 API 与测试 |

</div>

<div>

## 模型 <Badge type="info" text="OpenAI-compatible API" />

| 提供商 | 兼容性 | 多模态 |
| --- | :---: | :---: |
| [**OpenAI**](https://platform.openai.com/docs/api-reference/introduction) | ✅ | 💬 👁️ 🎨 |
| [**DeepSeek**](https://api-docs.deepseek.com/) | ✅ | 💬 👁️ |
| [**GLM**](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction) | ✅ | 💬 👁️ 🎨 |
| [**Kimi**](https://platform.kimi.com/docs/api/overview) | ✅ | 💬 👁️ |
| [**Qwen**](https://www.alibabacloud.com/help/zh/model-studio/compatibility-of-openai-with-dashscope) | ✅ | 💬 👁️ 🎨 |
| [**Gemini**](https://ai.google.dev/gemini-api/docs/openai) | ✅ | 💬 👁️ 🎨 |
| [**Grok**](https://docs.x.ai/developers/quickstart) | ✅ | 💬 👁️ 🎨 |
| [**Ollama**](https://docs.ollama.com/api/openai-compatibility) | ✅ | 💬 👁️ 🎨 |

</div>

</div>

<style>
.side-by-side {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  column-gap: 24px;
}
.side-by-side > div { min-width: 0; }
</style>

## 能力

- 实时回复提及、私聊与群聊，记录用户资料卡与关系。
- 定制主题、RSS 自动发帖，或手动指示 AI 发帖和生图。
- 订阅时间线与天线、关键词回复、天线贴互动、识图、联网检索。
- 互动命令：查状态、亲密度、切模型、功能开关、清理旧帖等。
- 白名单、黑名单、对话间隔与次数限制。
- SQLite 保存状态，配置热更新。

::: tip 🧩 扩展插件
可添加任何 Misskey API 支持的功能（即使本项目未封装的端点，见 [`call_api`](dev-guide/plugins.md#misskey-与-drive)）。
:::

::: warning ⚠️ 使用须知
请遵守所在实例规则与联邦规范，注册独立机器人账号，令牌与密钥切勿提交或公开。
:::
