---
title: Misskey
description: TwipsyBot 使用的 Misskey REST API、Streaming Channel、权限与客户端约束。
---

# Misskey

REST 负责查询与写入，Streaming 接收实时事件，协议适配集中在 `twipsybot/clients/misskey`。业务代码调用客户端方法，不自行拼接 endpoint、认证参数或 WebSocket 消息；插件使用 [`PluginContext.misskey`](plugins.md#misskey-与-drive)。

基于 Misskey API `2026.9.0`，随官方更新同步维护。

## REST

| Endpoint | 用途 | 客户端方法 |
| --- | --- | --- |
| `i` | 机器人账号信息 | `get_current_user()` |
| `notes/show` | 获取帖子 | `get_note()` |
| `notes/create` | 发帖、回复、引用、转帖 | `create_note()`、`create_renote()` |
| `notes/delete` | 删除帖子 | `delete_note()` |
| `notes/reactions/create` | 添加反应 | `create_reaction()` |
| `users/notes` | 用户帖子 | `get_user_notes()` |
| `antennas/list` | 天线列表 | `list_antennas()` |
| `chat/messages/create-to-user` | 发送私聊 | `send_message()` |
| `chat/messages/create-to-room` | 发送群聊 | `send_room_message()` |
| `chat/messages/user-timeline` | 私聊记录 | `get_messages()` |
| `chat/messages/room-timeline` | 群聊记录 | `get_room_messages()` |
| `drive/files/show` | 文件信息 | `drive.show_file()` |
| `drive/files/create` | 上传文件 | `drive.upload_bytes()` |
| 文件 URL（`GET`） | 下载文件 | `drive.fetch_bytes()`、`drive.download_bytes()` |

- 共享 32 并发槽位，单次超时 60 秒，统一经 `MisskeyAPI` / `MisskeyDrive` 复用会话与约束。
- 读请求对连接错误与限流最多重试 2 次（指数退避加抖动）；写请求不重试，避免重复提交。
- 帖子最长 3000 字符、聊天 2000 字符，客户端统一安全截断。
- 可见性、联合范围与权限由服务端校验收敛；指定可见帖子按事件原可见性回复。

## Streaming

| Channel | 事件 |
| --- | --- |
| `main` | 提及、回复、通知与新聊天消息（始终连接） |
| `homeTimeline` · `localTimeline` · `hybridTimeline` · `globalTimeline` | 对应时间线帖子 |
| `antenna` | 指定天线帖子 |
| `chatUser` · `chatRoom` | 指定用户 / 房间的聊天消息 |

- 自动断线重连与 Channel 恢复，动态聊天订阅以 `pong` 确认；单连接最多 32 个 Channel。
- 多 worker 加有限队列背压：拥塞时丢弃无法入队的事件；断线期间待发消息有限缓冲，溢出时丢弃最早消息。
- 同一 Channel 内按事件 ID 短期去重。
- 新增事件时先在客户端层规范化 payload，再由 flow 转为业务行为或插件事件，下游不依赖原始 WebSocket 结构。

## 权限

| 权限 | 场景 |
| --- | --- |
| `read:account` | 账号、时间线、天线 |
| `write:notes` | 回复、发帖、引用、转帖 |
| `read:chat` · `write:chat` | 读写聊天 |
| `read:drive` · `write:drive` | 读取、上传文件 |
| `write:reactions` | 添加反应 |
| `write:account` | 写入个人备注（`users/update-memo`） |

`users/show` 无需特定权限，但须携带令牌才会返回 `isFollowed`、`memo` 等相对于机器人的字段。

新增 API 能力时，同步更新[快速开始](../user-guide/getting-started.md)的权限表并保持最小权限。

## 错误与测试

- 错误分为参数、认证、权限、未找到、文件过大、限流与连接，保留 `status`、`code`、`retry_after`；上层不吞异常、不无条件重试。
- 单个事件处理失败不终止其他 worker。
- 测试不依赖真实例：REST 模拟 HTTP 响应，Streaming 构造事件与连接替身。新增 endpoint 或 Channel 至少覆盖成功、认证或参数失败、连接失败，以及有副作用的重试边界。
