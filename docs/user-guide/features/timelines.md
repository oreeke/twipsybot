---
title: 时间线与天线
description: 配置 TwipsyBot 订阅 Misskey Home、Local、Hybrid、Global 时间线与天线。
---

# 时间线与天线

TwipsyBot 通过 Misskey Streaming API 接收实时帖子。

默认不订阅公共时间线，只有启用相关配置后才会接收。

## 时间线类型

| 配置 | Misskey 通道 | 适合用途 |
| --- | --- | --- |
| `home` | `homeTimeline` | 机器人主页时间线 |
| `local` | `localTimeline` | 本实例公开内容，Iincho 必需 |
| `hybrid` | `hybridTimeline` | 本地与关注内容的混合流 |
| `global` | `globalTimeline` | 联邦公开内容，事件量通常最大 |
| `antenna_ids` | `antenna` | 由实例天线规则筛选的内容，Radar 必需 |

同一帖子可能从多个已订阅通道出现。非必要不要同时打开多个宽泛时间线，以减少日志、网络和插件处理量。

## 使用天线

1. 登录机器人账号。
2. 在 Misskey 中创建天线并设置来源、关键词和排除条件。
3. 保存后，将天线 ID 或名称写入 `bot.timeline.antenna_ids`。
4. 重启 TwipsyBot。

```yaml
bot:
  timeline:
    antenna_ids:
      - "project-news"
```

天线决定 Radar 能看到哪些帖子。启用自动回复、转帖或反应前，应先把规则收窄并观察天线结果，避免对大量无关帖子自动互动。

## 插件依赖

- Radar 只处理 `antenna` 通道事件，不需要同时开启 Home、Local、Hybrid 或 Global。
- Iincho 只处理 `localTimeline`，必须设置 `bot.timeline.local: true`。
- KeyAct 和 Vision 处理提及与聊天，不依赖时间线订阅。
- Topics 在自动发帖任务中运行，不依赖时间线订阅。
