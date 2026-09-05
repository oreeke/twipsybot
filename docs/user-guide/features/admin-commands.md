---
title: 聊天管理命令
description: 查询 TwipsyBot 的状态、模型、功能开关、用户访问名单和手动发布命令。
---

# 聊天管理命令

管理命令在与机器人的聊天页面中使用。只有 `bot.admin.allowed_users` 中的用户 ID 或账号可以执行。

## 本地管理命令

以 `^` 开头的命令直接在机器人进程中处理：

| 命令 | 作用 |
| --- | --- |
| `^help` | 查看当前可用命令 |
| `^status` | 查看运行时长、账号、模型、功能开关、插件和授权用户数量 |
| `^model` | 查看当前模型及已保存覆盖 |
| `^model <模型名>` | 在当前 `api_base` 下切换模型并保存 |
| `^model reset` | 删除模型覆盖，恢复启动配置 |
| `^autopost on\|off` | 切换当前进程的自动发帖状态 |
| `^autopost reset` | 重置当天自动发帖计数 |
| `^mention on\|off` | 切换当前进程的提及响应 |
| `^chat on\|off` | 切换当前进程的聊天响应 |
| `^whitelist ...` | 查看或修改回复白名单 |
| `^blacklist ...` | 查看或修改回复黑名单 |

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
