---
title: Topics 主题与 RSS 发帖
description: 使用 Topics 从 TXT 主题或 RSS 订阅为 TwipsyBot 自动发帖提供内容。
---

# Topics：主题与 RSS 发帖

Topics 在自动发帖任务触发时提供内容源。

它不会创建独立定时器，运行间隔和每日上限仍由 `bot.auto_post` 控制。

## TXT 主题

```yaml
topics:
  enabled: true
  priority: 100
  source: "txt"
  txt_start_line: 1
  txt_ai_prefix: |-
    以{topic}为主题，
```

将主题逐行写入 `plugins/topics/topics.txt`：

```text
开源软件的长期维护
城市夜间公共交通
```

插件按顺序循环读取非空行，并通过 `{topic}` 将主题加入自动发帖提示词。

使用 TXT 来源时，`txt_ai_prefix` 不能为空。

`txt_start_line` 只在插件尚无保存状态时决定起始行。之后的读取位置保存在 SQLite 中，修改该值不会重置现有进度。

## RSS 来源

```yaml
topics:
  enabled: true
  source: "rss"
  rss_list:
    - "https://example.com/feed.xml"
    - "https://example.com/rss"
  rss_post_mode: "rotate"
  rss_ai: false
  rss_ai_prefix: |-
    发表一段感想和相关知识，不超过150字。
    不加链接，不加引号：

    {summary}

    {title}
    {link}
```

两种发送模式：

- `batch`：每轮从每个 RSS 选择一条未发布的最新内容。
- `rotate`：每轮只从一个 RSS 发布一条，并在订阅源之间轮换。

插件记录近期条目标识，发布成功后才标记，避免重复。RSS 请求总超时为 60 秒，每个订阅源最多检查前 20 条。

`rss_ai: false` 时发布摘要或标题和原文链接；开启后，模型根据 `{summary}`、`{title}`、`{link}` 生成一段文本，帖子仍会附带链接，此时 `rss_ai_prefix` 不能为空。RSS 至少应提供标题和链接。

## 使用建议

- 多个订阅源优先使用 `rotate`，发帖节奏更平缓。
- RSS 内容由外部站点提供，应确认来源可靠并遵守转载规范。
- `batch` 中每篇都计入每日上限。上限耗尽后本轮剩余内容不会发布。
- 修改 TXT 起点或清理 RSS 历史前先备份 SQLite 数据库。
