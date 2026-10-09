---
title: KeyAct 关键词回复
description: 使用 KeyAct 为 TwipsyBot 配置精确关键词匹配与固定回复。
---

# KeyAct：关键词回复

精确匹配提及或聊天文本，返回预设内容，不调用模型。适合帮助入口、状态说明与固定问答。

## 配置

```yaml
plugins:
  keyact:
    enabled: true
    mention: true
    chat: true
    case_sensitive: false
    rules: |-
      ping = pong
      帮助, help = [帮助文档](https://docs.example.com)
      # 邀请码 = 7MFNHJSSAASZ3
```

`rules` 每行一条，TUI 中直接逐行填写：

- `关键词 = 回复`，以第一个 `=` 分隔。
- 多个关键词用 `,`、`，` 或 `|` 分隔。
- 回复中的 `\n` 转为换行。
- 空行忽略。

## 匹配

- 先移除 `@` 提及，再要求整段文本与关键词完全相等。
- `case_sensitive` 控制大小写。
- 优先级高于 Vision，带图片的精确关键词会先命中 KeyAct。

::: warning
回复原样发送，勿放入令牌或私密链接；避免易误触的短关键词。
:::
