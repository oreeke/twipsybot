---
title: 管理命令
description: TwipsyBot 的状态查询、模型切换、功能开关、名单管理、帖子清理与手动发布命令。
---

# 管理命令

在与机器人的聊天中使用，仅限 `bot.admins`。

## `^` 管理命令

| 命令 | 作用 |
| --- | --- |
| `^help` | 命令列表 |
| `^status` | 运行状态 |
| `^model [名称\|reset]` | 查看、切换（同端点）或恢复默认模型 |
| `^autopost rotation\|schedule\|off` | 切换自动发帖模式 |
| `^autopost reset` | 重置当天轮转计数 |
| `^mention on\|off` | 提及开关 |
| `^chat on\|off` | 聊天开关 |
| `^whitelist` · `^blacklist` | 名单管理 |
| `^clean posts <天数> [-y]` | 预览 / 删除超期无互动帖子 |
| `^reload <插件名>` | 重新读取设置并重载插件 |

模型、开关与名单的修改写入 `data/settings.yaml`，作为唯一事实来源，重启后保留。

### 名单

```text
^whitelist list
^whitelist add <用户...>
^whitelist del <用户...>
^whitelist set <用户...>
^whitelist clear
```

`set` 替换整个名单，`clear` 保存空名单；`^blacklist` 语法相同。

### 清理帖子

- 仅处理机器人的独立普通帖子，排除回复、转帖、提及、频道、投票、置顶、Clip 及已有互动的帖子。
- 不带 `-y` 仅预览；带 `-y` 时重新检查互动后由旧到新删除，**不可恢复**。
- 每次最多 300 条；受 Misskey 限制，每小时最多 300 次删除，间隔至少 1 秒。

### 重载插件

`^reload` 失败时该插件被禁用并返回提示，详情见日志，其他插件不受影响。

## `/` 内容命令

| 命令 | 场景 | 作用 |
| --- | --- | --- |
| `/post [-p\|-h\|-f] [-l] <主题>` | 私聊 | 生成并发帖 |
| `/img <描述>` | 私聊、群聊（需提及）、提及 | 生成图片并回复 |

详见[自动发帖与图片生成](posting.md)。

## 授权

```yaml
bot:
  admins:
    - "9abcdef012345678"
    - "operator@example.com"
```

推荐用户 ID，跨实例账号使用 `username@host`。管理员自动豁免回复限制。
