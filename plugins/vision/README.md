## 视觉插件

### 功能描述

理解 @提及或聊天中的图片并生成回复。

### 使用方法

在 `twipsybot cfg` 中启用 Vision，或编辑 `data/settings.yaml`：

```yaml
plugins:
  vision:
    enabled: true
```

在提及或聊天中发送图片，可附带问题或要求（例如：翻译图片文本 / 识别图片人物）。

仅发送图片时，将使用 `default_prompt` 作为提问内容。

依赖配置的 OpenAI 兼容接口与所选模型支持多模态输入。
