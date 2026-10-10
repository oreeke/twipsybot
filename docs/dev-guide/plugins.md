---
title: 插件开发
description: 使用 TwipsyBot 插件 API 编写事件 Hook、配置、存储与自动发帖扩展。
---

# 插件开发

本地插件位于 `plugins/<name>/plugin.py`，以模块级 `plugin` 导出插件类。入口按单文件加载，不支持相对导入，多模块插件请用 [Entry Points](#entry-points)。配置来自 `plugins.<name>`，可由 `twipsybot cfg` 编辑。

## 最小插件

```python
from twipsybot.plugin import MentionEvent, PluginBase, PluginConfig


class ExampleConfig(PluginConfig):
    response: str = "收到"


class ExamplePlugin(PluginBase):
    api_version = 3
    priority = 100
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

- `api_version` 必须是字面量；引用宿主常量会导致宿主升级后无法识别不兼容。
- 覆盖的生命周期与 Hook 方法必须是 `async`。
- `priority` 为默认优先级，可被 `plugins.<name>.priority` 覆盖，越大越先执行。

## 配置模型

`PluginConfig` 基于 Pydantic，验证后通过只读的 `self.settings` 访问；`enabled`、`priority` 等框架字段不进入模型。TUI 依据模型自动生成表单。类型为 `SecretStr` 的字段视为凭据，只从 `data/secrets.yaml` 的 `plugins.<name>` 读取，其余字段存于 `settings.yaml`。

```python
from typing import Annotated

from pydantic import Field

from twipsybot.plugin import LineText, PluginConfig


class ExampleConfig(PluginConfig):
    max_length: int = Field(200, ge=1, le=3000)
    feeds: Annotated[tuple[str, ...], LineText] = ()
```

`LineText` 字段在 TUI 中以多行文本原样保存，验证时拆为非空行并忽略 `#` 注释行；元素需进一步解析时，可在元素类型上叠加 `BeforeValidator`。

`When("mode", "a")` 加入 `Annotated`，TUI 仅在同级字段 `mode` 取值为 `a` 时显示该字段。

## Entry Points

需独立发布或声明依赖的插件，在包的 `pyproject.toml` 中注册，模块同样导出 `plugin`：

```toml
[project.entry-points."twipsybot.plugins"]
example = "twipsybot_example:plugin"
```

入口名即插件 ID 与配置键，与本地插件同名时本地插件优先。安装不会自动启用，需设置 `plugins.<name>.enabled: true`；依赖与版本由插件自行管理。

## 生命周期

| 方法 | 时机 |
| --- | --- |
| `initialize()` | 加载后初始化资源，返回 `True` 才启用 |
| `on_startup()` | 初始化完成、开始接收 Hook 前 |
| `on_shutdown()` | 停止接收新 Hook 后 |
| `cleanup()` | 释放资源，初始化失败时也可能调用 |

- `_register_resource(resource)` 注册带 `close()` 的资源，清理时自动关闭。
- 自建任务在 `on_shutdown()` 中停止，在 `cleanup()` 中完成释放。

**重载**（配置变更或 `^reload`）：停止分发 Hook 并等待进行中的调用 → 旧实例 `on_shutdown()`、`cleanup()` → 以新配置新建实例并执行 `initialize()`、`on_startup()`。运行时状态只放在实例内并在上述方法中释放即可正确重载；不要使用模块或类变量，需跨重载保留的数据写入 `storage`。

## 事件 Hook

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
| `MessageEvent` | `id text user room_id files raw` |
| `MentionEvent` | `id text cw user files raw` |
| `NotificationEvent` | `id type user raw` |
| `TimelineNoteEvent` | `id text cw user channel files raw` |
| `AutoPostEvent` | `triggered_at` |
| `UserRef` | `id username host handle` |
| `FileRef` | `id mime_type url thumbnail_url raw` |

- 按 `priority` 降序调用。`on_message` / `on_mention` 返回 `HandledResult` 即终止后续插件与 AI；`on_context`、通知与时间线 Hook 不截断，所有插件都会收到。
- 返回值须严格符合公共 TypedDict，多余字段会使结果失效。
- 消息、提及与时间线事件的 `id` 始终非空；`NotificationEvent.id`、`UserRef.id`、`cw`、`host`、文件 URL 等可能为空。
- `UserRef.handle` 为 `username@host`，本地用户为 `username`。`channel` 通常为 `homeTimeline`、`localTimeline`、`hybridTimeline`、`globalTimeline` 或 `antenna`。
- 事件数据类不可变；每个插件获得独立的 `raw` 副本，但嵌套值并非深度只读。`raw` 仅作只读后备，不保证长期兼容。

### 补充上下文

```python
return {"context": "<参考资料>", "text": "去掉前缀后的用户文本"}
```

- 仅在没有插件接管 `on_message` / `on_mention` 时调用，所有插件按优先级都会被调用，不会互相截断。
- `context` 在本次请求中置于用户内容之前，不写入聊天历史；多个插件的 `context` 按优先级以空行拼接。
- `text` 用于替换事件中的原文（如去掉命令前缀），多个插件返回时取优先级最高者。
- 两个字段均可省略，但至少返回其一，且须为非空字符串；返回其他字段使结果失效。
- 事件类型与来源一致：聊天为 `MessageEvent`，提及为 `MentionEvent`。仅在 AI 回复前调用，须自设超时并容忍失败。

### 自动发帖

```python
return {"contents": ["第一篇", "第二篇"], "visibility": "home"}
```

```python
return {"prompt": "围绕开源维护写一篇短文。"}
```

- `contents` 直接发布、不调用 AI；`prompt` 置于全局自动发帖提示词之前，由 AI 生成。两者及 `contents` 中的文本均须非空。
- 多插件返回时 `contents` 优先于 `prompt`，同类取优先级最高者；多篇间隔 10 秒发布。
- 轮转模式下两者都受 `autopost.daily_max` 约束，定时模式不受限。
- `PromptModificationResult` 可附带分钟级整数 `timestamp`。
- `on_auto_post_published(content)` 仅在本插件 `AutoPostResult` 的内容发布成功后调用，适合提交去重记录或推进轮换位置。

## PluginContext

| `self.context.*` | 说明 |
| --- | --- |
| `name` | 插件 ID：本地为目录名，第三方为 Entry Point 名 |
| `config` | `plugins.<name>` 原始配置的只读映射 |
| `storage` | 按插件 ID 隔离的字符串存储 |
| `misskey` | 发帖、转帖、反应、聊天、用户、天线与 Drive |
| `openai` | 文本、聊天与审核 |
| `http` | 下载外部 URL，拒绝内网目标 |
| `bot` | 机器人身份、用户锁与天线解析 |

### Storage

```python
value = await self.context.storage.get("key")
await self.context.storage.set("key", "value")
deleted = await self.context.storage.delete("key")
```

键值均为字符串，复杂结构用 JSON 编码；`delete(None)` 清空本插件存储并返回删除数量。同一用户的读改写需加锁：

```python
async with self.context.bot.actor_lock(event.user.id, event.user.handle):
    value = await self.context.storage.get("state")
    await self.context.storage.set("state", value or "initialized")
```

### Misskey 与 Drive

| 接口 | 用途 |
| --- | --- |
| `misskey.create_note(text, visibility, reply_id, local_only)` | 发帖或回复 |
| `misskey.create_renote(note_id, visibility, text, local_only)` | 转帖或引用 |
| `misskey.create_reaction(note_id, reaction)` | 添加反应 |
| `misskey.send_message(user_id, text)` | 发送私信 |
| `misskey.show_user(user_id)` | 用户详情，含资料、个人备注与双方关系 |
| `misskey.update_user_memo(user_id, memo)` | 写入个人备注，`None` 或空字符串删除；需 `write:account` 权限 |
| `misskey.list_antennas()` | 获取天线 |
| `misskey.get_note(note_id)` | 获取帖子最新数据 |
| `misskey.instance_url` | 实例地址 |
| `misskey.drive.show_file(file_id)` | 文件信息 |
| `misskey.drive.fetch_bytes(url, max_bytes=...)` | 从 URL 下载，上限默认 32 MiB |
| `misskey.drive.download_bytes(file_id, thumbnail=..., max_bytes=...)` | 下载文件 |
| `misskey.drive.upload_bytes(data, name=..., content_type=...)` | 上传文件，结果 `id` 为文件 ID |

`visibility`：`public`、`home`、`followers`。`create_note` 与 `HandledResult` 暂不支持附件。

### HTTP

```python
async with self.context.http.open(url) as resp:
    body = await self.context.http.read(resp, 1024 * 1024)
```

下载外部 URL 一律用 `http`，不要自建会话：仅访问公网 `http(s)`，重定向逐跳校验，违规抛出 `BlockedURLError`。可传 `allow=lambda url: bool` 追加自己的规则；`read` 超限抛出 `ValueError`，`truncate=True` 则截断。管理员配置的固定端点可自建会话。

### OpenAI 与 Bot

| 接口 | 用途 |
| --- | --- |
| `openai.generate_text(prompt, system_prompt, max_tokens, temperature, json_output)` | 单轮文本，可请求 JSON Object |
| `openai.generate_chat(messages, max_tokens, temperature)` | 多轮或多模态 |
| `openai.moderate_texts(texts)` | 批量审核，按输入顺序返回命中类别集合；需端点支持 `/moderations` |
| `openai.system_prompt` · `max_tokens` · `temperature` | 全局生成参数 |
| `openai.uses_responses_api` | 当前消息格式 |
| `bot.user_id` · `username` | 机器人身份 |
| `bot.actor_lock(user_id, username)` | 串行处理同一用户 |
| `bot.load_antenna_selectors()` | 读取天线选择器 |
| `bot.resolve_antenna_ids(selectors)` | 解析天线 ID |

## 失败处理

- Hook 异常或超时（180 秒）仅影响本次调用；关闭时最多等待 3 秒后取消。
- 自行捕获可预期的网络与解析错误，长时间 I/O 自设超时，不要吞掉 `asyncio.CancelledError`。
- 生命周期方法超时 30 秒；初始化、启动或重载失败时执行 `cleanup()` 并禁用该插件，不影响其他插件。

## API 边界

- **公共**：`twipsybot.plugin` 导出的 `PluginBase`、`PluginContext`、事件、结果与服务 `Protocol`，以及本文记载的 `PluginBase` 辅助方法。
- **内部**：其他 `twipsybot.*` 模块（含 `bot`、`clients`、`db`）、`PluginManager`、未文档化的私有属性与事件 `raw`。

插件 API v3 仅做向后兼容扩展；删除、重命名公共成员或改变其语义需提升主版本号。
