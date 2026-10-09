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
    delay: ""
    reaction: "heart"
    reply: false
    reply_text: "谢谢分享，{username}"
    reply_ai: false
    reply_visibility: ""
    reply_local_only: false
    repeat: off
    renote_visibility: ""
    renote_local_only: false
    quote_text: ""
    quote_ai: false
    quote_visibility: ""
    quote_local_only: false
```

## 动作规则

- `reaction` 留空不反应，支持实例表情名或自定义表情格式；已有 `myReaction` 时跳过。
- 回复：`reply_text` 优先（支持 `{username}`），为空且开启 `reply_ai` 时由模型生成。引用的 `quote_text` 与 `quote_ai` 同理。
- `reply_ai_prompt`、`quote_ai_prompt` 留空使用内置提示，支持 `{content}`。
- `repeat`：`off` / `renote` / `quote`；引用无有效文本或失败时退回转帖。
- `*_visibility` 支持 `public` / `home` / `followers`，`*_local_only` 控制是否联合；原帖为 `specified` 时回复保持 `specified`。
- `delay` 留空时收到帖子后随机延迟 3–5 分钟执行；填写后按精确时间延迟，支持 `m` / `h` / `d` 及组合（如 `30m`、`2h`、`1h30m`、`1d`），范围 `1m`–`1d`。
- 执行前重新获取帖子，已删除或不可见时跳过。
- 待处理上限 100 条，超出跳过；跳过机器人自身帖子。

::: warning 长延时
待处理任务仅保存在内存中，重启或重载插件会丢弃尚未执行的帖子；延时越长，丢失越多，且待处理更易达到上限而跳过新帖。使用长延时时请收窄天线规则。
:::

::: tip 稳妥启用
首次只开 `reaction`，确认天线准确后再逐项增加回复、引用或转帖，避免对同一主题高频重复互动。
:::
