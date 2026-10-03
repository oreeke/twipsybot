---
title: TwipsyBot 配置
description: 配置 Misskey API、OpenAI 兼容模型、自动发帖、访问限制、时间线、插件、数据库和日志。
---

# 配置

TwipsyBot 无需手写配置文件，所有配置都在 `twipsybot cfg` 中完成：

1. `connect`：实例地址、访问令牌和 API 密钥，保存在 `data/secrets.yaml`。
2. 其余分区：运行设置，保存在 `data/settings.yaml`。

完整字段列表见 [配置参考](reference/configuration.md)。

## 连接信息

首次运行时在 `connect` 中填写三项连接信息。实例地址只填写根地址，访问令牌应属于机器人账号。缺少连接信息时，机器人会等待，保存后自动启动。

`data/secrets.yaml` 与设置分开存放，分享 `settings.yaml` 求助时不会泄露密钥。需要交给外部密钥管理时，可用环境变量 `MISSKEY_INSTANCE_URL`、`MISSKEY_ACCESS_TOKEN`、`OPENAI_BASE_URL`、`OPENAI_API_KEY`（或工作目录中的 `.env`）覆盖，被覆盖的字段在 TUI 中标为 `env` 且不可编辑。

## 调整设置

本地运行：

```bash
twipsybot cfg
```

Docker Compose：

```bash
docker compose exec twipsybot twipsybot cfg
```

TUI 是英文界面。左侧按 `CORE`（`connect`、`bot`、`timeline`、`autopost`、`reply`、`system`）和 `PLUGINS` 分组，插件前的 `●` / `○` 表示启用或关闭。

常用按键：

| 按键 | 作用 |
| --- | --- |
| `Ctrl+S` | 保存 |
| `Ctrl+R` | 从磁盘重新加载 |
| `Ctrl+Q` | 退出，有未保存修改时会询问 |
| `Ctrl+P` | 命令面板，可切换主题 |

字段会实时校验。无效字段显示为红色，保存会被阻止。状态栏显示未保存数量、无效数量和需要重启的 `↻` 字段。保存时只写你改过的字段，并合并到最新文件上，机器人同时写入的管理命令变更会保留。列表字段一行一个值。Topics `rss_list` 和 KeyAct `rules` 也是逐行填写，内容原样保存，`#` 开头的行是注释：

```text
# 科普
https://www.nasa.gov/feeds/iotd-feed
https://www.sciencedaily.com/rss/all.xml
```

也可以直接编辑 `data/settings.yaml`。文件只需要写与默认值不同的字段：

```yaml
bot:
  model: gpt-5.4-mini
autopost:
  rotation: true
reply:
  rate_limit: 30s
```

## 模型服务

TwipsyBot 使用 OpenAI Python SDK 连接兼容接口。服务地址和密钥位于 `connect`：

- `openai_base_url`：服务商文档给出的 OpenAI 兼容地址，默认 DeepSeek；留空使用 OpenAI 官方端点，修改后需要重启。
- `openai_api_key`：API 密钥。

模型参数位于 `bot`：

- `model`：服务端实际支持的文本模型 ID。
- `api_mode`：`auto`、`chat` 或 `responses`。
- `max_tokens`：单次输出上限，必须大于 0。
- `temperature`：生成温度，范围 0 到 2。

通常先使用 `api_mode: auto`。只有兼容服务明确要求 Chat Completions 或 Responses API 时，再固定为 `chat` 或 `responses`。同一 `openai_base_url` 下切换模型，可以通过管理命令 `^model <模型名>` 实时完成。

不同能力可以由不同设置决定：普通回复和自动发帖使用 `model`，`/img` 需要设置 `image_model`，Vision 需要 `model` 支持图片输入，Iincho 默认还需要端点支持 `/moderations`。

## 提示词文件

`bot.system_prompt` 和 `autopost.prompt` 可以直接写文本，也可以引用 `prompts/*.txt`：

