---
title: Vision 图片理解
description: 使用 Vision 和多模态模型理解 Misskey 提及或聊天中的图片。
---

# Vision：图片理解

Vision 读取提及或聊天中的图片和提问，发送给支持多模态输入的模型，之后回复用户。

## 配置

```yaml
vision:
  enabled: true
  priority: 900
  max_images: 1
  max_bytes: 6MB
  use_thumbnail: false
  default_prompt: |-
    请描述图片内容。
```

- `max_images`：一次最多处理的附件数量，至少为 1。
- `max_bytes`：单张图片下载上限，支持 `KB`、`MB` 等大小单位。
- `use_thumbnail`：优先使用缩略图，可降低流量和延迟，但会损失细节。
- `default_prompt`：用户只发送图片时使用的问题；留空则仅向模型发送图片。

## 使用

在提及、私聊或群聊中发送图片，可以附带要求：

```text
请提取图片中的文字
这张图中的设备可能是什么？
概括这张图表表达的趋势
```

没有图片、下载失败或附件不是图片时，Vision 不接管消息，后续插件或默认 AI 可以继续处理。

## 模型兼容

文本模型可用不代表支持图片输入。启用前应在当前 `api_mode` 下确认模型支持 OpenAI 兼容的多模态消息格式。图片会由机器人从 Misskey Drive 下载后以内嵌数据发送到模型服务，因此还应考虑服务提供商的数据处理规则。

处理高分辨率图片时，可开启缩略图或降低 `max_images`。需要识别细小文字时则应优先使用原图。
