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

### 类型化配置

有自定义配置时，继承 `PluginConfig` 声明默认值和约束，再通过
`config_class` 挂载。`enabled`、`priority` 等框架字段会自动忽略，配置对象只读。

```python
from pydantic import Field

from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase, PluginConfig


class EchoConfig(PluginConfig):
    prefix: str = "echo"
    max_length: int = Field(200, ge=1, le=3000)


class EchoPlugin(PluginBase):
    api_version = PLUGIN_API_VERSION
    config_class = EchoConfig
```

简单插件无需定义配置类；复杂格式可使用 Pydantic 的字段或模型验证器集中处理。

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

#### Storage

```python
value = await self.context.storage.get("key")
await self.context.storage.set("key", "value")
await self.context.storage.delete("key")  # key=None 时清空本插件数据
```

存储按插件名隔离，键和值均为字符串；`delete` 返回删除数量。

#### Misskey 与 Drive

| 接口 | 用途 |
| --- | --- |
| `misskey.create_note(text, visibility, reply_id, local_only, validate_reply)` | 发帖或回复 |
| `misskey.create_renote(note_id, visibility, text, local_only)` | 转帖或引用 |
| `misskey.create_reaction(note_id, reaction)` | 添加反应 |
| `misskey.send_message(user_id, text)` | 向用户发送私信 |
| `misskey.list_antennas()` | 获取天线 |
| `misskey.instance_url` | 实例地址 |
| `misskey.drive.show_file(file_id)` | 获取文件信息 |
| `misskey.drive.fetch_bytes(url, max_bytes=...)` | 从 URL 下载 |
| `misskey.drive.download_bytes(file_id, thumbnail=..., max_bytes=...)` | 下载 Drive 文件 |
| `misskey.drive.upload_bytes(data, name=..., content_type=...)` | 上传文件 |

`visibility` 可为 `public`、`home` 或 `followers`。上传结果中的 `id` 是文件 ID。
当前 `create_note` 和 handled 回复尚不支持附带文件 ID。

#### OpenAI 与 Bot

| 接口 | 用途 |
| --- | --- |
| `openai.generate_text(prompt, system_prompt, max_tokens, temperature, json_output)` | 单轮生成，可选 JSON Object 输出 |
| `openai.generate_chat(messages, max_tokens, temperature)` | 多轮或多模态生成 |
| `openai.moderate_texts(texts)` | 批量审核文本，按输入顺序返回命中的类别集合 |
| `openai.system_prompt / max_tokens / temperature` | 读取全局生成参数 |
| `openai.uses_responses_api` | 判断当前消息格式 |
| `bot.user_id / username` | 机器人身份 |
| `bot.actor_lock(user_id, username)` | 串行处理同一用户 |
| `bot.load_antenna_selectors()` | 读取天线选择器 |
| `bot.resolve_antenna_ids(selectors)` | 将选择器解析为天线 ID |

`moderate_texts` 使用 `omni-moderation-latest`；自定义 OpenAI 兼容端点需要支持
`/moderations`。

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

`id`、`cw`、`host`、文件 URL 等字段可能为空。`UserRef.handle` 会自动组合为
`username@host`；本地用户仅为 `username`。时间线 `channel` 通常为
`homeTimeline`、`localTimeline`、`hybridTimeline`、`globalTimeline` 或 `antenna`。
`files` 可配合 Drive 接口读取；`raw` 仅用于缺失的原始字段。

### Hook

| 方法 | 输入 | 返回 |
| --- | --- | --- |
| `on_message` | `MessageEvent` | `HandledResult | None` |
| `on_mention` | `MentionEvent` | `HandledResult | None` |
| `on_notification` | `NotificationEvent` | `None` |
| `on_timeline_note` | `TimelineNoteEvent` | `None` |
| `on_auto_post` | `AutoPostEvent` | `AutoPostResult | PromptModificationResult | None` |

按 `priority` 降序调用。message/mention 返回 handled 后，后续插件和默认 AI
均不再执行；返回 `None` 则继续。notification/timeline_note 仅用于观察，所有插件
都会收到。事件字段只读；`raw` 是隔离副本且不保证兼容。

```python
return self.handled("已处理")
return {"contents": ["帖子"], "visibility": "home"}
return {"prompt": "以天气为主题，"}
```

`contents`、其中的帖子文本和 `prompt` 必须非空。

- `contents`：直接发帖，不调用 AI。
- `prompt`：追加自动发帖提示，由 AI 生成内容。
- `timestamp`：可选的分钟级时间戳，用于稳定生成输入。

返回字典必须严格符合对应 Result 类型，不能添加其他字段。自动发帖仍受全局每日限额限制。

### 生命周期

```text
__init__ -> initialize -> on_startup -> hooks -> on_shutdown -> cleanup
```

方法均可缺省，但必须 `async def`；生命周期方法不得要求额外参数，Hook 必须能接收事件参数。`initialize` 只有返回 `True` 才算成功。生命周期超时 30 秒，Hook 超时 60 秒。

- 初始化或启动失败：cleanup 并禁用。
- Bot 停止：on_shutdown 后 cleanup。
- Hook 异常或超时只隔离本次调用。

`context.config` 只读。配置优先读取 `plugins/config.yaml` 中的插件条目，否则读取
插件目录内的 `config.yaml`，两者不合并。修改配置后需重启 Bot。关闭时会等待正在
执行的 Hook 完成。插件类可设置 `description` 供插件信息展示。

### 边界

公共 API：`twipsybot.plugin` 导出的 Base、Context、事件、结果和服务 Protocol。

内部 API：其他 `twipsybot.*` 模块、`PluginManager`、底层对象、私有属性和事件 `raw`。

API v1 仅做向后兼容扩展；删除、改名或语义变化将提升主版本。
