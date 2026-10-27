---
title: 扩展插件
description: 使用 TwipsyBot 插件 API 编写 Hook、工具、配置与存储。
---

# 扩展插件

本地插件位于 `plugins/<name>/plugin.py`，模块级 `plugin` 导出插件类；目录按包加载，可用相对导入。配置来自 `plugins.<name>`。

## 最小插件

```python
from twipsybot.plugin import MentionEvent, PluginBase, PluginConfig


class ExampleConfig(PluginConfig):
    response: str = "收到"


class ExamplePlugin(PluginBase):
    api_version = 4
    config_class = ExampleConfig
    settings: ExampleConfig

    async def on_mention(self, event: MentionEvent):
        if event.text.strip() != "ping":
            return None
        return self.handled(self.settings.response)


plugin = ExamplePlugin
```

```yaml
plugins:
  example:
    enabled: true
    response: "pong"
```

- `api_version` 写字面量；生命周期、Hook 与工具须为 `async`。
- 插件按目录名排序，其后是 Entry Point，依次调用。

## 配置

`PluginConfig` 基于 Pydantic，经只读的 `self.settings` 访问；`enabled` 不进入模型，TUI 据模型生成表单。`SecretStr` 字段只读自 `data/secrets.yaml`。

```python
from typing import Annotated

from pydantic import Field

from twipsybot.plugin import LineText, PluginConfig


class ExampleConfig(PluginConfig):
    max_length: int = Field(200, ge=1, le=3000)
    feeds: Annotated[tuple[str, ...], LineText] = ()
```

- `LineText`：TUI 中为多行文本，验证时拆为非空行并忽略 `#` 注释行。
- `When("mode", "a")` 加入 `Annotated`：TUI 仅在同级字段 `mode` 为 `a` 时显示该字段。

## Entry Points

```toml
[project.entry-points."twipsybot.plugins"]
example = "twipsybot_example:plugin"
```

入口名即插件 ID；与本地插件同名时本地优先；安装后仍需 `plugins.<name>.enabled: true`。

## 生命周期

| 方法 | 时机 |
| --- | --- |
| `initialize()` | 加载后初始化资源，返回 `True` 才启用 |
| `on_startup()` | 开始接收 Hook 前 |
| `on_shutdown()` | 停止接收新 Hook 后 |
| `cleanup()` | 释放资源，初始化失败时也会调用 |

- `_register_resource(resource)` 注册带 `close()` 的资源，清理时自动关闭。
- 重载（配置变更、`^plugin on|off`）会新建实例：运行时状态放实例内，跨重载的数据写入 `storage`，不用模块或类变量。

## Hook

| Hook | 事件 | 返回 |
| --- | --- | --- |
| `on_message` | `MessageEvent` | `HandledResult \| None` |
| `on_mention` | `MentionEvent` | `HandledResult \| None` |
| `on_context` | `MessageEvent \| MentionEvent` | `ContextResult \| None` |
| `on_notification` | `NotificationEvent` | `None` |
| `on_timeline_note` | `TimelineNoteEvent` | `None` |
| `on_auto_post` | `AutoPostEvent` | `AutoPostResult \| PromptModificationResult \| None` |
| `on_auto_post_published` | `str` | `None` |

| 类型 | 字段 |
| --- | --- |
| `MessageEvent` | `id text user room_id room_name files raw` |
| `MentionEvent` | `id text cw user files raw reply_id renote_id reply renote attachments kind mentions visibility` |
| `NoteRef` | `id text cw user files raw hidden` |
| `NotificationEvent` | `id type user note raw` |
| `TimelineNoteEvent` | `id text cw user channel files raw` |
| `AutoPostEvent` | `triggered_at` |
| `UserRef` | `id username host is_bot handle` |
| `FileRef` | `id mime_type url thumbnail_url name raw` |

