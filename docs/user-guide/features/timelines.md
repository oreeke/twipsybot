---
title: 时间线与天线
description: 配置 TwipsyBot 订阅 Misskey 时间线与天线。
---

# 时间线与天线

默认不订阅任何时间线，`timeline.*` 修改后需重启。

| 配置 | 通道 | 说明 |
| --- | --- | --- |
| `timeline.home` | `homeTimeline` | 机器人主页 |
| `timeline.local` | `localTimeline` | 本实例公开内容，Iincho 必需 |
| `timeline.hybrid` | `hybridTimeline` | 本地与关注内容 |
| `timeline.global` | `globalTimeline` | 联邦公开内容，事件量最大 |
| `timeline.antennas` | `antenna` | 天线筛选内容，Radar 必需 |

同一帖子可能从多个通道到达，按需开启以减少流量与日志。

## 天线

用机器人账号在 Misskey 中创建天线，填入 ID 或名称后重启：

```yaml
timeline:
  antennas: ["project-news"]
```

天线决定 Radar 的作用范围，启用自动互动前先收窄规则并观察结果。

## 插件依赖

| 插件 | 事件源 |
| --- | --- |
| Radar | 仅 `antenna` |
| Iincho | 仅 `localTimeline` |
