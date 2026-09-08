---
title: 提及、聊天与访问控制
description: 配置 TwipsyBot 的 Misskey 提及、聊天上下文、回复限制和用户访问名单。
---

# 提及、聊天与访问控制

## 提及和聊天

`bot.response.mention` 控制公开或半公开帖子中的 `@提及`，`bot.response.chat` 控制 Misskey 私聊和群聊。两项可以独立关闭。

处理提及时，机器人会在生成前和发送回复前确认原帖仍可读取。原帖已删除或无法确认时，跳过本次回复。

聊天支持有限的历史上下文，`chat_memory` 控制最多读取的历史消息条数，取值范围为 `0` 到 `100`；`chat_context_tokens` 控制其中实际提交给模型的 token 数。两项限制同时生效，较大的值能保留更多上下文，也会增加模型输入和调用成本。机器人重启不会删除 Misskey 中的聊天记录。

## 回复限制

限制以 Misskey 用户 ID 为单位持久化到 SQLite：

- `rate_limit` 防止同一用户过于频繁地触发回复。
- `max_turns` 限制累计机器人回复次数。
- `max_turns_release` 设置轮数限制后的恢复时间。

被限流时，机器人发送 `rate_limit_reply`；达到轮数上限时发送 `max_turns_reply`。限制提示本身不会增加对话轮数，但会更新最近回复时间。

如果 `max_turns_release: -1`，达到上限的用户会被加入持久化黑名单。需要管理员通过 `^blacklist del <用户>` 或 `^blacklist reset` 解除。

## 白名单与黑名单

白名单用于信任的用户或管理员，使其不受回复间隔和轮数限制。黑名单完全阻止普通 AI 回复。两者都支持：

- Misskey 用户 ID
- `username@host`
- 带前导 `@` 的完整账号

推荐优先使用用户 ID。用户名匹配不区分大小写。

管理员命令权限由独立的 `bot.admin.allowed_users` 控制。进入回复白名单不会自动获得管理权限。

## 插件处理顺序

KeyAct 和 Vision 可以在默认 AI 之前处理提及或聊天。插件返回回复后，后续插件和默认 AI 不再处理同一个事件。优先级较高的插件先执行，因此默认配置中 KeyAct 会先匹配明确关键词，Vision 随后处理图片，其余内容才进入普通 AI 回复。

## 建议配置

公开机器人可以从以下策略开始：

```yaml
bot:
  response:
    mention: true
    chat: true
    chat_memory: 10
    chat_context_tokens: 2000
    rate_limit: 30s
    max_turns: 30
    max_turns_release: 1d
    whitelist:
      - "your-admin-user-id"
    blacklist: []
```

先观察实际调用量和社区使用方式，再调整间隔与轮数。
