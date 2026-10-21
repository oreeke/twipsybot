---
title: 对话与限制
description: 配置 TwipsyBot 的提及与聊天回复、上下文、回复限制和用户名单。
---

# 对话与限制

## 对话

- `reply.mention` 控制帖子中的 `@提及`，`reply.chat` 控制私聊与群聊。
- `reply.ignore_bots` 忽略 Bot 账号的提及与聊天，避免机器人互相回复。
- 群聊消息须 `@` 机器人，才会触发回复、插件或 `/img`。
- 以 `/` 开头的消息（含插件命令）仅关注机器人的用户与 `bot.admins` 可用，其他用户的命令静默忽略；关注状态缓存 60 秒，查询失败视为未关注。`^` 管理命令不受影响。
- 提及在生成前与发送前都会确认原帖可读，已删除则跳过。
- 上下文由 `reply.memory`（条数，0–100）与 `reply.ctx_tokens`（token 预算）共同限制；私聊按用户、群聊按房间共享。
- 帖子中的回复会读取回复链：以 `reply.memory` 为上限取最近的祖先帖子，保留其中最远一条（通常是主帖）和直接父帖，其余只保留机器人与当前用户的发言，再按 `ctx_tokens` 截断；读取失败或 `memory` 为 `0` 时仅使用直接父帖。回复链作为文字记忆；直接父帖和被引用帖的附件、链接仍由 MLLM、Web 读取，链上更早的帖子不读取。

## 限制

按用户 ID 持久化于 SQLite：

| 字段 | 作用 |
| --- | --- |
| `rate_limit` | 两次回复的最短间隔，触发时发送 `rate_limit_msg` |
| `max_turns` | 累计回复次数上限，触发时发送 `max_turns_msg`；`-1` 不计数 |
| `turns_release` | 达上限后的恢复时间；`-1` 则拉黑并写入设置 |

提示文案留空则静默。提示不计入轮数，但会刷新最近回复时间。被自动拉黑的用户需通过 `^blacklist del` 解除。

## 名单

- 白名单与 `bot.admins` 豁免间隔和轮数限制；黑名单禁用普通 AI 回复。
- 支持用户 ID、`username@host` 或 `@username@host`；推荐用户 ID，用户名不区分大小写。
- 白名单不授予管理权限，管理权限仅由 `bot.admins` 决定。

## 示例

```yaml
reply:
  rate_limit: 30s
  max_turns: 30
  turns_release: 1d
  whitelist: ["trusted-user-id"]
```
