## 主题插件

### 功能描述

为自动发帖提供内容源。

### 使用方法

在 `twipsybot cfg` 中启用 Topics，或编辑 `data/settings.yaml`：

```yaml
plugins:
  topics:
    enabled: true
    source: txt
```

`source` 可选：

- `txt`
  - 像装填弹夹一样，每行一词，将自定义主题关键词写入 `prompts/topics.txt`
  - 插件有序装载关键词，拼接成提示前缀，AI 以此为题生成内容
- `rss`
  - `rss_list` 中添加 RSS 链接，机器人筛选最新动态作为帖子发布
  - RSS 拉取和发帖时机由自动发帖的轮转或定时设置控制
  - `rss_ai` 让 AI 生成总结或感想，前提是 RSS 包含摘要或接入的模型能预览 URL
