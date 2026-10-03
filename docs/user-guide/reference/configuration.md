---
title: TwipsyBot 配置参考
description: 查询 TwipsyBot 连接信息、settings.yaml、默认值和生效方式。
---

# 配置参考

连接信息保存在 `data/secrets.yaml`，其余运行设置保存在 `data/settings.yaml`。`twipsybot cfg` 会打开全屏终端界面；也可以手写 YAML。缺失的文件或字段都表示使用默认值，保存时会自动丢弃与默认值相同的字段。

未知字段会导致配置检查失败。无效的设置文件会被运行中的机器人拒绝，并继续使用当前设置。

## connect

保存在 `data/secrets.yaml`，修改后需要重启。缺少 Misskey 地址、令牌或 API 密钥时机器人会等待，保存后自动启动。

| 键 | 覆盖环境变量 | 说明 |
| --- | --- | --- |
| `misskey_url` | `MISSKEY_INSTANCE_URL` | Misskey 实例根地址，例如 `https://misskey.example.com` |
| `misskey_token` | `MISSKEY_ACCESS_TOKEN` | 机器人账号访问令牌 |
| `openai_base_url` | `OPENAI_BASE_URL` | OpenAI 兼容 API Base，默认 `https://api.deepseek.com`；留空使用 OpenAI 官方端点 |
| `openai_api_key` | `OPENAI_API_KEY` | OpenAI 兼容 API 密钥 |

环境变量可选，设置后优先于文件中的值。本地 CLI 会自动加载工作目录中的 `.env`。`TZ` 仍可在运行环境中设置，用于影响日志和每日计数的本地时间。

## 生效方式

| 标记 | 含义 |
| --- | --- |
| 热更新 | 运行中的机器人约每 2 秒读取 `data/settings.yaml` 并应用变更 |
| 热更新，提示词文件也热更新 | 字段可填写 `prompts/*.txt`，编辑引用的文本文件也会自动生效 |
| 重启 | TUI 会标出 `↻`，日志会提示 `Restart required for: ...` |

`connect` 和全部 `timeline.*` 修改后需要重启。插件配置变更会自动热重载对应插件。

## bot

| 键 | 默认值 | 说明 | 生效 |
| --- | --- | --- | --- |
| `system_prompt` | `你是一个可爱的AI助手...` | 对话系统提示词，可填写 `prompts/*.txt` | 热更新，提示词文件也热更新 |
| `admins` | `[]` | 可使用 `^` 与 `/` 命令的用户 ID 或 `username@host` | 热更新 |
| `model` | `deepseek-flash` | 文本模型 ID | 热更新 |
| `api_mode` | `auto` | `auto`、`chat`、`responses` | 热更新 |
| `max_tokens` | `2000` | 最大输出 token，必须大于 0 | 热更新 |
| `temperature` | `0.8` | 生成温度，范围 0 到 2 | 热更新 |
| `image_model` | `null` | 图片生成模型，空值禁用 `/img` | 热更新 |
| `image_size` | `null` | 发送给图片 API 的尺寸参数 | 热更新 |
| `image_quality` | `null` | 发送给图片 API 的质量参数 | 热更新 |

图片尺寸和质量不会由 TwipsyBot 验证枚举值，应填写当前模型服务支持的值。

## timeline

| 键 | 默认值 | 说明 | 生效 |
| --- | --- | --- | --- |
| `home` | `false` | 订阅 Home Timeline | 重启 |
| `local` | `false` | 订阅 Local Timeline，Iincho 需要 | 重启 |
| `hybrid` | `false` | 订阅 Hybrid Timeline | 重启 |
| `global` | `false` | 订阅 Global Timeline | 重启 |
| `antennas` | `[]` | 天线 ID 或名称，Radar 需要 | 重启 |

启用通道只会建立订阅，实际动作由对应插件决定。

## autopost

