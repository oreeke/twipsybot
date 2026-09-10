---
title: 管理命令
description: 查询 TwipsyBot 的状态、模型、功能开关、用户访问名单，以及清理和手动发布帖子。
---

# 管理命令

管理命令在与机器人的聊天页面中使用。

只有 `bot.admin.allowed_users` 中的用户 ID 或账号可以执行。

## 本地管理命令

以 `^` 开头的命令直接在机器人进程中处理：

| 命令 | 作用 |
| --- | --- |
| `^help` | 查看可用命令 |
| `^status` | 机器人状态 |
| `^model` | 查看当前模型 |
| `^model <模型名>` | 切换模型（相同 `api_base`） |
| `^model reset` | 恢复默认模型 |
| `^autopost on\|off` | 自动发帖开关 |
| `^autopost reset` | 重置当天发帖计数 |
| `^clean posts <天数>` | 预览超过指定时间未被互动的帖子 |
| `^clean posts <天数> -y` | 确认删除预览范围内符合条件的帖子 |
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
^whitelist reset
```

`blacklist` 使用相同语法。`set` 会替换整个名单，`clear` 保存空名单，`reset` 删除运行时覆盖并恢复启动配置。

`clean posts` 只处理机器人的独立普通帖子，排除回复、转帖、提及、频道、投票、置顶、Clip 及已有回复、转帖或反应的帖子。确认删除前会重新检查互动状态，并按从旧到新的顺序删除。Misskey API 每小时最多接受 300 次删除请求，且请求间隔至少 1 秒。命令每次最多处理 300 条。只有 `-y` 参数会执行删除，删除后无法恢复。

模型和名单修改保存在 SQLite 中。功能开关只在当前进程中有效，重启后恢复 YAML 或环境变量配置。

## 内容操作命令

以 `/` 开头的命令可能调用模型服务：

| 命令 | 场景 | 说明 |
| --- | --- | --- |
| `/post [-p\|-h\|-f] [-l] <主题>` | 私聊 | 生成并发布一篇帖子 |
| `/img <描述>` | 私聊、群聊、提及 | 生成图片并上传至机器人 Drive |

这两项命令同样要求管理员授权。未授权用户会收到拒绝提示。`/post` 在非私聊场景不会执行。

## 授权建议

```yaml
bot:
  admin:
    allowed_users:
      - "9abcdef012345678"
      - "operator@example.com"
```

用户 ID 最稳定。跨实例账号应使用完整的 `username@host`。管理员名单与普通回复白名单相互独立。
