---
title: 插件
description: TwipsyBot 内置插件：关系记录、关键词回复、文生图、附件理解、联网检索、RSS 发帖、天线互动与时间线观察。
---

# 插件

内置插件默认关闭，在 `twipsybot cfg` 的 `PLUGINS` 区或 `plugins.<name>` 中启用。

| 插件 | 用途 | 依赖 |
| --- | --- | --- |
| [**Bond**](bond.md) | 用户关系记录与档案 | 提及、聊天、通知 |
| [**KeyAct**](keyact.md) | 精确关键词回复 | 提及、聊天 |
| [**T2X**](t2x.md) | 文本生成图片 | 图片生成 API |
| [**MLLM**](mllm.md) | 附件理解 | 多模态模型 |
| [**Web**](web.md) | 联网检索与链接读取 | 检索后端（SearXNG / DeepSeek） |
| [**Topics**](topics.md) | 主题列表与 RSS 发帖 | 自动发帖 |
| [**Radar**](radar.md) | 天线帖子互动 | 天线 |
| [**Iincho**](iincho.md) | 本地时间线风险概览 | Local 时间线、审核 API |

## 通用字段

```yaml
plugins:
  keyact:
    enabled: true
```

配置变更自动重载插件。
