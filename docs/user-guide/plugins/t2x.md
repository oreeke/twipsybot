---
title: T2X 文本生成多媒体
description: 使用 T2X 让 TwipsyBot 调用 OpenAI 兼容接口生成多媒体。
---

# T2X：文本生成多媒体

管理员发送 `/img <描述>`，调用 OpenAI 兼容的图片接口生成图片，上传到机器人 Drive 并作为附件回复。目前仅支持图片。

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
| `image.model` | 图片模型，必填；为空时 `/img` 回复失败提示 |
| `image.size` | 透传给图片 API，不校验取值，留空不传 |
| `image.quality` | 同上 |

设置 `image.base_url` 时必须同时设置 `image.api_key`，避免把主密钥发往其他服务。`image.api_key` 存于 `data/secrets.yaml`：

```yaml
plugins:
  t2x:
    image:
      api_key: "sk-..."
```

仅在服务支持时填写 `size` 与 `quality`。修改配置后自动重载。

## 使用

仅 `bot.admins` 可用，场景为私聊、群聊（需提及）与提及：

```text
/img 雨后的未来城市车站，清晨，自然光
```

- 收到命令后先回复“正在生成图片，请稍候…”，生成完成后补发带图片的回复，失败则补发失败提示；生成期间不阻塞其他消息。
- 结果上传至机器人 Drive 并作为附件回复，Drive 文件不会自动清理。
- 返回须为 JPEG、PNG 或 WebP；URL 形式的结果仅允许公网地址，重定向同样校验，下载上限沿用 Drive 下载的 32 MiB；上传受 Misskey 实例的 Drive 限制。
