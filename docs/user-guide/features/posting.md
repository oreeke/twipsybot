---
title: 自动发帖与图片生成
description: 配置 TwipsyBot 轮转与定时发帖、管理员手动发帖和图片生成。
---

# 自动发帖与图片生成

## 自动发帖

`autopost` 有两种互斥模式，默认均关闭，共用 `visibility`、`local_only` 与 `prompt`：

| 模式 | 触发 | 每日上限 |
| --- | --- | --- |
| `rotation` | 启动约 1 分钟后首次执行，之后每 `interval` 一次 | 受 `daily_max` 限制，按主机本地日期重置 |
| `schedule` | `times` 中的每个时间点，按主机时区 | 不受限，不计数 |

- TUI 中开启其一会自动关闭另一个，修改即时生效。
- `times` 最多 24 个，相邻（含跨午夜）至少 5 分钟；`interval` 同样不少于 5 分钟。
- 每日上限用尽或缺少有效提示词时跳过本次。
- 提示词前会附加分钟级时间标记，减少 [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) 重复命中；要丰富主题请用 [Topics](../plugins/topics.md)。
- 插件一次返回多篇时间隔 10 秒发布，轮转模式下每篇都计入 `daily_max`。

建议先以低曝光试运行：

```yaml
autopost:
  schedule: true
  times: ["08:30", "12:00", "21:00"]
  visibility: home
  local_only: true
```

## 手动发帖

管理员私聊发送，选项置于主题前：

```text
/post 夏夜观星
/post -h 今天的维护通知
/post -f -l 仅关注者可见且不联合的通知
```

| 选项 | 作用 |
| --- | --- |
| `-p` · `-h` · `-f` | `public` · `home` · `followers` |
| `-l` | 不联合 |

缺省沿用 `autopost` 的可见性与 `local_only`，不计入每日计数。

## 图片生成

设置 `bot.image_model` 后，管理员可在私聊、群聊（需提及）或提及中使用：

```text
/img 雨后的未来城市车站，清晨，自然光
```

- 结果上传至机器人 Drive 并作为附件回复；Drive 文件不会自动清理。
- 仅在服务支持时填写 `image_size` 与 `image_quality`。
- 返回须为 JPEG、PNG 或 WebP，不超过 32 MiB。
