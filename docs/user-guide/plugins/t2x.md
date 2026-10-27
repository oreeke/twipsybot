---
title: T2X 文本生成多媒体
description: 使用 T2X 让 TwipsyBot 调用 OpenAI 兼容接口生成多媒体。
---

# T2X：文本生成多媒体

调用 OpenAI 兼容的图片接口，上传到机器人 Drive 并作为附件回复。目前仅支持图片。

## 配置

```yaml
plugins:
  t2x:
    enabled: true
    image:
      model: gpt-image-2
      size: 1024x1024
      quality: high
```

| 字段 | 说明 |
| --- | --- |
| `image.base_url` | 专用端点，留空沿用 `connect.ai_base_url` |
| `image.api_key` | 专用密钥，留空沿用 `connect.ai_api_key` |
| `image.model` | 图片模型，必填；为空时工具不可用 |
| `image.size` | 透传给图片 API，不校验取值，留空不传 |
| `image.quality` | 同上 |

设置 `image.base_url` 时必须同时设置 `image.api_key`，避免把主密钥发往其他服务。`image.api_key` 存于 `data/secrets.yaml`：

```yaml
plugins:
  t2x:
    image:
      api_key: "sk-..."
```


## 使用

提供管理员专用的 `t2x_draw` [工具](../features/interactions.md#工具)，场景为私聊、群聊（需提及）与提及：

```text
画一张雨后的未来城市车站，清晨，自然光
```

也可用 `/draw <描述>` 强制调用。

- 机器人先回复已开始生成，完成后补发图片，失败则补发 `Image generation failed.`。Drive 文件不会自动清理。
- 引用或回复他人的帖子、群聊历史含他人发言、或读取过网页时，该轮不提供此工具。
- 返回须为 JPEG、PNG 或 WebP；URL 形式的结果仅允许公网地址，重定向同样校验，下载上限沿用 Drive 下载的 32 MiB；上传受 Misskey 实例的 Drive 限制。
