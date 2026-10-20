---
title: 插件
description: TwipsyBot 内置插件：关系记录、关键词回复、文生图、附件理解、联网检索、RSS 发帖、天线互动与时间线观察。
---

# 插件

内置插件默认关闭，在 `twipsybot cfg` 的 `PLUGINS` 区或 `plugins.<name>` 中启用。

| 插件 | 用途 | 依赖 | 优先级 |
| --- | --- | --- | --- |
| [**Bond**](bond.md) | 用户关系记录与档案 | 提及、聊天、通知 | 900 |
| [**KeyAct**](keyact.md) | 精确关键词回复 | 提及、聊天 | 850 |
| [**T2X**](t2x.md) | 文本生成图片 | 图片生成 API | 800 |
| [**MLLM**](mllm.md) | 附件理解 | 多模态模型 | 750 |
| [**Web**](web.md) | 联网检索与链接读取 | 检索后端（SearXNG） | 500 |
| [**Topics**](topics.md) | 主题列表与 RSS 发帖 | 自动发帖 | 300 |
| [**Radar**](radar.md) | 天线帖子互动 | 天线 | 50 |
| [**Iincho**](iincho.md) | 本地时间线风险概览 | Local 时间线、审核 API | 40 |

## 通用字段

```yaml
plugins:
  keyact:
    enabled: true
    priority: 850
```

`priority` 可省略，越大越先执行。配置变更自动重载插件。

## 执行顺序

- Bond 先于其他插件记录互动，并在 AI 回复前补充关系档案。
- KeyAct 任一返回回复即终止，后续插件与 AI 不再处理。
- T2X 匹配 `/img` 即接管回复，不再交给 LLM。
- Web、MLLM 不接管回复，仅在 AI 回复前补充检索资料与附件。
- Radar 与 Iincho 仅观察时间线，互不截断。
- Topics 只在自动发帖时运行。
