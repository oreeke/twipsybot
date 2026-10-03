---
title: KeyAct 关键词回复
description: 使用 KeyAct 为 TwipsyBot 配置精确关键词匹配和固定回复。
---

# KeyAct：关键词回复

KeyAct 对提及或聊天文本进行精确匹配，并直接返回预设内容，不调用模型。

适合帮助入口、状态说明、邀请码和固定问答。

## 配置

通过 `twipsybot cfg` 配置 KeyAct，或在 `data/settings.yaml` 中写入：

```yaml
plugins:
  keyact:
    enabled: true
    mention_enabled: true
    chat_enabled: true
    case_sensitive: false
    rules: |-
      ping = pong
      帮助, help = [帮助文档](https://docs.example.com)
      # 内部入口 = 暂时停用的规则
```

在 `twipsybot cfg` 的 `rules` 框中直接逐行填写，不需要缩进或 `-` 符号：

- 每行一条规则，第一个 `=` 前是关键词，之后是回复内容。
- 多个关键词用 `,`、`，` 或 `|` 分隔。
- 回复中的 `\n` 会转换为换行。
- `#` 开头的行是注释，可用来临时停用规则；空行会被忽略。

默认优先级为 `990`，通常无需填写。

## 匹配方式

- 文本中的 `@` 提及标记会先被移除。
- 清理后的整段文本必须等于某个关键词，不是包含匹配。
- `case_sensitive` 控制大小写。

KeyAct 默认优先级高于 Vision，因此“图片 + 精确关键词”通常先命中 KeyAct。希望图片优先时可以调整优先级。

## 注意事项

回复内容会原样发送。不要在配置中放置访问令牌、私密链接或其他不应公开的信息。关键词很多时，应避免容易误触发的短词。
