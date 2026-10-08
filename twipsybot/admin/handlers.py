import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from importlib.metadata import version as package_version
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING

from ..bot.flows.post import JOB_ID as AUTO_POST_JOB_ID
from ..shared.config_keys import ConfigKeys
from ..shared.exceptions import (
    APIBadRequestError,
    APIConnectionError,
    APINotFoundError,
    APIRateLimitError,
)
from ..shared.utils import normalize_tokens

if TYPE_CHECKING:
    from ..bot.engine.core import Neuro

CLEAN_POST_DELETE_LIMIT = 300
_CHANNEL_LABELS = {
    "main": "main",
    "homeTimeline": "home",
    "localTimeline": "local",
    "hybridTimeline": "hybrid",
    "globalTimeline": "global",
}
_DYNAMIC_CHANNEL_LABELS = {"antenna": "antenna", "chatUser": "chat"}


def _format_duration(seconds: float) -> str:
    days, rest = divmod(int(max(0, seconds)), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    value = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{days}d {value}" if days else value


def _light(*, healthy: bool, waiting: bool = True) -> str:
    if healthy:
        return "🟩"
    return "🟨" if waiting else "🟥"


def admins(bot: "Neuro") -> list[str]:
    return normalize_tokens(bot.config.get(ConfigKeys.BOT_ADMINS))


def task_status_text(bot: "Neuro") -> str:
    task = bot.runtime.tasks.get("streaming")
    running = bot.runtime.running
    stream = _light(
        healthy=task is not None and not task.done(),
        waiting=task is None or not running,
    )
    scheduler = _light(
        healthy=bot.scheduler.state == STATE_RUNNING,
        waiting=bot.scheduler.state == STATE_PAUSED or not running,
    )
    return f"任务  stream {stream} · scheduler {scheduler}"


def auto_post_status_text(bot: "Neuro") -> str:
    config = bot.config
    match bot.auto_post.mode:
        case "off":
            return "发帖  已关闭"
        case "rotation":
            count = bot.auto_post.daily_post_count
            limit = config.get(ConfigKeys.POST_DAILY_MAX)
            text = f"发帖  轮转 {count}/{limit}"
            if count >= limit:
                return f"{text} · 今日已达上限"
        case _:
            text = f"发帖  定时 {len(config.get(ConfigKeys.POST_TIMES))} 个时间点"
    job = bot.scheduler.get_job(AUTO_POST_JOB_ID)
    next_run = getattr(job, "next_run_time", None)
    if bot.scheduler.state != STATE_RUNNING or next_run is None:
        return f"{text} · 未调度"
    return f"{text} · 下次 {next_run.astimezone():%m-%d %H:%M %z}"


def _channel_status_text(bot: "Neuro") -> str:
    counts = Counter(channel["name"] for channel in bot.streaming.channels.values())
    groups = [
        " · ".join(label for name, label in _CHANNEL_LABELS.items() if counts[name]),
        " · ".join(
            f"{label} x{counts[name]}"
            for name, label in _DYNAMIC_CHANNEL_LABELS.items()
            if counts[name]
        ),
    ]
    return "频道  " + ("\n\u3000\u3000  ".join(filter(None, groups)) or "无")


def status_text(bot: "Neuro") -> str:
    running = bot.runtime.running
    uptime = (datetime.now(UTC) - bot.runtime.startup_time).total_seconds()
    connection = _light(
        healthy=bot.streaming.state == "connected",
        waiting=bot.streaming.state in {"initializing", "reconnecting"} or not running,
    )
    events = bot.streaming.events.status()
    parts = [
        f"版本  {package_version('twipsybot')}",
        (
            f"状态  {_light(healthy=running)} {'运行中' if running else '未运行'}"
            f" · {_format_duration(uptime)}"
        ),
        f"连接  {connection} {bot.streaming.state}",
        task_status_text(bot),
        (
            f"事件  worker {events['workers_alive']}/{events['workers_total']}"
            f" · busy {events['busy_workers']}"
            f" · queue {events['queue_size']}/{events['queue_capacity']}"
        ),
        "",
    ]
    if bot.bot_username:
        suffix = f" ({bot.bot_user_id})" if bot.bot_user_id else ""
        parts.append(f"账号  @{bot.bot_username}{suffix}")
    if hostname := urlsplit(bot.misskey.instance_url).hostname:
        parts.append(f"实例  {hostname}")
    if bot.openai.model:
        parts.append(f"模型  {bot.openai.model}")
    chat = "on" if bot.config.get(ConfigKeys.REPLY_CHAT) else "off"
    mention = "on" if bot.config.get(ConfigKeys.REPLY_MENTION) else "off"
    parts += [
        "",
        f"开关  chat={chat} mention={mention} autopost={bot.auto_post.mode}",
        auto_post_status_text(bot),
        _channel_status_text(bot),
    ]
    if plugins := bot.plugin_manager.get_plugin_info():
        enabled = sum(plugin.get("enabled") is True for plugin in plugins)
        parts += ["", f"插件  {enabled}/{len(plugins)} 已启用"]
    parts.append(f"授权  {len(admins(bot))}")
    return "\n".join(parts)


async def set_bool(bot: "Neuro", key: str, label: str, args: str) -> str | None:
    action = args.strip().lower()
    if action not in {"on", "off"}:
        return None
    await bot.settings.update({key: action == "on"})
    return f"{label}: {action}"


async def set_model(bot: "Neuro", args: str) -> str:
    model = args.strip()
    if not model:
        return f"当前模型: {bot.openai.model}"
    if model.lower() in {"reset", "default"}:
        await bot.settings.update({ConfigKeys.BOT_MODEL: None})
        return f"已恢复默认模型: {bot.openai.model}"
    await bot.settings.update({ConfigKeys.BOT_MODEL: model})
    return f"已切换模型: {model}"


async def set_autopost(bot: "Neuro", args: str) -> str | None:
    mode = args.strip().lower()
    if mode == "reset":
        await bot.auto_post.reset_daily_counters()
        return "自动发帖计数器已重置"
    if mode not in {"rotation", "schedule", "off"}:
        return None
    if mode == "schedule" and not bot.config.get(ConfigKeys.POST_TIMES):
        return "定时发帖需要先在 twipsybot cfg 中添加时间点"
    await bot.settings.update(
        {
            ConfigKeys.POST_ROTATION: mode == "rotation",
            ConfigKeys.POST_SCHEDULE: mode == "schedule",
        }
    )
    return f"autopost: {mode}"


async def reload_plugin(bot: "Neuro", args: str) -> str | None:
    if len(parts := args.split()) != 1:
        return None
    name = parts[0]
    statuses = await bot.settings.reload()
    match statuses.get(name) or await bot.plugin_manager.reload_plugin(name):
        case "enabled":
            return f"插件 {name} 已重载"
        case "disabled":
            return f"插件 {name} 已按配置禁用"
        case "failed":
            return f"插件 {name} 重载失败，已禁用，详见日志"
        case "unknown":
            return f"未知插件: {name}"
        case _:
            return "当前无法重载插件"


async def edit_user_list(bot: "Neuro", key: str, label: str, args: str) -> str | None:
    current = normalize_tokens(bot.config.get(key), lower=True)
    action, *rest = args.split(maxsplit=1) or [""]
    action = action.lower()
    if action in {"", "list", "status", "show"}:
        return "\n".join(current) or "(空)"
    values = normalize_tokens(" ".join(rest), lower=True)
    if action in {"clear", "empty"}:
        updated: list[str] = []
    elif action in {"add", "+", "append"} and values:
        updated = current + [value for value in values if value not in current]
    elif action in {"del", "remove", "-"} and values:
        updated = [value for value in current if value not in values]
    elif action in {"set", "="}:
        updated = values
    else:
        return None
    await bot.settings.update({key: updated})
    return "\n".join([f"已更新 {label}", "", *(updated or ["(空)"])])


def _created_at(note: dict[str, Any]) -> datetime | None:
    if not isinstance(value := note.get("createdAt"), str):
        return None
    try:
        created_at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)


