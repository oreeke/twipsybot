---
title: Misskey
description: 了解 TwipsyBot 使用的 Misskey REST API、Streaming Channel、权限与客户端约束。
---

# Misskey

TwipsyBot 通过 REST API 执行查询和写入，通过 Streaming API 接收实时事件。协议适配集中在 `twipsybot/clients/misskey`，业务代码应调用客户端方法，不应自行拼接 endpoint、认证参数或 WebSocket 控制消息。

插件不应直接导入内部客户端。需要访问 Misskey 时，应使用 `PluginContext.misskey` 提供的稳定服务接口。可用方法见[插件开发](plugins.md#misskey-与-drive)。

## REST API

| Endpoint | 用途 | 客户端方法 |
| --- | --- | --- |
| `i` | 获取机器人账号信息 | `get_current_user()` |
| `notes/show` | 获取帖子并校验回复可见性 | `get_note()` |
| `notes/create` | 发帖、回复、引用和转帖 | `create_note()`、`create_renote()` |
| `notes/delete` | 删除帖子 | `delete_note()` |
| `notes/reactions/create` | 添加反应 | `create_reaction()` |
| `users/notes` | 获取指定用户的帖子 | `get_user_notes()` |
| `antennas/list` | 获取天线并解析订阅配置 | `list_antennas()` |
| `chat/messages/create-to-user` | 向用户发送聊天消息 | `send_message()` |
| `chat/messages/create-to-room` | 向房间发送聊天消息 | `send_room_message()` |
| `chat/messages/user-timeline` | 获取与用户的聊天记录 | `get_messages()` |
| `chat/messages/room-timeline` | 获取房间聊天记录 | `get_room_messages()` |
| `drive/files/show` | 获取 Drive 文件信息 | `drive.show_file()` |
| `drive/files/create` | 上传文件 | `drive.upload_bytes()` |

所有 REST 请求共享最多 32 个并发槽位，单次请求超时为 60 秒。读取类请求会对连接错误和限流执行最多 2 次带随机抖动的指数退避重试。写入类请求默认不重试，避免发帖、聊天或反应被重复提交。新增调用时应继续通过 `MisskeyAPI` 或 `MisskeyDrive` 复用这些约束和 HTTP 会话。

帖子文本最长 3000 字符，聊天文本最长 2000 字符，客户端会截断超长内容。回复遵循原贴可见性。

## Streaming API

客户端始终连接 `main` Channel，并按配置连接以下 Channel：

| Channel | 事件用途 |
| --- | --- |
| `main` | 提及、回复、通知和新聊天消息 |
| `homeTimeline` | Home 时间线帖子 |
| `localTimeline` | Local 时间线帖子 |
| `hybridTimeline` | Hybrid 时间线帖子 |
| `globalTimeline` | Global 时间线帖子 |
| `antenna` | 指定天线的帖子 |
| `chatUser` | 指定用户的聊天消息 |

Streaming 客户端负责断线重连和 Channel 恢复，使用多个 worker 处理事件，并通过有限队列提供背压。队列拥塞时会丢弃无法及时入队的事件，断线期间使用有限缓冲保存待发送消息，缓冲溢出时丢弃最早消息。近期事件 ID 会短期缓存以避免重复处理。

新增事件时，应先在客户端层规范化不同 Channel 的 payload，再由 flow 转换为业务行为或插件事件。不要让下游代码依赖原始 WebSocket 包装结构。

## 权限

机器人令牌按启用能力授予最小权限：

| 权限 | 使用场景 |
| --- | --- |
| `read:account` | 读取账号、时间线和天线 |
| `write:notes` | 回复、发帖、引用和转帖 |
| `read:chat` | 读取聊天消息 |
| `write:chat` | 发送聊天消息 |
| `read:drive` | 读取文件信息和内容 |
| `write:drive` | 上传文件 |
| `write:reactions` | 添加反应 |

新增 API 能力时，应同步检查示例配置、快速开始中的权限说明和实际最小权限。

## 错误与测试

REST 客户端将错误区分为参数错误、认证错误、限流和连接错误。上层业务代码应保留这些错误语义，不要统一吞掉异常或无条件重试。Streaming 断线由客户端恢复，事件处理失败不应终止其余 worker。

自动化测试不得依赖真实 Misskey 实例。REST 行为通过模拟 HTTP 响应验证，Streaming 行为通过构造事件和连接替身验证。新增 endpoint 或 Channel 时，至少覆盖成功响应、认证或参数失败、连接失败，以及会产生重复副作用的重试边界。

## API 版本

项目代码和文档基于 Misskey API `2026.9.0`，并随官方更新同步维护。
