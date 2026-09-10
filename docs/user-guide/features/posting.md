---
title: 自动发帖与图片生成
description: 配置 TwipsyBot 定时发帖、管理员手动发帖和图片生成命令。
---

# 自动发帖与图片生成

## 自动发帖

启用 `bot.auto_post.enabled` 后，TwipsyBot 按 `interval` 运行发帖任务。默认提示词会与系统提示词一起交给文本模型。Topics 插件启用后，可以为任务提供 TXT 主题或 RSS 内容。

机器人每次启动或重启后，首次自动发帖任务会在约 1 分钟后执行，之后再按 `interval` 运行。如果自动发帖未启用、每日上限已经用完或缺少有效提示词，本次任务会跳过。

TwipsyBot 会在自动发帖提示词前加入分钟级时间标记，减少 [Prompt caching](https://platform.openai.com/docs/guides/prompt-caching) 重复命中。时间标记不能代替内容来源。希望帖子主题更丰富时，应配置 [Topics](../plugins/topics.md)。

每日计数保存在 SQLite，并按主机本地日期重置。插件一次返回多篇内容时，每篇都计入 `max_posts_per_day`，帖子之间间隔 10 秒。

建议首次配置：

```yaml
bot:
  auto_post:
    enabled: true
    interval: 6h
    max_posts_per_day: 4
    visibility: "home"
    local_only: true
    prompt: |-
      生成一篇有趣、有见解的社交媒体帖子。
```

确认内容和频率合适后，再决定是否设为 `public` 或允许联合。

## 手动发帖

授权管理员可以在与机器人的私聊中使用：

```text
/post 发一篇关于夏夜观星的短文
/post -h 写一条今天的维护通知
/post -f -l 写一条仅关注者可见且不联合的通知
```

选项必须放在主题前：

| 选项 | 作用 |
| --- | --- |
| `-p` | `public` 可见性 |
| `-h` | `home` 可见性 |
| `-f` | `followers` 可见性 |
| `-l` | 禁止联合，仅本地显示 |

未指定选项时使用自动发帖的可见性和 `local_only` 配置。`/post` 只在私聊中执行，不计入自动发帖的每日计数。

## 图片生成

设置 `openai.image_model` 后，授权管理员可以发送：

```text
/img 雨后的未来城市车站，清晨，自然光
```

`/img` 可用于私聊、群聊或提及。生成结果会上传到机器人账号的 Misskey Drive，并作为回复附件发送。

注意事项：

- `image_size` 和 `image_quality` 只有在对应服务支持时才填写。
- 返回图片必须是 JPEG、PNG 或 WebP，单个生成结果不能超过 32 MiB。
- 文件会保留在机器人 Drive 中，TwipsyBot 不会自动清理，需结合实例容量定期管理。
- 文本模型可用不代表图片模型可用，两者应分别验证。