def _is_cleanable(note: dict[str, Any], cutoff: datetime, pinned: set[str]) -> bool:
    created_at = _created_at(note)
    return (
        isinstance(note.get("id"), str)
        and note["id"] not in pinned
        and created_at is not None
        and created_at < cutoff
        and note.get("replyId") is None
        and note.get("renoteId") is None
        and note.get("channelId") is None
        and not note.get("mentions")
        and note.get("repliesCount") == 0
        and note.get("renoteCount") == 0
        and note.get("reactionCount") == 0
        and not note.get("reactions")
        and note.get("clippedCount") == 0
        and note.get("poll") is None
    )


async def _pinned_note_ids(bot: "Neuro") -> set[str]:
    user = await bot.misskey.get_current_user()
    ids = {i for i in user.get("pinnedNoteIds") or [] if isinstance(i, str)}
    ids.update(
        note["id"]
        for note in user.get("pinnedNotes") or []
        if isinstance(note, dict) and isinstance(note.get("id"), str)
    )
    return ids


async def _collect_clean_posts(
    bot: "Neuro", user_id: str, cutoff: datetime, pinned: set[str]
) -> list[dict[str, Any]]:
    notes: dict[str, dict[str, Any]] = {}
    until_id: str | None = None
    while page := await bot.misskey.get_user_notes(
        user_id,
        until_date=int(cutoff.timestamp() * 1000),
        until_id=until_id,
    ):
        for note in page:
            if _is_cleanable(note, cutoff, pinned):
                notes.setdefault(note["id"], note)
        next_id = page[-1].get("id")
        if len(page) < 100 or not isinstance(next_id, str) or next_id == until_id:
            break
        until_id = next_id
    return sorted(notes.values(), key=lambda note: _created_at(note) or cutoff)