- `on_message` / `on_mention` 返回 `HandledResult` 即终止后续插件与 AI；其余 Hook 不截断。
- `self.handled(response, file_id=None, followup=None)`：`followup` 为无参 `async` 函数，回复发出后在后台调用，其返回的 `HandledResult` 同样补发（只取 `response`、`file_id`）。不受 Hook 超时约束，异常只记录日志。
- 返回值须严格符合公共 TypedDict，多余字段使结果失效。
- 消息、提及与时间线事件的 `id` 非空；`NotificationEvent.id`、`UserRef.id`、`cw`、`host`、文件 URL 可能为空。
- `MentionEvent.reply` / `renote` 仅一层，不可用时为 `None`，仍可用 `reply_id` / `renote_id` 判断关系；`attachments` 按本帖、被引用帖、被回复帖合并并去重。
- 事件不可变；`raw` 为每个插件独立的副本，仅作后备。

### on_context

```python
return {
    "context": "<参考资料>",
    "text": "替换后的用户文本",
    "media": [{"type": "input_image", "image_url": "data:image/png;base64,..."}],
}
```

- 仅在没有插件接管时调用，所有插件依次调用；须自设超时并容忍失败。
- `context` 置于用户内容之前，多个以空行拼接；`text` 替换原文，取最先返回者；`media` 为 Responses 风格的多模态片段（Base64），合并后随用户消息发送。均不写入聊天历史。
- 至少返回一个字段；`context`、`text` 须为非空字符串，`media` 须为非空列表。

### on_auto_post

```python
return {
    "contents": ["第一篇", {"text": "带图", "file_ids": ["<Drive 文件 ID>"]}],
    "visibility": "home",
}
return {"prompt": "围绕开源维护写一篇短文。"}
```

- `contents` 直接发布，每项为文本或 `AutoPostItem`；`prompt` 置于 `autopost.prompt` 之前交给 AI。文本须非空。
- 多个插件返回时 `contents` 先于 `prompt`，同类取最先返回者；轮转模式受 `daily_max` 约束。
- `on_auto_post_published(content)` 仅在本插件的 `contents` 发布成功后调用，用于提交去重记录或推进轮换位置。

## 工具

`@tool` 声明模型可调用的能力。

```python
from typing import Annotated

from pydantic import Field

from twipsybot.plugin import MentionEvent, MessageEvent, PluginBase, tool


class ExamplePlugin(PluginBase):
    api_version = 4

    @tool("查询城市天气", access="followers", external=True)
    async def weather(
        self,
        event: MessageEvent | MentionEvent,
        city: Annotated[str, Field(description="城市名")],
    ) -> str:
        return "晴"


plugin = ExamplePlugin
```

- 签名为 `async def (self, event, ...)`；其余参数生成 JSON Schema，不接受未声明字段。
- 工具名为 `<插件 ID>_<方法名>`；消息以 `/<方法名>` 或 `/<工具名>` 开头时强制调用。
- 返回 `str`，或 `self.handled(...)`：`response` 交给模型，`file_id` 附加到最终回复，`followup` 在回复后补发。
- 结果截断至 8000 字符，单次调用 60 秒；参数错误、异常与超时以错误文本返回模型。
- 参数全部必填才满足 Responses 的 strict 模式，带默认值的按非 strict 处理。

| 参数 | 含义 |
| --- | --- |
| `access="all"`（默认） | 所有用户 |
| `access="followers"` | 关注机器人的用户与管理员 |
| `access="admin"` | `bot.admins` |
| `external=True` | 返回网页等外部内容；同一轮读取后，`effect` 工具不再提供 |
| `effect=True` | 有对外影响（发帖、花费额度、删除数据）；引用或回复他人的帖子、群聊历史含他人发言时，该轮不提供 |

## PluginContext

| `self.context.*` | 说明 |
| --- | --- |
| `name` | 插件 ID |
| `config` | `plugins.<name>` 的只读映射 |
| `storage` | 按插件隔离的字符串存储 |
| `misskey` | 发帖、转帖、反应、聊天、用户、天线与 Drive |
| `openai` | 文本、聊天、审核与图片生成 |
| `http` | 下载外部 URL，拒绝内网目标 |
| `bot` | 机器人身份、管理员判断、用户锁与天线解析 |

### Storage

```python
value = await self.context.storage.get("key")
await self.context.storage.set("key", "value")
deleted = await self.context.storage.delete("key")
```

键值均为字符串，复杂结构用 JSON 编码；`delete(None)` 清空本插件存储。同一用户的读改写需加锁：

```python
async with self.context.bot.actor_lock(event.user.id, event.user.handle):
    ...
```

### Misskey 与 Drive

