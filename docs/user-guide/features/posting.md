---
title: 自动发帖
description: 配置 TwipsyBot 轮转与定时发帖及管理员手动发帖。
---

# 自动发帖

## 自动发帖

`autopost.mode` 取 `off`、`rotation`、`schedule`，共用 `visibility`、`local_only`、`prompt`：

| 模式 | 触发 | 每日上限 |
| --- | --- | --- |
| `rotation` | 启动约 1 分钟后首次执行，之后每 `interval` 一次 | 受 `daily_max` 限制，按主机本地日期重置 |
| `schedule` | `times` 中的每个时间点，按主机时区 | 不受限，不计数 |

- `times` 最多 24 个，相邻（含跨午夜）至少 5 分钟；`interval` 同样不少于 5 分钟。
- 每日上限用尽或缺少有效提示词时跳过本次；要丰富主题请用 [Topics](../plugins/topics.md)。
- 插件一次返回多篇时间隔 10 秒发布，轮转模式下每篇都计入 `daily_max`。

建议先以低曝光试运行：

```yaml
autopost:
  mode: schedule
  times: ["08:30", "12:00", "21:00"]
  visibility: home
  local_only: true
```

## 手动发帖

管理员说明，由机器人撰写并发布，如「发一条关于夏夜观星的帖子，仅主页可见、不联合」。

缺省沿用 `autopost` 的可见性与 `local_only`，不计入每日计数。

### 定时发布

说明时间，如「明早 8 点发一篇……」。可让机器人列出待发布的帖子，或按编号取消。

- 时间已过则登记失败。
- 待发布的帖子只保存在内存中，重启后丢失；错过 5 分钟则不再发布。
