---
title: Radar 天线互动
description: 使用 Radar 对 Misskey 天线帖子添加反应、回复、引用或转帖。
---

# Radar：天线互动

Radar 只处理指定 Misskey 天线收到的帖子，可以依次添加反应、回复，并选择引用或转帖。

## 启用前准备

1. 使用机器人账号在 Misskey 中创建天线。
2. 设置较窄的来源、关键词和排除条件。
3. 将天线 ID 或名称写入 `timeline.antennas`。
4. 重启机器人并先观察天线内容，确认不会匹配大量无关帖子。

## 配置

通过 `twipsybot cfg` 配置 Radar，或在 `data/settings.yaml` 中写入：

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

默认优先级为 `50`，通常无需填写。`reply_ai_prompt` 和 `quote_ai_prompt` 有内置默认值；需要自定义时支持 `{content}`。`quote_visibility` 与 `renote_visibility` 默认为 `null`，表示复用目标可见性。

## 动作规则

- `reaction` 留空表示不添加反应，可使用实例支持的名称或自定义表情格式。
- `reply_text` 支持 `{username}`。有固定文本时优先于 `reply_ai`。
- `reply_ai` 只在开启回复且 `reply_text` 为空时调用模型。
- `quote_text` 与 `quote_ai` 的关系相同。
- 引用成功后不会再执行普通转帖；引用没有生成有效文本时，仍可继续转帖。
- Radar 会跳过机器人自己的帖子和已有 `myReaction` 的反应动作。
- 互动会在收到帖子后随机延迟 3–5 分钟执行；等待中的帖子最多 100 条，超出部分跳过。

`visibility` 支持 `public`、`home`、`followers`。`local_only` 控制对应回复、引用或转帖是否联合。

## 安全启用示例

第一次运行建议只添加反应：

```yaml
plugins:
  radar:
    enabled: true
    reaction: "heart"
    reply: false
    renote: false
    quote: false
```

确认天线规则准确后，再逐项增加回复或转帖。自动互动应遵守实例规则，并避免对同一主题进行高频、重复操作。