| 接口 | 用途 |
| --- | --- |
| `misskey.create_note(text, visibility, reply_id, local_only, file_ids)` | 发帖或回复，`file_ids` 至多 16 个 |
| `misskey.create_renote(note_id, visibility, text, local_only)` | 转帖或引用 |
| `misskey.create_reaction(note_id, reaction)` | 添加反应 |
| `misskey.send_message(user_id, text, file_id)` | 发送私信 |
| `misskey.show_user(user_id)` | 用户详情，含个人备注与双方关系 |
| `misskey.update_user_memo(user_id, memo)` | 写入个人备注，`None` 或空串删除；需 `write:account` |
| `misskey.list_antennas()` · `get_note(note_id)` · `instance_url` | 天线、帖子、实例地址 |
| `misskey.call_api(endpoint, params, retry=False)` | 调用未封装的 REST 端点 |
| `misskey.drive.show_file(file_id)` | 文件信息 |
| `misskey.drive.fetch_bytes(url, max_bytes=...)` | 从 URL 下载，默认上限 32 MiB |
| `misskey.drive.download_bytes(file_id, thumbnail=..., max_bytes=...)` | 下载文件 |
| `misskey.drive.upload_bytes(data, name=..., content_type=...)` | 上传，结果 `id` 为文件 ID |

`visibility` 取 `public`、`home`、`followers`。文件须属于机器人的 Drive。

```python
notes = await self.context.misskey.call_api(
    "notes/search", {"query": "misskey", "limit": 10}
)
```

- `endpoint` 为 `notes/search` 这类相对路径；`params` 为 JSON 请求体，不得含 `i`，否则抛出 `ValueError`。
- 默认不重试；仅幂等读取才传 `retry=True`（见 [Misskey](misskey.md#rest)）。
- 失败抛出 `APIResponseError`，含 `status`、`code`、`retry_after`。可调用的端点取决于令牌权限，新增权限须更新[快速开始](../user-guide/getting-started.md)。

### HTTP

```python
async with self.context.http.open(url) as resp:
    body = await self.context.http.read(resp, 1024 * 1024)
```

外部 URL 一律经 `http`：仅公网 `http(s)`，重定向逐跳校验，违规抛出 `BlockedURLError`。`open(url, allow=...)` 可追加规则；`read` 超限抛出 `ValueError`，`truncate=True` 则截断。固定端点（RSS、外部 API）可自建会话，请求头用 `twipsybot.plugin.USER_AGENT`。

### OpenAI 与 Bot

| 接口 | 用途 |
| --- | --- |
| `openai.generate_text(prompt, system_prompt, max_tokens, temperature, json_output)` | 单轮文本，可请求 JSON Object |
| `openai.generate_chat(messages, max_tokens, temperature)` | 多轮或多模态 |
| `openai.moderate_texts(texts)` | 批量审核，返回各条命中的类别集合；需端点支持 `/moderations` |
| `openai.generate_image(prompt, model=..., size=..., quality=..., base_url=..., api_key=...)` | 文生图，返回字节或 URL |
| `openai.system_prompt` · `max_tokens` · `temperature` · `uses_responses_api` | 全局生成参数与消息格式 |
| `bot.user_id` · `username` · `admins` | 机器人身份与管理员名单 |
| `bot.is_admin(user_id, handle)` | 是否管理员，`handle` 为 `username` 或 `username@host` |
| `bot.actor_lock(user_id, username)` | 串行处理同一用户 |
| `bot.load_antenna_selectors()` · `resolve_antenna_ids(selectors)` | 读取、解析天线 |
| `bot.generate_auto_post(plugin_prompt)` | 以 `plugin_prompt + autopost.prompt` 生成帖子 |

## 约束

- Hook 超时 180 秒，生命周期方法 30 秒；超时或异常只影响本次调用。初始化、启动或重载失败时执行 `cleanup()` 并禁用该插件。
- 关闭时最多等待进行中的 Hook 3 秒后取消；不要吞掉 `asyncio.CancelledError`，长时间 I/O 自设超时。
- 公共 API 是 `twipsybot.plugin` 导出的内容；其余 `twipsybot.*`、`PluginManager` 与事件 `raw` 属内部实现。不兼容修改须提升 API 版本，见[维护](maintenance.md#兼容性)。
