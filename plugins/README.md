## 插件开发

Plugin API v3。只从 `twipsybot.plugin` 导入公共接口。

完整开发流程另见[开发指南](../docs/dev-guide/plugins.md)。

### 最小示例

```text
plugins/echo/
├── config.yaml
└── plugin.py
```

```python
from twipsybot.plugin import MessageEvent, PluginBase


class EchoPlugin(PluginBase):
    api_version = 3

    async def on_message(self, event: MessageEvent):
        return self.handled(f"echo: {event.text}")


plugin = EchoPlugin
```

```yaml
enabled: true
priority: 100
```

本地插件使用 `plugin.py`，并通过模块级 `plugin` 导出插件类。入口按单文件加载，不支持相对导入；多模块插件应使用 Entry Points。

### 类型化配置

有自定义配置时，继承 `PluginConfig` 声明默认值和约束，再通过 `config_class` 挂载。`enabled`、`priority` 等框架字段不会进入配置模型。

```python
from pydantic import Field

from twipsybot.plugin import PluginBase, PluginConfig


class EchoConfig(PluginConfig):
    prefix: str = "echo"
    max_length: int = Field(200, ge=1, le=3000)


class EchoPlugin(PluginBase):
    api_version = 3
    config_class = EchoConfig
    settings: EchoConfig
```

### 第三方包

第三方插件可通过 Python Entry Points 分发，无需复制到 `plugins/`：

```toml
[project.entry-points."twipsybot.plugins"]
echo = "twipsybot_echo:plugin"
```

入口名称是集中配置中的插件键，入口值必须指向 `PluginBase` 子类。安装后仍需在 `plugins/config.yaml` 中显式启用：

```yaml
echo:
    enabled: true
    priority: 100
```

第三方包自行声明依赖和版本。

验证后的配置通过 `self.settings` 读取，配置实例只读。简单插件无需定义配置类；复杂格式可使用 Pydantic 的字段或模型验证器集中处理。

### PluginContext

通过 `self.context` 使用：

| 字段 | 内容 |
| --- | --- |
| `name` | 稳定插件 ID（本地目录名或 Entry Point 名称） |
| `config` | 原始插件配置的只读映射 |
| `storage` | 插件私有存储 |
| `misskey` | Misskey 服务 |
| `openai` | AI 服务 |
| `bot` | Bot 控制接口 |

仅服务 `Protocol` 中声明的成员属于稳定接口。不要导入 `twipsybot.bot`、`twipsybot.clients` 或 `twipsybot.db`，这些模块不保证插件兼容性。

#### Storage

```python
value = await self.context.storage.get("key")
await self.context.storage.set("key", "value")
deleted = await self.context.storage.delete("key")
```

存储按插件 ID 隔离，键和值均为字符串。`delete(None)` 会清空当前插件的存储并返回删除数量。

#### Misskey 与 Drive

| 接口 | 用途 |
| --- | --- |
| `misskey.create_note(text, visibility, reply_id, local_only)` | 发帖或回复 |
| `misskey.create_renote(note_id, visibility, text, local_only)` | 转帖或引用 |
| `misskey.create_reaction(note_id, reaction)` | 添加反应 |
| `misskey.send_message(user_id, text)` | 向用户发送私信 |
| `misskey.list_antennas()` | 获取天线 |
| `misskey.instance_url` | 实例地址 |
| `misskey.drive.show_file(file_id)` | 获取文件信息 |
| `misskey.drive.fetch_bytes(url, max_bytes=...)` | 从 URL 下载 |
| `misskey.drive.download_bytes(file_id, thumbnail=..., max_bytes=...)` | 下载文件 |
| `misskey.drive.upload_bytes(data, name=..., content_type=...)` | 上传文件 |

`visibility` 可为 `public`、`home` 或 `followers`。上传结果中的 `id` 是文件 ID。
当前 `create_note` 和 `HandledResult` 尚不支持附带文件 ID。

#### OpenAI 与 Bot

| 接口 | 用途 |
| --- | --- |
| `openai.generate_text(prompt, system_prompt, max_tokens, temperature, json_output)` | 单轮文本生成，可请求 JSON Object |
| `openai.generate_chat(messages, max_tokens, temperature)` | 多轮或多模态生成 |
| `openai.moderate_texts(texts)` | 批量审核文本，按输入顺序返回命中的类别集合 |
| `openai.system_prompt / max_tokens / temperature` | 读取全局生成参数 |
| `openai.uses_responses_api` | 判断当前消息格式 |
| `bot.user_id / username` | 机器人身份 |
| `bot.actor_lock(user_id, username)` | 串行处理同一用户 |
| `bot.load_antenna_selectors()` | 读取天线选择器 |
| `bot.resolve_antenna_ids(selectors)` | 将选择器解析为天线 ID |

