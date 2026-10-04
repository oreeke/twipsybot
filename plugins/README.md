## 插件开发

Plugin API v3，只从 `twipsybot.plugin` 导入公共接口。完整 API 见[插件开发](https://twipsybot.oreeke.com/dev-guide/plugins)。

### 最小示例

`plugins/echo/plugin.py`：

```python
from twipsybot.plugin import MessageEvent, PluginBase


class EchoPlugin(PluginBase):
    api_version = 3
    priority = 100

    async def on_message(self, event: MessageEvent):
        return self.handled(f"echo: {event.text}")


plugin = EchoPlugin
```

```yaml
plugins:
  echo:
    enabled: true
```

- 模块级 `plugin` 导出插件类；单文件加载，不支持相对导入，多模块插件用 Entry Points。
- `api_version` 写字面量；覆盖的生命周期与 Hook 须为 `async def`。
- 自定义配置继承 `PluginConfig` 并挂到 `config_class`，经只读的 `self.settings` 访问。

### 第三方包

```toml
[project.entry-points."twipsybot.plugins"]
echo = "twipsybot_echo:plugin"
```

入口名即插件键，安装后仍需在 `plugins.<name>` 中启用。

### Hook

| 方法 | 输入 | 返回 |
| --- | --- | --- |
| `on_message` | `MessageEvent` | `HandledResult \| None` |
| `on_mention` | `MentionEvent` | `HandledResult \| None` |
| `on_notification` | `NotificationEvent` | `None` |
| `on_timeline_note` | `TimelineNoteEvent` | `None` |
| `on_auto_post` | `AutoPostEvent` | `AutoPostResult \| PromptModificationResult \| None` |
| `on_auto_post_published` | `str` | `None` |

按 `priority` 降序调用，`HandledResult` 终止后续插件与默认 AI。

```text
__init__ -> initialize -> on_startup -> hooks -> on_shutdown -> cleanup
```
