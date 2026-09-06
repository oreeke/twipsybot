---
title: TwipsyBot 配置参考
description: 查询 TwipsyBot 主配置、环境变量、默认值和有效取值。
---

# 配置参考

TwipsyBot 读取项目根目录的 `config.yaml`。环境变量会覆盖 YAML 中的同名配置；未列入本页的未知字段会导致配置检查失败。

插件使用独立的 `plugins/config.yaml`，请查看[插件](/user-guide/plugins/)。

## Misskey

| YAML 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `misskey.instance_url` | `MISSKEY_INSTANCE_URL` | 必填 | 实例根 URL |
| `misskey.access_token` | `MISSKEY_ACCESS_TOKEN` | 必填 | 机器人账号访问令牌 |

## 模型服务

| YAML 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `openai.api_key` | `OPENAI_API_KEY` | 必填 | OpenAI 兼容 API 密钥 |
| `openai.model` | `OPENAI_MODEL` | `deepseek-chat` | 文本模型 ID |
| `openai.api_base` | `OPENAI_API_BASE` | `api.deepseek.com/v1` | OpenAI 兼容 API Base |
| `openai.api_mode` | `OPENAI_API_MODE` | `auto` | `auto`、`chat`、`responses` |
| `openai.image_model` | `OPENAI_IMAGE_MODEL` | `null` | 图片生成模型；空值禁用 `/img` |
| `openai.image_size` | `OPENAI_IMAGE_SIZE` | `null` | 发送给图片 API 的尺寸参数 |
| `openai.image_quality` | `OPENAI_IMAGE_QUALITY` | `null` | 发送给图片 API 的质量参数 |
| `openai.max_tokens` | `OPENAI_MAX_TOKENS` | `1000` | 最大输出 token，必须大于 0 |
| `openai.temperature` | `OPENAI_TEMPERATURE` | `0.8` | 生成温度，范围 0 到 2 |

图片尺寸和质量不会由 TwipsyBot 验证枚举值，应填写当前模型服务支持的值。

## 机器人与管理员

| YAML 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `bot.system_prompt` | `BOT_SYSTEM_PROMPT` | 空 | 对话系统提示词 |
| `bot.admin.allowed_users` | `BOT_ADMIN_ALLOWED_USERS` | `[]` | 管理员用户 ID 或 `username@host` |

系统提示词和自动发帖提示词可以填写 `prompts/*.txt` 相对路径。路径必须位于项目的 `prompts` 目录，且不能使用绝对路径或 `..`。

名单在 YAML 中可写为列表。环境变量支持逗号或空格分隔：

```text
BOT_ADMIN_ALLOWED_USERS=admin@example.com,9abcdef012345678
```

## 自动发帖

| YAML 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `bot.auto_post.enabled` | `BOT_AUTO_POST_ENABLED` | `true` | 启用定时发帖 |
| `bot.auto_post.interval` | `BOT_AUTO_POST_INTERVAL` | `3h` | 间隔，支持分钟、小时、天 |
| `bot.auto_post.max_posts_per_day` | `BOT_AUTO_POST_MAX_PER_DAY` | `8` | 每日上限，0 表示不发帖 |
| `bot.auto_post.visibility` | `BOT_AUTO_POST_VISIBILITY` | `public` | `public`、`home`、`followers` |
| `bot.auto_post.local_only` | `BOT_AUTO_POST_LOCAL_ONLY` | `false` | 禁用联合，仅本地发布 |
| `bot.auto_post.prompt` | `BOT_AUTO_POST_PROMPT` | 空 | 自动发帖提示词 |

纯数字间隔按分钟解释，也可使用 `30m`、`2h`、`1d` 或组合格式。秒单位不适用于自动发帖间隔。

## 回复与访问控制

| YAML 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `bot.response.mention` | `BOT_RESPONSE_MENTION` | `true` | 响应帖子提及 |
| `bot.response.chat` | `BOT_RESPONSE_CHAT` | `true` | 响应私聊和群聊 |
| `bot.response.chat_memory` | `BOT_RESPONSE_CHAT_MEMORY` | `10` | 每个聊天保留的上下文条数，0 禁用 |
| `bot.response.rate_limit` | `BOT_RESPONSE_RATE_LIMIT` | `-1` | 同一用户最小回复间隔 |
| `bot.response.rate_limit_reply` | `BOT_RESPONSE_RATE_LIMIT_REPLY` | `我需要休息一下...` | 间隔限制提示 |
| `bot.response.max_turns` | `BOT_RESPONSE_MAX_TURNS` | `-1` | 每个用户最多机器人回复次数 |
| `bot.response.max_turns_reply` | `BOT_RESPONSE_MAX_TURNS_REPLY` | `我要回家了...` | 次数用尽提示 |
| `bot.response.max_turns_release` | `BOT_RESPONSE_MAX_TURNS_RELEASE` | `-1` | 次数限制解除时间；`-1` 转入黑名单 |
| `bot.response.whitelist` | `BOT_RESPONSE_WHITELIST` | `[]` | 不受间隔和次数限制的用户 |
| `bot.response.blacklist` | `BOT_RESPONSE_BLACKLIST` | `[]` | 禁止使用回复的用户 |

回复时长支持整数秒、`30s`、`5m`、`1h`、`1d` 和组合格式。`-1`、`off`、`none`、`unlimited` 均表示不限制。

## 时间线

| YAML 字段 | 环境变量 | 默认值 | 通道 |
| --- | --- | --- | --- |
| `bot.timeline.home` | `BOT_TIMELINE_HOME` | `false` | Home Timeline |
| `bot.timeline.local` | `BOT_TIMELINE_LOCAL` | `false` | Local Timeline |
| `bot.timeline.hybrid` | `BOT_TIMELINE_HYBRID` | `false` | Hybrid Timeline |
| `bot.timeline.global` | `BOT_TIMELINE_GLOBAL` | `false` | Global Timeline |
| `bot.timeline.antenna_ids` | `BOT_TIMELINE_ANTENNA_IDS` | `[]` | 天线 ID 或名称 |

天线可在 YAML 中写列表，环境变量中使用逗号或空格分隔。启用通道只会建立订阅，实际动作由对应插件决定。

## 数据库与日志

| YAML 字段 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `db.path` | `DB_PATH` | `data/twipsybot.db` | SQLite 文件路径 |
| `db.clear` | `DB_CLEAR` | `-1` | 回复限制状态保留天数；`-1` 不清理 |
| `log.path` | `LOG_PATH` | `data/logs/twipsybot.log` | 日志文件路径 |
| `log.level` | `LOG_LEVEL` | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR` |
| `log.dump_events` | `LOG_DUMP_EVENTS` | `false` | 记录原始 Streaming 事件 |

## 其他环境变量

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TZ` | 运行环境决定 | 时区，影响每日计数重置和日志时间 |

使用 Docker 时，主配置通常完全由 Compose 环境变量提供，`plugins/config.yaml` 则以只读文件挂载。
