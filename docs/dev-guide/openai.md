---
title: OpenAI
description: TwipsyBot 使用的 OpenAI 兼容 API、接口模式、多模态能力与客户端约束。
---

# OpenAI

经 OpenAI Python SDK 连接 OpenAI 或兼容服务，适配集中在 `twipsybot/clients/openai`。业务代码调用 `OpenAIAPI`，不自行创建 SDK 客户端；插件使用 [`PluginContext.openai`](plugins.md#openai-与-bot)。`connect.openai_base_url` 原样传给 SDK，留空时读取 `OPENAI_BASE_URL` 或使用官方端点。

基于 OpenAI Python SDK `3.24.0`，随官方更新同步维护。

## 能力

| API | 客户端方法 | 说明 |
| --- | --- | --- |
| Responses、Chat Completions | `generate_text()`、`generate_chat()` | 单轮、多轮与多模态；`generate_text()` 可请求 JSON Object |
| Images | `generate_image()` | 需 `bot.image_model`，不属于插件稳定接口 |
| Moderations | `moderate_texts()` | 固定 `omni-moderation-latest`，需端点实现 `/moderations` |

图片生成透传 `image_size`、`image_quality`，接受 Base64 或 URL 返回；仅允许 PNG、JPEG、WebP 且不超过 32 MiB，随后上传至 Drive。

## 接口模式

| `bot.api_mode` | 行为 |
| --- | --- |
| `auto` · `responses` | 优先 Responses，不可用时回退 Chat Completions |
| `chat` | 直接使用 Chat Completions |

- Responses 返回 `404`、`405`、`501` 或明确不支持时，当前客户端实例改用 Chat Completions；认证、参数与响应格式错误不触发回退。
- 回退时多模态消息转换：`input_text` → `text`，`input_image` → `image_url`。
- 仅提供 Chat Completions 的服务设为 `chat`，可省去首次探测。

## 请求约束

- 共享 16 个并发槽位；SDK 单次超时 60 秒、最多重试 2 次；文本与审核外层等待上限 120 秒。
- `max_tokens` 映射：Responses 为 `max_output_tokens`；Chat Completions 对 OpenAI 官方域名用 `max_completion_tokens`，其他端点用 `max_tokens`。`temperature` 与 JSON Object 参数按接口转换。
- 聊天历史按 `reply.ctx_tokens` 由新到旧截取：已知模型用对应 tiktoken 编码，未知模型用 `o200k_base`，编码不可用时按字符近似；分词表缓存于 `data/tiktoken`。
- 保留认证、参数、连接与响应格式错误语义，不吞异常、不额外重试。

## 扩展与测试

- 新能力在客户端层统一请求格式与响应提取，业务代码不依赖服务商的原始 SDK 响应对象。
- 测试不调用真实服务，通过模拟 SDK 响应验证：模式覆盖 Responses 成功、回退与不应回退的错误；图片覆盖 Base64、URL、空响应、无效格式与大小限制；审核验证批量结果与输入顺序一致。
