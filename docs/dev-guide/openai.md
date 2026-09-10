---
title: OpenAI
description: 了解 TwipsyBot 使用的 OpenAI 兼容 API、接口模式、多模态能力与客户端约束。
---

# OpenAI

TwipsyBot 使用 OpenAI Python SDK 连接 OpenAI 或兼容服务。协议适配集中在 `twipsybot/clients/openai`，业务代码应调用 `OpenAIAPI`，不应自行创建 SDK 客户端或拼接请求。

插件不应直接导入内部客户端。需要调用模型时，应使用 `PluginContext.openai` 提供的稳定服务接口。可用方法见[插件开发](plugins.md#openai-与-bot)。

## API 能力

| API | 用途 | 客户端方法 |
| --- | --- | --- |
| Responses | 单轮文本、多轮聊天和多模态输入 | `generate_text()`、`generate_chat()` |
| Chat Completions | 单轮文本、多轮聊天和多模态输入 | `generate_text()`、`generate_chat()` |
| Images | 生成图片 | `generate_image()` |
| Moderations | 批量审核文本 | `moderate_texts()` |

`generate_text()` 可请求 JSON Object。`generate_chat()` 接收消息列表，并支持文本与图片内容。Moderations 固定使用 `omni-moderation-latest`，兼容服务需要实现 `/moderations`。

图片生成需要配置 `image_model`，可选的 `image_size` 和 `image_quality` 会直接传给兼容服务。服务可返回 Base64 数据或图片 URL。核心只接受 PNG、JPEG 和 WebP，下载或解码后的图片不得超过 32 MiB，随后会上传到 Misskey Drive。图片生成目前不属于插件稳定接口。

## 接口模式

`api_mode` 支持以下值：

| 值 | 行为 |
| --- | --- |
| `auto` | 优先使用 Responses API，不可用时回退到 Chat Completions |
| `responses` | 使用 Responses API，不可用时回退到 Chat Completions |
| `chat` | 直接使用 Chat Completions |

Responses API 返回 `404`、`405`、`501` 或明确的不支持错误时，当前客户端实例会停用 Responses API，后续请求直接使用 Chat Completions。认证错误、普通参数错误和响应格式错误不会触发模式回退。

多模态消息在回退时会转换为 Chat Completions 格式，其中 `input_text` 转为 `text`，`input_image` 转为 `image_url`。兼容服务仍需支持对应模型和消息格式。

## 请求约束

OpenAI 请求共享最多 16 个并发槽位。SDK 单次请求超时为 60 秒并最多重试 2 次，文本与审核请求的外层等待上限为 120 秒。上层业务代码应保留认证、参数、连接和响应格式等错误语义，不要统一吞掉异常或额外执行无条件重试。

`max_tokens` 在 Responses API 中映射为 `max_output_tokens`。Chat Completions 请求 OpenAI 官方域名时使用 `max_completion_tokens`，其他兼容端点使用 `max_tokens`。`temperature` 和 JSON Object 参数会按所选接口转换。

聊天历史按 `chat_context_tokens` 从最新消息向前截取。已知模型使用 tiktoken 对应编码，未知模型使用 `o200k_base`，编码不可用时退回字符数近似计算。分词表缓存在 `data/tiktoken`。

## 兼容服务

兼容端点至少应实现项目启用能力对应的 API。仅提供 Chat Completions 时，将 `api_mode` 设为 `chat` 可避免首次 Responses 探测。使用 Vision、Images 或 Moderations 前，应分别确认模型支持图片输入、图片生成和 `/moderations`。

新增模型能力时，应在客户端层统一请求格式和响应提取，再由 flow 或插件服务适配器调用。不要让业务代码依赖特定服务商的原始 SDK 响应对象。

## 错误与测试

自动化测试不得调用真实模型服务。文本和多模态行为通过模拟 SDK 响应验证，模式兼容需覆盖 Responses 成功、不可用后回退和不应回退的错误。图片测试应覆盖 Base64、URL、空响应、无效格式和大小限制，审核测试应验证批量结果与输入顺序一致。

## API 版本

项目代码和文档基于 OpenAI Python SDK `3.6.0`，并随官方更新同步维护。
