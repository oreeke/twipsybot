---
title: KeyAct 关键词回复
description: 使用 KeyAct 为 TwipsyBot 配置精确关键词匹配和固定回复。
---

# KeyAct：关键词回复

KeyAct 对提及或聊天文本进行精确匹配，并直接返回预设内容，不调用模型。适合帮助入口、状态说明、邀请码和固定问答。

## 配置

```yaml
keyact:
  enabled: true
  priority: 990
  mention_enabled: true
  chat_enabled: true
  case_sensitive: false
  rules:
    - keywords: ["ping"]
      response: "pong"
    - keywords: ["帮助", "help"]
      response: "[帮助文档](https://docs.example.com)"
    - keywords: ["内部入口"]
      response: "仅管理员可见的信息"
      enabled: false
```

## 匹配方式

- 机器人提及标记会先从文本中移除。
- 清理后的整段文本必须等于某个关键词，不是包含匹配。
- 全局 `case_sensitive` 控制大小写；单条规则也可以设置同名字段覆盖。
- `keywords` 可以是字符串，也可以是字符串列表。
- `enabled: false` 可以临时关闭单条规则。

KeyAct 默认优先级高于 Vision，因此“图片 + 精确关键词”通常先命中 KeyAct。希望图片优先时可以调整优先级。

## 注意事项

回复内容会原样发送。不要在配置中放置访问令牌、私密链接或其他不应公开的信息。关键词很多时，应避免容易误触发的短词。
