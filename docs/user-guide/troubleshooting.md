---
title: 故障排查
description: 排查 TwipsyBot 启动、鉴权、模型接口、命令与插件问题。
---

# 故障排查

先看日志，优先处理最早出现的错误：

```bash
docker compose logs --tail 200 twipsybot
```

本地运行查看终端输出或 `data/logs/twipsybot.log`。

## `Startup blocked`

连接信息缺失、设置无效、鉴权或连接失败时，进程不退出而是等待设置变更，连接失败另每 60 秒重试。在 TUI 中修正并保存即自动重试。

## `Configuration error`

运行 `twipsybot config-check`，报错会指出具体字段。

## 无法连接 Misskey

- `misskey_url` 只填根地址，如 `https://misskey.example.com`。
- 令牌属于机器人账号且未撤销。
- 检查反向代理、证书与出站网络。
- 能登录却无法发帖、聊天、反应或访问 Drive：令牌权限不足。

## 模型请求失败

- `openai_base_url` 使用服务商给出的兼容地址，OpenAI 官方留空。
- 模型名与服务端 ID 完全一致。
- 端点不兼容时将 `bot.api_mode` 从 `auto` 改为 `chat` 或 `responses`。
- 推理模型输出截断时调大 `bot.max_tokens`。
- `^model` 只切换模型，不切换端点与密钥。

## 提及或聊天无回复

- `reply.mention` / `reply.chat` 已开启。
- 用户未被拉黑，也未触发间隔或轮数限制（`^status`、`^blacklist list`）。
- KeyAct 或 Vision 是否接管后失败。
- Streaming 是否已连接，必要时临时调为 `DEBUG`。

## `/post` 或 `/img` 无效

- 仅 `bot.admins` 可用。
- `/post` 仅限私聊且需提供主题；`/img` 需要描述与 `bot.image_model`。
- 图片须为 JPEG、PNG 或 WebP，不超过 32 MiB，且令牌具备 Drive 权限。

## 插件

### Radar 无互动

- 天线在 Misskey 中能看到帖子，`timeline.antennas` 正确且修改后已重启。
- 至少启用反应、回复、引用、转帖之一。
- 固定文本为空且未开启 `reply_ai` / `quote_ai` 时不会回复 / 引用。

### Topics 不发帖

- `autopost.mode` 为 `rotation` 或 `schedule`，轮转未达每日上限。
- list 模式下 `list` 不能为空，引用的 `prompts/` 文件需存在，否则插件启动失败。
- RSS 模式：检查 URL、网络与 HTTP 状态，条目需含标题与链接。已发布条目不会重复；AI 改写失败回退为标题，拉取失败跳过该源。

### Vision 不识图

- 附件为图片且不超过 `max_bytes`。
- 附件 URL 指向内网会被拒绝，需配置 `system.allow_nets`。
- 模型与 `bot.api_mode` 支持多模态输入。
- 细节不足时关闭 `use_thumbnail`。
- 从日志区分 Drive 下载失败与模型拒绝。

### Web 不检索

- `endpoint` 已配置且可达；`searxng` 后端需已启用 `json` 输出。
- `always_on` 关闭时需以 `/web <问题>` 发起；开启时问题去掉链接与提及后不少于 `min_chars`。
- 链接为内网地址（需配置 `system.allow_nets`）、被域名规则拦截或非文本网页时不会读取。
- 检索失败不影响回复，详见日志中的 `Web search failed`。

### Iincho 无报告

- 已开启 `timeline.local` 并重启。
- 有效样本少于 `min_notes` 时正常跳过；不补采启动前帖子，不补发失败周期。
- 配置 `admin_ids` 且发现违规时，文本模型需支持 JSON 输出。
- `openai` 后端需端点支持 `/moderations` 与 `omni-moderation-latest`，否则改用 `cloudflare`。
- `cloudflare` 后端：401/403 检查账户 ID 与 Workers AI 权限，429 调小 `cf_concurrency` 或 `sample_size`。
