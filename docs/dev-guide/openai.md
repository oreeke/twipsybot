---
title: OpenAI
description: TwipsyBot 使用的 OpenAI 兼容 API、接口模式、多模态能力与客户端约束。
---

# OpenAI

经 OpenAI Python SDK 连接 OpenAI 或兼容服务，适配集中在 `twipsybot/clients/openai`。业务代码调用 `OpenAIAPI`，不自建 SDK 客户端；插件使用 [`PluginContext.openai`](plugins.md#openai-与-bot)。`connect.ai_base_url` 原样传给 SDK，留空时读取 `OPENAI_BASE_URL`。

基于 OpenAI Python SDK `3.24.0`，随官方更新同步维护。

## 能力

| API | 客户端方法 | 说明 |
| --- | --- | --- |
| Responses、Chat Completions | `generate_text()`、`generate_chat()` | 单轮、多轮与多模态；`generate_text()` 可请求 JSON Object；`generate_chat()` 可带工具 |
| Images | `generate_image()` | 由调用方传入；插件经 `context.openai.generate_image()` 使用 |
| Moderations | `moderate_texts()` | 固定 `omni-moderation-latest`，需端点实现 `/moderations` |

图片生成透传 `size`、`quality`，接受 Base64 或 URL 返回。传入 `base_url` 或 `api_key` 时使用临时专用客户端，留空沿用 `connect.*`。格式校验及上传 Drive 由调用方完成。

## 接口模式

| `bot.api_mode` | 行为 |
| --- | --- |
| `auto` · `responses` | 优先 Responses，不可用时回退 Chat Completions |
| `chat` | 直接使用 Chat Completions |

- Responses 返回 `404`、`405`、`501` 时，当前客户端实例改用 Chat Completions；认证、参数与响应格式错误不回退。
- 回退时多模态消息转换：`input_text` → `text`，`input_image` → `image_url`，`input_file` → `file`。
- 仅提供 Chat Completions 的服务设为 `chat`，可省去首次探测。

## 工具调用

`generate_chat(..., tools=)` 接受提供 `specs`（名称、说明、参数 Schema）与 `run(name, arguments)` 的对象，调用循环由客户端驱动：

- Responses 回传 `function_call` 与 `function_call_output`（含推理项）；Chat Completions 使用 `tool_calls` 与 `tool` 消息。
- 同一轮的多个调用并行执行，最多 3 轮，之后不带工具生成最终回复；`specs` 每轮重新读取。
- 端点拒绝工具参数时，本次请求不带工具重试。
- Schema 满足 strict 要求（参数全部必填、`additionalProperties: false`）时 Responses 自动采用 strict，否则按非 strict。
- 部分模型（如 GPT-6 Astra、GPT-6.1 Sol）的工具调用仅支持 Responses，`bot.api_mode` 保持 `auto` 或 `responses`。

## 请求约束

- 共享 16 并发槽位；SDK 单次超时 60 秒、最多重试 2 次；文本与审核外层等待上限 120 秒。
- `max_tokens`：Responses 为 `max_output_tokens`；Chat Completions 对 OpenAI 官方域名用 `max_completion_tokens`，其他端点用 `max_tokens`。
- 聊天历史按 `reply.ctx_tokens` 由新到旧截取：已知模型用对应 tiktoken 编码，未知用 `o200k_base`，不可用时按字符近似；分词表缓存于 `data/tiktoken`。
- 认证、参数、连接与响应格式错误原样抛出，不额外重试。

新能力在客户端层统一请求与响应提取，业务代码不依赖原始 SDK 响应对象。
