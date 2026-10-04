---
title: Topics 主题与 RSS 发帖
description: 使用 Topics 从 TXT 主题或 RSS 订阅为 TwipsyBot 自动发帖提供内容。
---

# Topics：主题与 RSS 发帖

在自动发帖触发时提供内容源，无独立定时器，运行时机与上限遵循 `autopost`。

## TXT

```yaml
plugins:
  topics:
    enabled: true
    source: txt
    txt_start_line: 1
```

`prompts/topics.txt` 每行一个主题，按顺序循环，经 `{topic}` 注入提示词（`txt_ai_prefix` 有内置默认值）：

```text
开源软件的长期维护
城市夜间公共交通
```

`txt_start_line` 仅在无保存进度时生效，此后进度保存在 SQLite。

## RSS

```yaml
plugins:
  topics:
    enabled: true
    source: rss
    rss_list: |-
      # 科技
      https://example.com/feed.xml
      https://example.com/rss
    rss_post_mode: rotate
    rss_ai: false
```

- `rss_list` 每行一个地址。
- `rss_post_mode`：`rotate`（默认）每轮从一个源发一条并轮换；`batch` 每轮从每个源各发一条最新未发布内容。
- `rss_ai: false` 发布摘要或标题加链接；开启后由模型基于摘要、标题与链接改写，仍附链接。`rss_ai_prefix` 支持 `{summary}`、`{title}`、`{link}`。
- 条目需含标题与链接；发布成功后才记录标识，避免重复。
- 请求总超时 60 秒，每个源检查前 20 条。

## 建议

- 多源优先 `rotate`，节奏更平缓；`batch` 每篇计入 `daily_max`，耗尽后本轮剩余不发。
- 确认来源可靠并遵守转载规范。
- 调整 TXT 进度或清理 RSS 历史前先备份数据库。
