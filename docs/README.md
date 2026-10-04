---
title: TwipsyBot
description: TwipsyBot 的部署、配置、功能、插件与开发文档。
---

# TwipsyBot

一只轻量、可扩展的 [Misskey](https://misskey-hub.net/) AI 机器人。

## 文档

| 页面 | 内容 |
| --- | --- |
| [**快速开始**](user-guide/getting-started.md) | Docker Compose 部署与验证 |
| [**配置**](user-guide/configuration.md) | TUI、YAML、生效方式与模型 |
| [**功能**](/user-guide/features/) | 回复、发帖、时间线与管理命令 |
| [**插件**](/user-guide/plugins/) | 内置插件与启用方式 |
| [**运维**](user-guide/operations.md) | 状态、备份与升级 |
| [**故障排查**](user-guide/troubleshooting.md) | 常见问题定位 |
| [**配置参考**](user-guide/reference/configuration.md) | 全部字段与默认值 |
| [**开发**](/dev-guide/) | 架构、插件 API 与测试 |

## 能力

- 响应提及、私聊与群聊，保留有限上下文。
- 轮转或定时自动发帖，管理员可手动发帖与生图。
- 白名单、黑名单、回复间隔与轮数限制。
- 订阅时间线与天线，通过插件扩展关键词回复、天线互动、RSS 发帖、识图与时间线观察。
- SQLite 保存状态，配置热更新，无需额外数据库。

## 须知

- 仅依赖 Misskey 访问令牌与 OpenAI 兼容 API；推荐 Docker Compose 部署。
- 使用独立机器人账号，令牌与密钥切勿提交或公开。
- 自动发帖、Radar、Iincho 会主动读写内容，先用低频、窄天线与 `local_only` 验证。
- “OpenAI 兼容”仅指协议，图片、视觉与审核能力取决于所选模型和服务端。
- 请遵守所在实例规则与联邦规范。
