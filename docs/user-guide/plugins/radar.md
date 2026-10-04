---
title: Radar 天线互动
description: 使用 Radar 对 Misskey 天线帖子添加反应、回复、引用或转帖。
---

# Radar：天线互动

对指定天线中的帖子依次执行反应、回复、引用或转帖，仅处理 `antenna` 通道。

启用前用机器人账号创建规则较窄的天线，写入 `timeline.antennas` 并重启，先观察匹配结果。

## 配置

```yaml
plugins:
  radar:
    enabled: true
    reaction: "heart"
    reply: false
    reply_text: "谢谢分享，{username}"
    reply_ai: false
    reply_local_only: false
    renote: false
    renote_local_only: false
    quote: false
    quote_text: ""
    quote_ai: false
    quote_local_only: false
```

## 动作规则

- `reaction` 留空不反应，支持实例表情名或自定义表情格式；已有 `myReaction` 时跳过。
- 回复：`reply_text` 优先（支持 `{username}`），为空且开启 `reply_ai` 时由模型生成。引用的 `quote_text` 与 `quote_ai` 同理。
- `reply_ai_prompt`、`quote_ai_prompt` 有内置默认值，支持 `{content}`。
- 引用成功后不再转帖；引用未生成有效文本时仍可转帖。
- `quote_visibility`、`renote_visibility` 支持 `public` / `home` / `followers`，默认沿用原帖；`*_local_only` 控制是否联合。
- 收到帖子后随机延迟 3–5 分钟执行，待处理上限 100 条，超出跳过；跳过机器人自身帖子。

::: tip 稳妥启用
首次只开 `reaction`，确认天线准确后再逐项增加回复、引用或转帖，避免对同一主题高频重复互动。
:::
