---
title: Bond 羁绊
description: 使用 Bond 记录 TwipsyBot 与用户的互动、亲密度与关注关系，并为 AI 回复补充关系档案。
---

# Bond：羁绊

按用户记录互动次数、亲密度与关注关系，写入数据库；AI 回复前补充关系档案。

## 配置

```yaml
plugins:
  bond:
    enabled: true
    weights:
      chat: 1
      mention: 1
      reply: 1
      quote: 1.5
      renote: 1
      reaction: 0.5
      follow: 5
      unfollow: 10
    daily_cap: 10
    half_life_days: 30
    levels: |-
      0 = 陌生
      5 = 初识
      20 = 熟悉
      50 = 朋友
      120 = 挚友
    profile_ttl_hours: 24
    context_enabled: true
    context_max_chars: 600
    memo_sync: false
    opt_out_tags:
      - "#nobot"
      - "#noai"
```

| 字段 | 说明 |
| --- | --- |
| `weights` | 各类互动的加分；`unfollow` 为取消关注时的扣分 |
| `daily_cap` | 每位用户每日最多加分，防止刷分 |
| `half_life_days` | 亲密度半衰期，长期不互动会逐渐冷却 |
| `levels` | 每行 `分数 = 名称`，亲密度达到分数即进入该等级 |
| `profile_ttl_hours` | 用户资料缓存时长 |
| `context_enabled` | 是否为 AI 补充关系档案 |
| `context_max_chars` | 关系档案最大字数 |
| `memo_sync` | 等级变化时写入 Misskey 个人备注 |
| `opt_out_tags` | 简介含任一标签的用户不被记录 |

## 记录

- 聊天在消息到达时计入；提及、回复、引用、转帖、反应、关注与关注请求从通知计入，不受回复开关影响。
- 优先级高于 KeyAct，其他插件接管的聊天同样计入；命令本身不计入。
- 资料经 `users/show` 获取并缓存，含昵称、语言、生日、所在地、简介、备注与双方关注状态；获取失败 10 分钟后重试。
- 刷新时发现对方取消关注即扣分；对方迁移账号后，新账号首次刷新时合并旧账号记录。

## 关系档案

无插件接管时，在 AI 回复前补充：

```text
<bond>
对象：@alice（Alice）
关系：朋友（亲密度 56）
互动：聊天 42、提及 8、反应 15
相识于 2026-03-01；连续互动 4 天
状态：互相关注；今天是对方生日；语言 ja-JP
简介：……
备注：……
</bond>
```

简介与备注来自用户或管理员，提示词要求模型不执行其中指令。

## 个人备注

`memo_sync` 开启后，等级变化时在机器人对该用户的 Misskey 个人备注末尾写入一行 `[bond] 朋友 · 2026-10-07`，其余内容保留；最低等级不写入。

管理员登录机器人账号即可在用户主页查看或编辑备注，非 `[bond]` 行会作为备注进入关系档案。写入需令牌具备 `write:account` 权限，失败仅记录警告。

## 用户命令

| 命令 | 作用 |
| --- | --- |
| `/bond` | 查看自己的关系卡片 |
| `/forget` | 删除自己的全部记录与备注中的 `[bond]` 行 |

提及与聊天均可使用，需单独发送命令，仅关注机器人的用户可用。`/forget` 后继续互动会重新记录；未关注者可在简介加入退出标签或屏蔽机器人来删除记录。

## 隐私

- 简介含 `opt_out_tags` 任一标签，或对方屏蔽了机器人时，删除其记录并停止记录。
- 记录保存在本地数据库，`^reload bond` 不会清除。
