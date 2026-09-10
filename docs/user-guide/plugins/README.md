---
title: 插件
description: 配置 TwipsyBot 的关键词回复、天线互动、RSS 发帖、图片理解和时间线观察插件。
---

# 插件

TwipsyBot 自带几个可选插件。插件默认关闭，可以在 `plugins/config.yaml` 中集中管理。

| 插件 | 用途 | 依赖 |
| --- | --- | --- |
| [Iincho](iincho.md) | 汇总本地时间线趋势和风险信号 | Local Timeline、Moderations API |
| [KeyAct](keyact.md) | 精确关键词直接回复 | 提及或聊天 |
| [Radar](radar.md) | 对天线帖子反应、回复、转帖或引用 | Misskey 天线 |
| [Topics](topics.md) | 为自动发帖提供 TXT 主题或 RSS | 自动发帖 |
| [Vision](vision.md) | 理解提及和聊天中的图片 | 多模态模型 |

## 配置规则

每个插件都包含通用字段：

```yaml
plugin_name:
  enabled: true
  priority: 100
```

- `enabled` 决定是否加载。
- `priority` 越大越先执行。
- 修改插件配置后需要重启机器人。
- `plugins/config.yaml` 中的条目会完整取代插件目录配置，不会合并。

KeyAct 和 Vision 会处理消息或提及。一个插件返回结果后，后续插件和默认 AI 不再处理同一事件。Radar 和 Iincho 观察时间线事件，不会互相截断。Topics 只在自动发帖任务中运行。

## 推荐启用顺序

1. 先保持所有插件关闭，验证普通提及和聊天。
2. 需要固定问答时启用 KeyAct。
3. 需要图片理解时启用 Vision，并验证模型支持多模态。
4. 需要内容来源时启用 Topics。
5. 最后配置 Radar 或 Iincho，并先缩小时间线范围。