```yaml
bot:
  system_prompt: "prompts/system.txt"
autopost:
  prompt: "prompts/auto-post.txt"
```

路径必须相对工作目录并位于 `prompts/` 下。Docker 示例会把宿主机 `./prompts` 只读挂载到 `/app/prompts`。编辑引用的文本文件也会热更新。

## 自动发帖

```yaml
autopost:
  rotation: false
  interval: 3h
  daily_max: 8
  schedule: false
  times: []
  visibility: public
  local_only: false
  prompt: "生成一篇有趣、有见解的社交媒体帖子。"
```

- `rotation` 与 `schedule` 互斥且默认都关闭：前者按 `interval` 循环，后者在 `times` 的每个时间点发帖。
- `interval` 支持纯数字分钟或 `30m`、`2h`、`1d`，至少 5 分钟。
- `daily_max` 只限制轮转，按运行主机的本地日期重置，`0` 表示不自动发布；定时不受此限制。
- `visibility` 支持 `public`、`home`、`followers`。
- `local_only: true` 表示不向联邦发送。
- Topics 插件可以在每次自动发帖时提供主题、RSS 内容或直接发布的链接。

## 回复和访问控制

```yaml
reply:
  mention: true
  chat: true
  memory: 10
  ctx_tokens: 2000
  rate_limit: 30s
  max_turns: 20
  turns_release: 1d
  whitelist: []
  blacklist: []
```

- `mention` 和 `chat` 分别控制提及与聊天响应。
- `memory` 是读取的历史消息条数，范围为 `0` 到 `100`，`0` 表示不带历史上下文。
- `ctx_tokens` 是历史消息的 token 预算，`0` 表示不带历史上下文。
- `rate_limit` 是同一用户两次机器人回复之间的最短间隔。
- `max_turns` 是同一用户可获得的机器人回复次数，只在开启（不为 `-1`）期间计数。
- 达到轮数上限后，`turns_release` 决定等待多久恢复，默认 `1h`。设为 `-1` 会将该用户加入黑名单并写回 `data/settings.yaml`。
- `rate_limit_msg` 和 `max_turns_msg` 是触发限制时的提示，有默认文案，留空则不回复。
- 白名单用户和 `bot.admins` 中的管理员不受回复间隔和轮数限制；黑名单用户不能使用普通回复。

用户名单可填写用户 ID、`username@host` 或带前导 `@` 的完整账号。用户 ID 不受用户名和域名变化影响，适合作为长期配置。

## 时间线与天线

```yaml
timeline:
  antennas:
    - "project-news"
  home: false
  local: false
  hybrid: false
  global: false
```

时间线开关决定机器人接收哪些实时帖子。仅在插件需要时启用：Radar 使用天线事件，Iincho 使用本地时间线。订阅范围越大，事件和日志越多。所有 `timeline.*` 修改后都需要重启。

## 数据库和日志

数据库和日志路径固定：

- SQLite：`data/twipsybot.db`
- 日志：`data/logs/twipsybot.log`

`system.log_level` 控制日志级别。日常运行使用 `INFO`。排查事件结构时才临时启用 `DEBUG` 和 `system.dump_events`，因为原始事件可能包含用户内容，不应长期保留或公开。

`system.db_clear_days` 只设置回复限制状态的保留天数，`-1` 表示不清理。

## 配置生效方式

运行中的机器人会约每 2 秒轮询 `data/settings.yaml` 和被引用的 `prompts/*.txt` 文件。有效修改会自动应用；无效文件会被拒绝并记录错误，当前设置保持不变。

只有 `connect` 和全部 `timeline.*` 需要重启。TUI 会用 `↻` 标记，机器人日志会提示 `Restart required for: ...`。

插件配置在 `plugins.<name>` 下，文件变更后会自动重载对应插件。管理员命令 `^reload <插件名>` 会先重新读取设置，再手动重载插件。
