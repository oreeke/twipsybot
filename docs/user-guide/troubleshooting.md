---
title: TwipsyBot 故障排查
description: 排查 Misskey 机器人鉴权、Streaming API、模型接口、图片、RSS、天线和插件问题。
---

# 故障排查

## 从日志开始

Docker：

```bash
docker compose logs --tail 200 twipsybot
```

本地安装直接查看 `twipsybot run` 输出和配置的日志文件。先处理最早出现的错误，后续错误常常只是连接或初始化失败的结果。

## 容器一直运行但机器人未启动

Docker 镜像会在启动配置错误时保持容器存活，方便查看日志。检查：

1. Misskey URL、访问令牌、API Base、模型和密钥是否填写。
2. YAML 或 Compose 是否有缩进和引号问题。
3. 挂载的 `plugins/config.yaml` 是否存在且是文件。
4. 修正后执行 `docker compose restart twipsybot`。

## `Configuration error`

运行 `twipsybot config-check` 查看字段路径。常见原因：

- 使用了不支持的额外字段。
- 布尔值写成无法识别的文本。
- 自动发帖间隔使用了秒；该字段只支持分钟、小时或天。
- `max_tokens` 小于等于 0，或 `temperature` 不在 0 到 2 之间。
- 可见性不是 `public`、`home`、`followers`。
- Iincho 的间隔低于 5 分钟，或 `sample_size < min_notes`。

## 无法连接 Misskey

- `instance_url` 应为实例根地址，例如 `https://misskey.example.com`。
- 确认令牌属于机器人账号且未撤销。
- 检查实例版本、反向代理、证书和出站网络。
- 如果能登录但不能发帖、聊天、反应或访问 Drive，通常是令牌权限不足。

## 模型请求失败

- API Base 通常需要兼容服务提供的 `/v1` 地址。
- 模型名称必须与服务端实际 ID 完全一致。
- 先使用 `api_mode: auto`；出现端点不兼容时再尝试 `chat` 或 `responses`。
- 推理模型输出中断时适当增加 `max_tokens`。
- `^model` 只能在同一个 API Base 下切换模型，不能同时切换服务商和密钥。

## 提及或聊天没有回复

- 检查 `bot.response.mention` 和 `bot.response.chat`。
- 确认用户不在黑名单，且没有达到回复间隔或轮数限制。
- 使用管理员账号发送 `^status` 和 `^blacklist list`。
- 检查 KeyAct 或 Vision 是否已接管事件但执行失败。
- 确认 Streaming API 已连接；必要时临时提高日志级别。

## `/post` 或 `/img` 没有执行

- 两者只允许 `bot.admin.allowed_users` 中的用户使用。
- `/post` 只能在私聊中使用，并且必须提供主题。
- `/img` 需要非空描述和 `openai.image_model`。
- 图片服务必须返回 JPEG、PNG 或 WebP，且结果不超过 32 MiB。
- 检查机器人是否有上传和发送 Drive 文件的权限。

## Radar 没有互动

- 确认天线在 Misskey 页面中能看到帖子。
- 检查 `bot.timeline.antenna_ids` 的 ID 或名称。
- Radar 只处理 `antenna` 通道，不处理普通时间线。
- 确认至少启用了反应、回复、引用或转帖中的一项。
- 固定回复为空且 `reply_ai` 关闭时，不会产生回复；引用同理。

## Topics 没有发帖

- 确认全局自动发帖已开启且未达到每日上限。
- TXT 模式检查 `plugins/topics/topics.txt` 是否存在且有非空行。
- RSS 模式检查 URL、网络、HTTP 状态和条目是否包含标题与链接。
- 已发布条目会记录在数据库中，不会立即重复发布。
- RSS AI 改写失败时会回退到标题；RSS 拉取失败则跳过对应来源。

## Vision 无法识图

- 确认附件是图片且不超过 `max_bytes`。
- 确认当前模型和 API mode 支持多模态输入。
- `use_thumbnail: true` 可降低下载压力，识别细节不足时改用原图。
- 查看日志中是 Drive 下载失败还是模型拒绝了图片格式。

## Iincho 不发布报告

- 必须开启 `bot.timeline.local`。
- 周期内有效文本少于 `min_notes` 时会正常跳过。
- 服务端必须同时支持文本生成、JSON 输出和 `/moderations`。
- 自定义兼容端点缺少 `omni-moderation-latest` 时无法完成报告。
- Iincho 不补采启动前的帖子，也不会补发失败周期。
