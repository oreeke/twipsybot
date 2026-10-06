---
title: Vision 图片理解
description: 使用 Vision 和多模态模型理解 Misskey 提及或聊天中的图片。
---

# Vision：图片理解

将提及或聊天中的图片与提问交给多模态模型并回复。

## 配置

```yaml
plugins:
  vision:
    enabled: true
    max_images: 3
    max_bytes: 6MB
    use_thumbnail: true
```

| 字段 | 说明 |
| --- | --- |
| `max_images` | 单次最多处理图片数，至少 1 |
| `max_bytes` | 单张下载上限，支持 `KB`、`MB` 等单位 |
| `use_thumbnail` | 优先缩略图，节省流量与延迟但损失细节 |
| `default_prompt` | 发图片时附带的提示，留空只发送图片 |

## 使用

在提及、私聊或群聊（需提及）中发送图片，可附带要求，如“提取图中文字”。无图片、下载失败或附件非图片时不接管，交由后续插件或默认 AI。

## 兼容性

- 确认模型在当前 `bot.api_mode` 下支持 OpenAI 兼容的多模态消息。
- 图片从 Drive 下载后内嵌发送至模型服务，注意服务商的数据处理规则。
- 识别细小文字时关闭 `use_thumbnail`；高分辨率图片较多时可降低 `max_images`。