async def delete_clean_posts(
    bot: "Neuro", notes: list[dict[str, Any]], cutoff: datetime, pinned: set[str]
) -> tuple[int, int, int]:
    deleted = skipped = 0
    batch = notes[:CLEAN_POST_DELETE_LIMIT]
    unprocessed = len(notes) - len(batch)
    for index, note in enumerate(batch):
        try:
            current = await bot.misskey.get_note(note["id"])
        except (APIBadRequestError, APINotFoundError):
            skipped += 1
            continue
        except APIRateLimitError:
            unprocessed += len(batch) - index
            break
        try:
            if not _is_cleanable(current, cutoff, pinned):
                skipped += 1
                continue
            await bot.misskey.delete_note(note["id"])
        except (APIBadRequestError, APINotFoundError):
            skipped += 1
            continue
        except (APIConnectionError, APIRateLimitError):
            unprocessed += len(batch) - index
            break
        deleted += 1
        if index < len(batch) - 1:
            await asyncio.sleep(1.05)
    return deleted, skipped, unprocessed


async def clean_posts(bot: "Neuro", args: str) -> str | None:
    parts = args.split()
    if len(parts) not in {2, 3} or parts[0] != "posts" or parts[2:] not in ([], ["-y"]):
        return None
    try:
        days = int(parts[1])
    except ValueError:
        return None
    if days < 1:
        return "天数必须大于 0"
    if not (user_id := bot.bot_user_id):
        return "机器人账号尚未初始化"
    cutoff = datetime.now(UTC) - timedelta(days=days)
    pinned = await _pinned_note_ids(bot)
    notes = await _collect_clean_posts(bot, user_id, cutoff, pinned)
    if not notes:
        return f"未发现超过 {days} 天未被互动的帖子"
    if len(parts) == 2:
        oldest, newest = notes[0], notes[-1]
        return "\n".join(
            (
                f"发现 {len(notes)} 条超过 {days} 天未被互动的帖子",
                f"最旧：{_created_at(oldest):%Y-%m-%d} · {oldest['id']}",
                f"最新：{_created_at(newest):%Y-%m-%d} · {newest['id']}",
                f"使用 ^clean posts {days} -y 确认删除",
                "危险：删除后无法恢复",
            )
        )
    deleted, skipped, unprocessed = await delete_clean_posts(bot, notes, cutoff, pinned)
    return (
        f"已删除 {deleted} 条超过 {days} 天未被互动的帖子 · "
        f"跳过 {skipped} 条 · 未处理 {unprocessed} 条"
    )
