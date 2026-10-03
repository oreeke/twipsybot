## KeyAct 插件

### 功能描述

匹配自定义关键词直接回复，绕过 AI。

### 使用方法

在 `twipsybot cfg` 中启用 KeyAct，或编辑 `data/settings.yaml`：

```yaml
plugins:
  keyact:
    enabled: true
    rules: |-
      # 每行一条：关键词 = 回复
      ping, hi = pong
```

`rules` 每行一条规则，`=` 前为关键词（用 `,`、`，` 或 `|` 分隔），之后为回复内容，回复中的 `\n` 表示换行；`#` 开头的行为注释。匹配到任一关键词时直接回复。
