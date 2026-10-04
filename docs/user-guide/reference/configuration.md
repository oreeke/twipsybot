---
title: 配置参考
description: TwipsyBot 全部配置字段、默认值与生效方式。
---

# 配置参考

`connect` 存于 `data/secrets.yaml`，其余存于 `data/settings.yaml`。缺省即默认值，保存时自动省略默认值字段，未知字段校验失败。除标注 <Badge type="warning" text="重启" /> 的分区外均热更新。

## connect <Badge type="warning" text="重启" />

| 键 | 环境变量 | 说明 |
| --- | --- | --- |
| `misskey_url` | `MISSKEY_INSTANCE_URL` | 实例根地址，如 `https://misskey.example.com` |
| `misskey_token` | `MISSKEY_ACCESS_TOKEN` | 机器人访问令牌 |
| `openai_base_url` | `OPENAI_BASE_URL` | 默认 `https://api.deepseek.com`，留空为 OpenAI 官方 |
| `openai_api_key` | `OPENAI_API_KEY` | API 密钥 |

环境变量（含工作目录 `.env`）优先于文件。`TZ` 决定日志与每日计数的本地时间。

## bot

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `system_prompt` | `你是一个可爱的AI助手...` | 系统提示词，可引用 `prompts/*.txt` |
| `admins` | `[]` | 管理员用户 ID 或 `username@host` |
| `model` | `deepseek-flash` | 文本模型 |
| `api_mode` | `auto` | `auto` / `chat` / `responses` |
| `max_tokens` | `2000` | 最大输出 token，须大于 0 |
| `temperature` | `0.8` | 0–2 |
| `image_model` | `null` | 图片模型，为空时禁用 `/img` |
| `image_size` | `null` | 透传给图片 API，不校验取值 |
| `image_quality` | `null` | 同上 |

## timeline <Badge type="warning" text="重启" />

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `home` | `false` | Home 时间线 |
| `local` | `false` | Local 时间线，Iincho 必需 |
| `hybrid` | `false` | Hybrid 时间线 |
| `global` | `false` | Global 时间线 |
| `antennas` | `[]` | 天线 ID 或名称，Radar 必需 |

仅建立订阅，实际动作由插件决定。

## autopost

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `rotation` | `false` | 轮转：每 `interval` 发帖，与 `schedule` 互斥 |
| `interval` | `3h` | 分钟数或 `30m`、`2h`、`1d`，至少 5 分钟，不支持秒 |
| `daily_max` | `8` | 轮转每日上限，`0` 不发帖；定时不受限 |
| `schedule` | `false` | 定时：在 `times` 各时间点发帖 |
| `times` | `[]` | `"HH:MM"`，主机时区，最多 24 个，相邻至少 5 分钟 |
| `visibility` | `public` | `public` / `home` / `followers` |
| `local_only` | `false` | 仅本地发布，不联合 |
| `prompt` | `生成一篇有趣、有见解的社交媒体帖子。` | 可引用 `prompts/*.txt` |

## reply

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `mention` | `true` | 响应提及 |
| `chat` | `true` | 响应私聊与群聊 |
| `memory` | `10` | 上下文条数，0–100 |
| `ctx_tokens` | `2000` | 上下文 token 预算，`0` 禁用 |
| `rate_limit` | `-1` | 同一用户最小回复间隔 |
| `rate_limit_msg` | `我需要休息一下...` | 间隔提示，留空静默 |
| `max_turns` | `-1` | 每用户回复次数上限，`-1` 不限且不计数 |
| `max_turns_msg` | `我要回家了...` | 上限提示，留空静默 |
| `turns_release` | `1h` | 达上限后的解除时间，`-1` 改为拉黑 |
| `whitelist` | `[]` | 豁免间隔与次数限制，管理员自动豁免 |
| `blacklist` | `[]` | 禁用普通回复 |

时长支持整数秒、`30s`、`5m`、`1h`、`1d` 及组合；`-1`、`off`、`none`、`unlimited` 表示不限。

## system

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `dump_events` | `false` | 记录原始 Streaming 事件 |
| `db_clear_days` | `-1` | 回复限制状态保留天数，`-1` 不清理 |

数据库 `data/twipsybot.db` 与日志 `data/logs/twipsybot.log` 路径固定。

## plugins

键为本地插件目录名或 Entry Point 名，`context.config` 是其只读视图。通用字段：

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 启用插件 |
| `priority` | 插件默认值 | 越大越先执行 |

内置默认优先级：KeyAct `850` · Vision `750` · Topics `300` · Radar `50` · Iincho `40`。其余字段见各[插件](../plugins/)页面。

```yaml
plugins:
  vision:
    enabled: true
  keyact:
    enabled: true
    rules: |-
      # 测试
      ping = pong
```

多行字段（KeyAct `rules`、Topics `rss_list`）原样保存，也接受 YAML 列表。
