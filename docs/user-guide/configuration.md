---
title: TwipsyBot 配置
description: 配置 Misskey API、OpenAI 兼容模型、自动发帖、访问限制、时间线、数据库和日志。
---

# 配置

TwipsyBot 可以通过 YAML 或环境变量配置。手动安装通常使用 `config.yaml`，Docker Compose 通常使用环境变量。环境变量会覆盖 YAML 中的对应值。

完整字段列表见 [配置参考](reference/configuration.md)。

## 最小配置

```yaml
misskey:
  instance_url: "https://misskey.example.com"
  access_token: "your_access_token_here"

openai:
  api_key: "your_api_key_here"
  model: "deepseek-chat"
  api_base: "https://api.deepseek.com/v1"

bot:
  system_prompt: |
    你是运行在 Misskey 上的友好助手，请简洁自然地回复。
  admin:
    allowed_users:
      - "admin@example.com"
```

`misskey.instance_url` 只填写实例根地址。访问令牌应属于机器人账号。

## 模型服务

TwipsyBot 使用 OpenAI Python SDK 连接兼容接口：

- `api_key`：服务提供商签发的密钥。
- `model`：服务端实际支持的模型 ID。
- `api_base`：兼容 API 的基础地址，通常以 `/v1` 结尾。
- `api_mode`：`auto`、`chat` 或 `responses`。
- `max_tokens`：单次输出上限，必须大于 0。
- `temperature`：生成温度，范围 0 到 2。

通常先使用 `api_mode: auto`。只有兼容服务明确要求 Chat Completions 或 Responses API 时，再固定为 `chat` 或 `responses`。

更换模型服务时，修改 `api_key`、`model` 和 `api_base`；如果新服务只支持特定接口模式，还需要调整 `api_mode`。同一 `api_base` 下切换模型，可以通过聊天管理命令 `^model <模型名>` 实时完成，无需重启。

不同能力可以由不同配置决定：

- 普通回复和自动发帖使用 `model`。
- `/img` 需要设置 `image_model`。
- Vision 需要 `model` 支持图片输入。
- Iincho 还需要服务端支持 `/moderations`。

## 系统提示词

`bot.system_prompt` 决定机器人整体语气和身份。保持内容清楚、约束具体，比堆叠大量描述更稳定。

YAML 可以使用多行文本，也可以引用项目 `prompts` 目录中的 `.txt` 文件：

```yaml
bot:
  system_prompt: "prompts/system.txt"
  auto_post:
    prompt: "prompts/auto-post.txt"
```

只允许读取 `prompts/*.txt` 范围内的相对路径。Docker 使用提示词文件时，需要挂载 `./prompts:/app/prompts:ro`。

## 自动发帖

```yaml
bot:
  auto_post:
    enabled: true
    interval: 3h
    max_posts_per_day: 8
    visibility: "public"
    local_only: false
    prompt: "生成一篇简短、有信息量的社交帖子。"
```

- `interval` 支持分钟、小时和天，例如 `30m`、`2h`、`1d`；纯数字按分钟处理。
- `max_posts_per_day` 按运行主机的本地日期重置，`0` 表示不自动发布。
- `visibility` 支持 `public`、`home`、`followers`。
- `local_only: true` 表示不向联邦发送。
- Topics 插件可以在每次自动发帖时提供主题、RSS 内容或直接发布的链接。

## 回复和访问控制

```yaml
bot:
  response:
    mention: true
    chat: true
    chat_memory: 10
    rate_limit: 30s
    max_turns: 20
    max_turns_release: 1d
    whitelist: []
    blacklist: []
```

- `mention` 和 `chat` 分别控制提及与聊天响应。
- `chat_memory` 是读取的历史消息条数，`0` 表示不带历史上下文。
- `rate_limit` 是同一用户两次机器人回复之间的最短间隔。
- `max_turns` 是同一用户可获得的机器人回复次数。
- 达到轮数上限后，`max_turns_release` 决定等待多久恢复；设为 `-1` 会将该用户加入黑名单。
- 白名单用户不受回复间隔和轮数限制；黑名单用户不能使用普通回复。

时长支持整数秒或 `30s`、`5m`、`1h`、`1d` 等组合。`-1`、`off`、`none` 和 `unlimited` 可用于关闭时长限制。

用户名单可填写用户 ID、`username@host`，环境变量中可用逗号或空格分隔。用户 ID 不受用户名和域名变化影响，适合作为长期配置。

## 时间线与天线

```yaml
bot:
  timeline:
    antenna_ids:
      - "AI Topics"
    home: false
    local: false
    hybrid: false
    global: false
```

时间线开关决定机器人接收哪些实时帖子。仅在插件需要时启用：Radar 使用天线事件，Iincho 使用本地时间线。订阅范围越大，事件和日志越多。

`antenna_ids` 可填写天线 ID 或名称。环境变量中使用逗号或空格分隔；名称包含空格时建议直接填写 ID，或在 YAML 中使用列表。

## 数据库和日志

```yaml
db:
  path: "data/twipsybot.db"
  clear: -1

log:
  path: "data/logs/twipsybot.log"
  level: "INFO"
  dump_events: false
```

SQLite 保存自动发帖计数、回复限制状态、管理命令覆盖和插件私有状态。`db.clear` 只设置回复限制状态的保留天数，`-1` 表示不清理。

日常运行使用 `INFO`。排查事件结构时才临时启用 `DEBUG` 和 `dump_events`，因为原始事件可能包含用户内容，不应长期保留或公开。

## 配置生效方式

- YAML 和环境变量在启动时读取，修改后需要重启。
- `^model`、`^whitelist` 和 `^blacklist` 的修改写入 SQLite，重启后仍会应用。
- `^mention`、`^chat` 和 `^autopost` 只修改当前进程状态，重启后恢复启动配置。
- `^model reset` 和名单的 `reset` 会删除保存的覆盖，恢复启动配置。
