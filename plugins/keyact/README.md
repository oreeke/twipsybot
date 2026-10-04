## KeyAct

精确匹配关键词直接回复，不调用模型。

```yaml
plugins:
  keyact:
    enabled: true
    rules: |-
      # 关键词 = 回复
      ping, hi = pong
```

完整说明见[文档](https://twipsybot.oreeke.com/user-guide/plugins/keyact)。
