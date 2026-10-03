---
title: 管理命令
description: 查询 TwipsyBot 的状态、模型、功能开关、用户访问名单，以及清理和手动发布帖子。
---

# 管理命令

管理命令在与机器人的聊天页面中使用。

只有 `bot.admins` 中的用户 ID 或账号可以执行。

## 本地管理命令

以 `^` 开头的命令直接在机器人进程中处理：

| 命令 | 作用 |
| --- | --- |
| `^help` | 查看可用命令 |
| `^status` | 机器人状态 |
| `^model` | 查看当前模型 |
| `^model <模型名>` | 切换模型（相同 `openai_base_url`） |
| `^model reset` | 删除自定义模型，恢复默认 `deepseek-flash` |
| `^autopost <rotation\|schedule\|off>` | 切换轮转、定时或关闭自动发帖 |
| `^autopost reset` | 重置当天轮转发帖计数 |
| `^clean posts <天数>` | 预览超过指定时间未被互动的帖子 |
| `^clean posts <天数> -y` | 确认删除预览范围内符合条件的帖子 |
| `^reload <插件名>` | 重新读取设置并重载指定插件 |
| `^mention on\|off` | 响应提及开关 |
| `^chat on\|off` | 响应聊天开关 |
| `^whitelist ...` | 查看/修改白名单 |
| `^blacklist ...` | 查看/修改黑名单 |

名单命令支持：

```text
^whitelist list
^whitelist add user-id username@example.com
^whitelist del user-id
^whitelist set user-a user-b
^whitelist clear
```

`blacklist` 使用相同语法。`set` 会替换整个名单，`clear` 保存空名单。

`clean posts` 只处理机器人的独立普通帖子，排除回复、转帖、提及、频道、投票、置顶、Clip 及已有回复、转帖或反应的帖子。确认删除前会重新检查互动状态，并按从旧到新的顺序删除。Misskey API 每小时最多接受 300 次删除请求，且请求间隔至少 1 秒。命令每次最多处理 300 条。只有 `-y` 参数会执行删除，删除后无法恢复。

`^model` 写入 `bot.model`；`^mention`、`^chat` 和 `^autopost` 分别写入 `reply.mention`、`reply.chat` 和 `autopost.rotation` / `autopost.schedule`；名单命令写入 `reply.whitelist` 与 `reply.blacklist`。这些修改都会持久化到 `data/settings.yaml`，并作为单一事实来源。`^model reset` 会移除 `bot.model`，回到默认模型。

`^reload <插件名>` 会先重新读取 `data/settings.yaml`，再用 `plugins.<插件名>` 重载插件。重载失败时该插件被禁用并返回提示，详情记录在日志中，其他插件不受影响。

除 `connect` 和全部 `timeline.*` 外，设置变更会被运行中的机器人热更新；需要重启的字段会在 TUI 中用 `↻` 标记，并在日志中提示。

## 内容操作命令

以 `/` 开头的命令可能调用模型服务：

| 命令 | 场景 | 说明 |
| --- | --- | --- |
| `/post [-p\|-h\|-f] [-l] <主题>` | 私聊 | 生成并发布一篇帖子 |
| `/img <描述>` | 私聊、群聊（需提及机器人）、提及 | 生成图片并上传至机器人 Drive |

这两项命令同样要求管理员授权。未授权用户会收到拒绝提示。`/post` 在非私聊场景不会执行。

## 授权建议

```yaml
bot:
  admins:
    - "9abcdef012345678"
    - "operator@example.com"
```

用户 ID 最稳定。跨实例账号应使用完整的 `username@host`。管理员自动不受回复间隔和轮数限制；加入回复白名单则不会获得管理权限。
