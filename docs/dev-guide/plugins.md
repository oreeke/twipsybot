---
title: 插件开发
description: 使用 TwipsyBot 插件 API 创建事件 Hook、配置、存储和自动发帖扩展。
---

# 插件开发

插件位于 `plugins/<name>/`。目录、模块和配置键使用相同的小写名称：

```text
plugins/example/
├── __init__.py
├── example.py
└── config.yaml
```

插件类名应为目录名转换成大驼峰后加 `Plugin`，例如 `example_bot/example_bot.py` 对应 `ExampleBotPlugin`。`api_version` 不兼容时，插件不会加载。

集中配置 `plugins/config.yaml` 中存在同名条目时，会完整取代插件目录的 `config.yaml`，两处不会合并。

## 最小插件

```python
from twipsybot.plugin import (
    PLUGIN_API_VERSION,
    MentionEvent,
    PluginBase,
    PluginConfig,
)


class ExampleConfig(PluginConfig):
    response: str = "收到"


class ExamplePlugin(PluginBase):
    api_version = PLUGIN_API_VERSION
    config_class = ExampleConfig
    settings: ExampleConfig

    async def on_mention(self, event: MentionEvent):
        if event.text.strip() != "ping":
            return None
        return self.handled(self.settings.response)
```

```yaml
enabled: true
priority: 100
response: "pong"
```

以上是 `plugins/example/config.yaml`。写入集中配置时使用：

```yaml
example:
    enabled: true
    priority: 100
    response: "pong"
```

插件必须声明当前 `PLUGIN_API_VERSION`。所有被覆盖的生命周期和 Hook 方法必须是异步方法。`PluginConfig` 使用 Pydantic 验证，`enabled`、`priority` 等框架字段不会进入配置模型。验证后的配置通过 `self.settings` 读取，配置实例只读。

复杂配置可以使用 Pydantic 字段和模型验证器集中约束：

```python
from pydantic import Field

from twipsybot.plugin import PluginConfig


class ExampleConfig(PluginConfig):
    response: str = "收到"
    max_length: int = Field(200, ge=1, le=3000)
```

## 生命周期

| 方法 | 时机 |
| --- | --- |
| `initialize()` | 加载后初始化资源；只有返回 `True` 才继续启用 |
| `on_startup()` | 所有核心服务准备完成后 |
| `on_shutdown()` | 停止接收新 Hook 后 |
| `cleanup()` | 释放资源，初始化失败时也可能调用 |

通过 `_register_resource(resource)` 注册带 `close()` 的资源，基类会在清理时关闭。插件自身创建的任务应在 `on_shutdown()` 中停止，并在 `cleanup()` 中完成最终释放。

## 事件 Hook

| Hook | 事件类型 | 可返回结果 |
| --- | --- | --- |
| `on_message` | `MessageEvent` | `HandledResult \| None` |
| `on_mention` | `MentionEvent` | `HandledResult \| None` |
| `on_notification` | `NotificationEvent` | `None` |
| `on_timeline_note` | `TimelineNoteEvent` | `None` |
| `on_auto_post` | `AutoPostEvent` | `AutoPostResult \| PromptModificationResult \| None` |

事件字段如下：

| 类型 | 字段 |
| --- | --- |
| `MessageEvent` | `id text user room_id files raw` |
| `MentionEvent` | `id text cw user files raw` |
| `NotificationEvent` | `id type user raw` |
| `TimelineNoteEvent` | `id text cw user channel files raw` |
| `AutoPostEvent` | `triggered_at` |
| `UserRef` | `id username host handle` |
| `FileRef` | `id mime_type url thumbnail_url raw` |

`NotificationEvent.id`、`UserRef.id`、`cw`、`host` 和文件 URL 等字段可能为空；消息、提及和时间线事件的 `id` 始终是非空字符串。事件数据类不可变，每个插件会收到独立的 `raw` 副本，但嵌套值并非深度只读。应将 `raw` 视为只读后备数据，不要依赖其长期兼容性。

`UserRef.handle` 会自动组合为 `username@host`，本地用户仅为 `username`。时间线 `channel` 通常为 `homeTimeline`、`localTimeline`、`hybridTimeline`、`globalTimeline` 或 `antenna`。

插件按 `priority` 从高到低调用。`on_message` 或 `on_mention` 返回 `HandledResult` 后，后续插件和默认 AI 不再执行；返回 `None` 则继续。通知和时间线 Hook 仅用于观察，所有插件都会收到。返回值必须严格符合公共 TypedDict，额外字段会使结果失效。