`moderate_texts` 使用 `omni-moderation-latest`。自定义 OpenAI 兼容端点需要支持
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

`NotificationEvent.id`、`UserRef.id`、`cw`、`host`、文件 URL 等字段可能为空；消息、提及和时间线事件的 `id` 始终是非空字符串。`UserRef.handle` 会自动组合为
`username@host`；本地用户仅为 `username`。时间线 `channel` 通常为
`homeTimeline`、`localTimeline`、`hybridTimeline`、`globalTimeline` 或 `antenna`。
`files` 可配合 Drive 接口读取。每个插件收到独立的 `raw` 副本，但嵌套值并非深度只读。应将其视为只读后备数据，且不依赖其长期兼容性。

### Hook

| 方法 | 输入 | 返回 |
| --- | --- | --- |
| `on_message` | `MessageEvent` | `HandledResult \| None` |
| `on_mention` | `MentionEvent` | `HandledResult \| None` |
| `on_notification` | `NotificationEvent` | `None` |
| `on_timeline_note` | `TimelineNoteEvent` | `None` |
| `on_auto_post` | `AutoPostEvent` | `AutoPostResult \| PromptModificationResult \| None` |
| `on_auto_post_published` | `str` | `None` |

插件按 `priority` 从高到低调用。`on_message` 或 `on_mention` 返回 `HandledResult` 后，后续插件和默认 AI 不再执行；返回 `None` 则继续。通知和时间线 Hook 仅用于观察，所有插件都会收到。事件数据类不可变，`raw` 的隔离副本仅用于读取公共事件尚未提供的字段。

`on_auto_post_published(content)` 仅在当前插件通过 `AutoPostResult` 返回的内容成功发布后调用，可用于提交去重记录或轮换位置。发布失败时不会调用。

```python
return self.handled("已处理")
return {"contents": ["帖子"], "visibility": "home"}
return {"prompt": "以天气为主题，"}
```

`contents`、其中的帖子文本和 `prompt` 必须非空。

- `contents`：直接发帖，不调用 AI。
- `prompt`：放在全局自动发帖提示词之前，由 AI 生成内容。
- `timestamp`：可选的分钟级时间戳，用于稳定生成输入。

返回字典必须严格符合对应 Result 类型，不能添加其他字段。自动发帖仍受全局每日限额限制。

### 生命周期

```text
__init__ -> initialize -> on_startup -> hooks -> on_shutdown -> cleanup
```

生命周期方法和 Hook 均可不覆盖，覆盖时必须使用 `async def`。生命周期方法不得要求额外参数，事件 Hook 必须能接收事件参数，发布成功回调必须能接收内容字符串。`initialize` 只有返回 `True` 才算成功。生命周期超时 30 秒，Hook 超时 180 秒。

- 初始化或启动失败：调用 `cleanup` 并禁用。
- Bot 停止：先调用 `on_shutdown`，再调用 `cleanup`。
- Hook 异常或超时只隔离本次调用。

通过 `_register_resource(resource)` 注册带 `close()` 的资源，基类会在 `cleanup` 时关闭。插件自行创建的任务应在 `on_shutdown()` 中停止，并在 `cleanup()` 中完成最终释放。不要吞掉 `asyncio.CancelledError`，长时间 I/O 应设置自身超时。

`context.config` 是原始配置的只读映射。集中配置 `plugins/config.yaml` 中存在同名条目时，会完整取代插件目录的 `config.yaml`，两处不会合并。修改配置后需重启 Bot。关闭时最多等待 Hook 3 秒，随后取消。插件类可设置 `description` 供插件信息展示。

### API 边界

公共 API 包括 `twipsybot.plugin` 导出的 `PluginBase`、`PluginContext`、事件类型、结果类型和服务 `Protocol`，以及本文明确说明的 `PluginBase` 辅助方法。

内部 API 包括其他 `twipsybot.*` 模块、`PluginManager`、底层对象、未文档化的私有属性和事件 `raw`。

插件 API v3 只进行向后兼容的扩展。删除、重命名公共 API 成员或改变其语义属于破坏性变更，需要提升 API 主版本号。
