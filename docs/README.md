---
title: TwipsyBot
description: TwipsyBot 的安装、配置、功能、插件与运行维护文档。
---

# TwipsyBot

为 [Misskey](https://misskey-hub.net/) 提供一套轻量、可扩展的机器人运行方式。

## 开始使用

- [快速开始](user-guide/getting-started.md)
- [配置](user-guide/configuration.md)
- [功能](user-guide/features/README.md)
- [插件](user-guide/plugins/README.md)
- [运维](user-guide/operations.md)
- [故障排查](user-guide/troubleshooting.md)
- [配置参考](user-guide/reference/configuration.md)
- [开发](dev-guide/README.md)

## 能做什么

- 在 Misskey 中响应提及、私聊和群聊，并保留有限的对话上下文。
- 按计划自动发帖，也可以由管理员临时指定主题、可见性和联合范围。
- 使用白名单、黑名单、回复间隔和对话轮数控制访问。
- 订阅 Home、Local、Hybrid、Global 时间线或指定天线。
- 通过插件增加关键词回复、天线互动、主题与 RSS 发帖、图片理解和本地时间线观察。
- 使用 SQLite 保存运行状态，不需要额外部署数据库。

## 运行方式

TwipsyBot 只需要以下外部服务：

- 一个 Misskey 账号及其访问令牌。
- 一个可用的 OpenAI 兼容 API、密钥和模型名称。

Docker Compose 是生产运行的推荐方式。本地安装适合调试、插件调整或希望自行管理 Python 环境的用户。

## 使用前须知

- 建议创建独立的机器人账号，不要复用个人账号的访问令牌。
- 访问令牌和模型 API 密钥属于敏感信息，不要提交到 Git 仓库或发到公开日志中。
- 自动发帖、Radar 和 Iincho 会主动读取或发布内容。首次启用时应使用较低频率、较窄天线和 `local_only` 进行验证。
- OpenAI 兼容只表示请求协议兼容。文本、图片、视觉和内容审核能力仍取决于所选模型及服务端。
- 机器人行为需要遵守所在实例的使用规则和联邦规范。