自动发帖可以直接返回内容：

```python
return {"contents": ["第一篇", "第二篇"], "visibility": "home"}
```

也可以修改核心提示词：

```python
return {"prompt": "围绕开源维护写一篇短文。"}
```

`contents`、其中的文本和 `prompt` 必须非空。`contents` 会直接发布而不调用 AI；`prompt` 会放在全局自动发帖提示词之前。两种结果仍受每日上限控制。`PromptModificationResult` 还可提供分钟级整数 `timestamp`。

## PluginContext

`self.context` 提供：

- `name`：插件名。
- `config`：原始插件配置的只读映射。
- `storage`：当前插件命名空间内的 `get`、`set`、`delete`。
- `misskey`：发帖、转帖、反应、聊天、天线和 Drive 服务。
- `openai`：文本、聊天和 Moderations API。
- `bot`：机器人账号信息、用户锁和天线解析。

只从 `twipsybot.plugin` 导入公共类型。不要导入 `twipsybot.bot`、`twipsybot.clients` 或 `twipsybot.db`，这些模块不保证插件兼容性。

### Storage

```python
value = await self.context.storage.get("key")
await self.context.storage.set("key", "value")
deleted = await self.context.storage.delete("key")
```

键和值均为字符串，`delete(None)` 会清空当前插件的存储并返回删除数量。

### Misskey 与 Drive

| 接口 | 用途 |
| --- | --- |
| `misskey.create_note(...)` | 发帖或回复 |
| `misskey.create_renote(...)` | 转帖或引用 |
| `misskey.create_reaction(...)` | 添加反应 |
| `misskey.send_message(...)` | 向用户发送私信 |
| `misskey.list_antennas()` | 获取天线 |
| `misskey.instance_url` | 读取实例地址 |
| `misskey.drive.show_file(...)` | 获取文件信息 |
| `misskey.drive.fetch_bytes(...)` | 从 URL 下载文件 |
| `misskey.drive.download_bytes(...)` | 下载 Drive 文件 |
| `misskey.drive.upload_bytes(...)` | 上传文件 |

`visibility` 支持 `public`、`home` 和 `followers`。Drive 上传结果中的 `id` 是文件 ID。当前 `create_note` 和 `HandledResult` 不支持附带文件 ID。

### OpenAI 与 Bot

| 接口 | 用途 |
| --- | --- |
| `openai.generate_text(...)` | 单轮文本生成，可请求 JSON Object |
| `openai.generate_chat(...)` | 多轮或多模态生成 |
| `openai.moderate_texts(...)` | 批量审核文本 |
| `openai.system_prompt / max_tokens / temperature` | 读取全局生成参数 |
| `openai.uses_responses_api` | 判断当前消息格式 |
| `bot.user_id / username` | 读取机器人身份 |
| `bot.actor_lock(...)` | 串行处理同一用户 |
| `bot.load_antenna_selectors()` | 读取天线选择器 |
| `bot.resolve_antenna_ids(...)` | 解析天线 ID |

`moderate_texts` 使用 `omni-moderation-latest`。自定义 OpenAI 兼容端点需要支持 `/moderations`。

持久化值必须是字符串；复杂结构可使用 JSON 编码。涉及同一用户的读改写操作时使用：

```python
async with self.context.bot.actor_lock(event.user.id, event.user.handle):
    value = await self.context.storage.get("state")
    await self.context.storage.set("state", value or "initialized")
```

## 失败处理

Hook 异常或超时只隔离本次调用，不会终止其他插件。插件仍应捕获可预期的网络或解析错误并记录清楚。不要吞掉 `asyncio.CancelledError`。长时间 I/O 应设置自身超时，并确保整个 Hook 能在 60 秒内返回。

生命周期方法超时 30 秒。初始化或启动失败时，插件会执行 `cleanup` 并被禁用；Bot 停止时会先执行 `on_shutdown`，再执行 `cleanup`。

插件类可以设置 `description`，用于状态和插件信息展示。

## API 边界

公共 API 包括 `twipsybot.plugin` 导出的 `PluginBase`、`PluginContext`、事件类型、结果类型和服务 `Protocol`，以及本文明确说明的 `PluginBase` 辅助方法。

内部 API 包括其他 `twipsybot.*` 模块、`PluginManager`、底层对象、未文档化的私有属性和事件 `raw`。

插件 API v1 只进行向后兼容的扩展。删除、重命名公共 API 成员或改变其语义属于破坏性变更，需要提升 API 主版本号。