| 键 | 默认值 | 说明 | 生效 |
| --- | --- | --- | --- |
| `rotation` | `false` | 轮转模式：按 `interval` 循环发帖，与 `schedule` 互斥 | 热更新 |
| `interval` | `3h` | 轮转间隔，支持纯数字分钟或 `30m`、`2h`、`1d`，至少 5 分钟 | 热更新 |
| `daily_max` | `8` | 轮转每日上限，`0` 表示不发帖；定时不受限制 | 热更新 |
| `schedule` | `false` | 定时模式：在 `times` 的每个时间点发帖，与 `rotation` 互斥 | 热更新 |
| `times` | `[]` | `HH:MM` 时间点列表，按主机时区，最多 24 个，相邻至少间隔 5 分钟 | 热更新 |
| `visibility` | `public` | `public`、`home`、`followers` | 热更新 |
| `local_only` | `false` | 禁用联合，仅本地发布 | 热更新 |
| `prompt` | `生成一篇有趣、有见解的社交媒体帖子。` | 自动发帖提示词，可填写 `prompts/*.txt` | 热更新，提示词文件也热更新 |

秒单位不适用于自动发帖间隔。手写 YAML 时时间点建议加引号，例如 `"08:30"`。

## reply

| 键 | 默认值 | 说明 | 生效 |
| --- | --- | --- | --- |
| `mention` | `true` | 响应帖子提及 | 热更新 |
| `chat` | `true` | 响应私聊和群聊 | 热更新 |
| `memory` | `10` | 每个聊天保留的上下文条数，范围 0-100 | 热更新 |
| `ctx_tokens` | `2000` | 历史消息 token 预算，0 禁用 | 热更新 |
| `rate_limit` | `-1` | 同一用户最小回复间隔，`-1` 表示不限 | 热更新 |
| `rate_limit_msg` | `我需要休息一下...` | 间隔限制提示，留空则静默跳过 | 热更新 |
| `max_turns` | `-1` | 每个用户最多机器人回复次数，`-1` 不限制且不计数 | 热更新 |
| `max_turns_msg` | `我要回家了...` | 次数用尽提示，留空则静默跳过 | 热更新 |
| `turns_release` | `1h` | 轮数限制解除时间；`-1` 加入黑名单 | 热更新 |
| `whitelist` | `[]` | 不受间隔和次数限制的用户，管理员自动豁免 | 热更新 |
| `blacklist` | `[]` | 禁止使用普通回复的用户 | 热更新 |

时长支持整数秒、`30s`、`5m`、`1h`、`1d` 和组合格式。`off`、`none`、`unlimited` 等价于 `-1`。

## system

| 键 | 默认值 | 说明 | 生效 |
| --- | --- | --- | --- |
| `log_level` | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR` | 热更新 |
| `dump_events` | `false` | 记录原始 Streaming 事件 | 热更新 |
| `db_clear_days` | `-1` | SQLite 回复限制状态保留天数，`-1` 不清理 | 热更新 |

数据库固定为 `data/twipsybot.db`，日志固定为 `data/logs/twipsybot.log`。

## plugins

插件配置位于 `plugins.<name>`。所有插件都有通用字段：

| 键 | 默认值 | 说明 | 生效 |
| --- | --- | --- | --- |
| `enabled` | `false` | 是否启用插件 | 热更新并重载插件 |
| `priority` | 插件类默认值 | 数字越大越先执行 | 热更新并重载插件 |

内置插件默认优先级：KeyAct `990`，Vision `900`，Topics `100`，Radar `50`，Iincho `40`。第三方插件作者可在 `PluginBase` 子类上声明 `priority = <int>` 作为默认值。插件自定义字段保持各自文档中的名称不变。

示例：

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

KeyAct `rules` 与 Topics `rss_list` 逐行填写并原样保存，`#` 开头的行是注释；也接受普通 YAML 列表。

入口点插件也使用入口名称作为 `plugins.<name>` 的键。插件中的 `context.config` 是该映射的只读视图。
