---
title: 插件
description: TwipsyBot 内置插件：关键词回复、图片理解、联网检索、RSS 发帖、天线互动与时间线观察。
---

# 插件

内置插件默认关闭，在 `twipsybot cfg` 的 `PLUGINS` 区或 `plugins.<name>` 中启用。

| 插件 | 用途 | 依赖 | 优先级 |
| --- | --- | --- | --- |
| [**KeyAct**](keyact.md) | 精确关键词回复 | 提及、聊天 | 850 |
| [**Vision**](vision.md) | 图片理解 | 多模态模型 | 750 |
| [**Web**](web.md) | 联网检索与链接读取 | 检索后端（SearXNG） | 500 |
| [**Topics**](topics.md) | TXT 主题与 RSS 发帖 | 自动发帖 | 300 |
| [**Radar**](radar.md) | 天线帖子互动 | 天线 | 50 |
| [**Iincho**](iincho.md) | 本地时间线风险概览 | Local 时间线、审核 API | 40 |

## 通用字段

```yaml
plugins:
  keyact:
    enabled: true
    priority: 850
```

`priority` 可省略，越大越先执行。配置变更自动重载插件，也可发送 `^reload <插件名>`。

## 执行顺序

- 提及与聊天按优先级交给 KeyAct、Vision；任一插件返回回复即终止，后续插件与默认 AI 不再处理。
- Web 不接管回复，仅在默认 AI 回复前补充检索资料。
- Radar 与 Iincho 仅观察时间线，互不截断。
- Topics 只在自动发帖时运行。

建议先验证普通回复，再按需启用 KeyAct → Vision → Web → Topics，最后在收窄时间线范围后启用 Radar 或 Iincho。
