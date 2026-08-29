## 插件开发

Plugin API v1。只从 `twipsybot.plugin` 导入公共接口。

### 最小示例

```text
plugins/echo/
├── config.yaml
└── echo.py
```

```python
from twipsybot.plugin import PLUGIN_API_VERSION, MessageEvent, PluginBase


class EchoPlugin(PluginBase):
    api_version = PLUGIN_API_VERSION

    async def on_message(self, event: MessageEvent):
        return self.handled(f"echo: {event.text}")
```

```yaml
enabled: true
priority: 100
```

目录名、文件名和类名应对应。`api_version` 不兼容时拒绝加载。

### Context

通过 `self.context` 使用：

| 字段 | 内容 |
| --- | --- |
| `name` | 插件名 |
| `config` | 插件配置 |
| `storage` | 插件私有存储 |
| `misskey` | Misskey 服务 |
| `openai` | AI 服务 |
| `bot` | Bot 控制接口 |

只承诺这些 Protocol 中声明的成员稳定。

事件字段：

| 类型 | 字段 |
| --- | --- |
| `MessageEvent` | `id text user room_id files raw` |
| `MentionEvent` | `id text cw user files raw` |
| `NotificationEvent` | `id type user raw` |
| `TimelineNoteEvent` | `id text cw user channel files raw` |
| `AutoPostEvent` | `triggered_at` |
| `UserRef` | `id username host handle` |
| `FileRef` | `id mime_type url thumbnail_url raw` |

### Hook

| 方法 | 输入 | 返回 |
| --- | --- | --- |
| `on_message` | `MessageEvent` | `HandledResult | None` |
| `on_mention` | `MentionEvent` | `HandledResult | None` |
| `on_notification` | `NotificationEvent` | `None` |
| `on_timeline_note` | `TimelineNoteEvent` | `None` |
| `on_auto_post` | `AutoPostEvent` | `AutoPostResult | PromptModificationResult | None` |

按 `priority` 降序调用；message/mention 返回 handled 后短路。事件字段只读；`raw` 是隔离副本且不保证兼容。

```python
return self.handled("已处理")
return {"contents": ["帖子"], "visibility": "home"}
return {"prompt": "以天气为主题，"}
```

`contents`、其中的帖子文本和 `prompt` 必须非空。

### 生命周期

```text
__init__ -> initialize -> on_startup -> hooks -> on_shutdown -> cleanup
```

方法均可缺省，但必须使用 `async def`；生命周期方法不得要求额外参数，Hook 必须能接收事件参数。生命周期超时 30 秒，Hook 超时 60 秒。

- 初始化或启动失败：cleanup 并禁用。
- disable：cleanup 并卸载。
- reload：cleanup 后创建新实例。
- Bot 停止：on_shutdown 后 cleanup。
- Hook 异常或超时只隔离本次调用。

`on_startup/on_shutdown` 仅对应 Bot 启停；运行期 enable/reload 只调用 initialize/cleanup。
shutdown、disable 和 reload 会等待正在执行的 Hook 完成。

### 边界

公共 API：`twipsybot.plugin` 导出的 Base、Context、事件、结果和服务 Protocol。

内部 API：其他 `twipsybot.*` 模块、`PluginManager`、底层对象、私有属性和事件 `raw`。

API v1 仅做向后兼容扩展；删除、改名或语义变化将提升主版本。
