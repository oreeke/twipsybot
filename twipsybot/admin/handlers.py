import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from importlib.metadata import version as package_version
from typing import Any
from urllib.parse import urlsplit

from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING

from ..shared.config_keys import ConfigKeys
from ..shared.exceptions import (
    APIBadRequestError,
    APIConnectionError,
    APINotFoundError,
    APIRateLimitError,
)
from ..shared.utils import normalize_tokens

CLEAN_POST_DELETE_LIMIT = 300


def _format_duration(seconds: float) -> str:
    total = int(max(0, seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    value = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{days}d {value}" if days else value


def _status_light(*, healthy: bool, waiting: bool = True) -> str:
    if healthy:
        return "🟩"
    return "🟨" if waiting else "🟥"


class CmdHandlersMixin:
    bot: Any
    global_config: Any
    db: Any
    plugin_manager: Any
    openai: Any
    name: str
    commands: dict[str, Any]
    allowed_users: frozenset[str]
    _default_model: str

    def _set_global_config_value(self, path: str, value: Any) -> None:
        raise NotImplementedError

    def _log_plugin_action(self, action: str, details: str = "") -> None:
        raise NotImplementedError

    def _get_uptime_text(self, bot: Any) -> str:
        seconds = (datetime.now(UTC) - bot.runtime.startup_time).total_seconds()
        return _format_duration(seconds)

    def _get_feature_toggle_text(self) -> str:
        cfg = self.global_config
        chat = "on" if cfg.get(ConfigKeys.BOT_RESPONSE_CHAT) else "off"
        mention = "on" if cfg.get(ConfigKeys.BOT_RESPONSE_MENTION) else "off"
        autopost = "on" if cfg.get(ConfigKeys.BOT_AUTO_POST_ENABLED) else "off"
        return f"开关  chat={chat} mention={mention} autopost={autopost}"

    def _get_plugin_status_text(self) -> str | None:
        plugins = self.plugin_manager.get_plugin_info()
        if not plugins:
            return None
        enabled = sum(plugin.get("enabled") is True for plugin in plugins)
        return f"插件  {enabled}/{len(plugins)} 已启用"

    def _get_help_text(self) -> str:
        lines = []
        for name, info in self.commands.items():
            description = info.get("description", "无描述")
            aliases = info.get("aliases", [])
            alias_text = f" ({', '.join(aliases)})" if aliases else ""
            lines.append(f"^{name}{alias_text} - {description}")
        return "\n".join(lines)

    def _get_task_status_text(self) -> str:
        task = self.bot.runtime.tasks.get("streaming")
        running = self.bot.runtime.running
        stream = _status_light(
            healthy=task is not None and not task.done(),
            waiting=task is None or not running,
        )
        scheduler = _status_light(
            healthy=self.bot.scheduler.state == STATE_RUNNING,
            waiting=self.bot.scheduler.state == STATE_PAUSED or not running,
        )
        return f"任务  stream {stream} · scheduler {scheduler}"

    def _get_auto_post_status_text(self) -> str:
        count = self.bot.auto_post.daily_post_count
        limit = self.global_config.get(ConfigKeys.BOT_AUTO_POST_MAX_PER_DAY)
        text = f"发帖  {count}/{limit}"
        if not self.global_config.get(ConfigKeys.BOT_AUTO_POST_ENABLED):
            return f"{text} · 已关闭"
        if count >= limit:
            return f"{text} · 今日已达上限"
        job = self.bot.scheduler.get_job("auto_post")
        next_run = getattr(job, "next_run_time", None)
        if self.bot.scheduler.state != STATE_RUNNING or next_run is None:
            return f"{text} · 未调度"
        return f"{text} · 下次 {next_run.astimezone():%m-%d %H:%M %z}"

    def _get_channel_status_text(self) -> str:
        counts = Counter(
            channel["name"] for channel in self.bot.streaming.channels.values()
        )
        labels = {
            "main": "main",
            "homeTimeline": "home",
            "localTimeline": "local",
            "hybridTimeline": "hybrid",
            "globalTimeline": "global",
            "antenna": "antenna",
            "chatUser": "chat",
        }
        timelines = []
        dynamic = []
        for name, label in labels.items():
            count = counts.get(name, 0)
            if not count:
                continue
            if name in {"antenna", "chatUser"}:
                dynamic.append(f"{label} x{count}")
            else:
                timelines.append(label)
        groups = [" · ".join(group) for group in (timelines, dynamic) if group]
        return "频道  " + ("\n\u3000\u3000  ".join(groups) if groups else "无")

    def _get_status_text(self) -> str:
        bot = self.bot
        status = _status_light(healthy=bot.runtime.running)
        status_text = "运行中" if bot.runtime.running else "未运行"
        connection = _status_light(
            healthy=bot.streaming.state == "connected",
            waiting=bot.streaming.state in {"initializing", "reconnecting"}
            or not bot.runtime.running,
        )
        events = bot.streaming.get_event_status()
        parts = [
            f"版本  {package_version('twipsybot')}",
            f"状态  {status} {status_text} · {self._get_uptime_text(bot)}",
            f"连接  {connection} {bot.streaming.state}",
            self._get_task_status_text(),
            f"事件  worker {events['workers_alive']}/{events['workers_total']}"
            f" · busy {events['busy_workers']}"
            f" · queue {events['queue_size']}/{events['queue_capacity']}",
            "",
        ]
        if bot.bot_username:
            suffix = f" ({bot.bot_user_id})" if bot.bot_user_id else ""
            parts.append(f"账号  @{bot.bot_username}{suffix}")
        if hostname := urlsplit(bot.misskey.instance_url).hostname:
            parts.append(f"实例  {hostname}")
        if bot.openai.model:
            parts.append(f"模型  {bot.openai.model}")
        parts.append("")
        parts.append(self._get_feature_toggle_text())
        parts.append(self._get_auto_post_status_text())
        parts.append(self._get_channel_status_text())
        if plugin_status := self._get_plugin_status_text():
            parts.extend(("", plugin_status))
        parts.append(f"授权  {len(self.allowed_users)}")
        return "\n".join(parts)

    @staticmethod
    def _note_created_at(note: dict[str, Any]) -> datetime | None:
        value = note.get("createdAt")
        if not isinstance(value, str):
            return None
        try:
            created_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (
            created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at
        )

    @classmethod
    def _is_clean_post_candidate(
        cls,
        note: dict[str, Any],
        cutoff: datetime,
        pinned_note_ids: set[str],
    ) -> bool:
        created_at = cls._note_created_at(note)
        note_id = note.get("id")
        return (
            isinstance(note_id, str)
            and note_id not in pinned_note_ids
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

    async def _get_pinned_note_ids(self) -> set[str]:
        user = await self.bot.misskey.get_current_user()
        ids = {
            note_id
            for note_id in user.get("pinnedNoteIds", []) or []
            if isinstance(note_id, str)
        }
        ids.update(
            note["id"]
            for note in user.get("pinnedNotes", []) or []
            if isinstance(note, dict) and isinstance(note.get("id"), str)
        )
        return ids

    @classmethod
    def _append_clean_post_candidates(
        cls,
        page: list[dict[str, Any]],
        notes: list[dict[str, Any]],
        seen_ids: set[str],
        cutoff: datetime,
        pinned_note_ids: set[str],
    ) -> None:
        for note in page:
            note_id = note.get("id")
            if not isinstance(note_id, str) or note_id in seen_ids:
                continue
            seen_ids.add(note_id)
            if cls._is_clean_post_candidate(note, cutoff, pinned_note_ids):
                notes.append(note)

    @staticmethod
    def _next_clean_posts_cursor(
        page: list[dict[str, Any]], current: str | None
    ) -> str | None:
        if len(page) < 100:
            return None
        next_id = page[-1].get("id")
        if not isinstance(next_id, str) or next_id == current:
            return None
        return next_id

    async def _collect_clean_posts(
        self, days: int
    ) -> tuple[list[dict[str, Any]], datetime, set[str]]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        pinned_note_ids = await self._get_pinned_note_ids()
        notes: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        until_id: str | None = None
        while True:
            page = await self.bot.misskey.get_user_notes(
                self.bot.bot_user_id,
                until_date=int(cutoff.timestamp() * 1000),
                until_id=until_id,
            )
            if not page:
                break
            self._append_clean_post_candidates(
                page, notes, seen_ids, cutoff, pinned_note_ids
            )
            until_id = self._next_clean_posts_cursor(page, until_id)
            if until_id is None:
                break
        notes.sort(key=lambda note: self._note_created_at(note) or cutoff)
        return notes, cutoff, pinned_note_ids

    @classmethod
    def _format_clean_preview(cls, notes: list[dict[str, Any]], days: int) -> str:
        if not notes:
            return f"未发现超过 {days} 天未被互动的帖子"
        oldest = cls._note_created_at(notes[0])
        newest = cls._note_created_at(notes[-1])
        return "\n".join(
            (
                f"发现 {len(notes)} 条超过 {days} 天未被互动的帖子",
                f"最旧：{oldest:%Y-%m-%d} · {notes[0]['id']}",
                f"最新：{newest:%Y-%m-%d} · {notes[-1]['id']}",
                f"使用 ^clean posts {days} -y 确认删除",
                "危险：删除后无法恢复",
            )
        )

    @staticmethod
    def _parse_clean_args(args: str) -> tuple[int, bool] | str:
        parts = args.split()
        confirmed = len(parts) == 3 and parts[2] == "-y"
        if (
            len(parts) not in {2, 3}
            or parts[0] != "posts"
            or (len(parts) == 3 and not confirmed)
        ):
            return "用法: ^clean posts <天数> [-y]"
        try:
            days = int(parts[1])
        except ValueError:
            return "用法: ^clean posts <天数> [-y]"
        if days < 1:
            return "天数必须大于 0"
        return days, confirmed

    async def _delete_clean_posts(
        self,
        notes: list[dict[str, Any]],
        cutoff: datetime,
        pinned_note_ids: set[str],
    ) -> tuple[int, int, int]:
        deleted = 0
        skipped = 0
        batch = notes[:CLEAN_POST_DELETE_LIMIT]
        unprocessed = len(notes) - len(batch)
        for index, note in enumerate(batch):
            try:
                current = await self.bot.misskey.get_note(note["id"])
            except (APIBadRequestError, APINotFoundError):
                skipped += 1
                continue
            except APIRateLimitError:
                unprocessed += len(batch) - index
                break
            try:
                if not self._is_clean_post_candidate(current, cutoff, pinned_note_ids):
                    skipped += 1
                    continue
                await self.bot.misskey.delete_note(note["id"])
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

    async def _handle_clean(self, args: str) -> str:
        parsed = self._parse_clean_args(args)
        if isinstance(parsed, str):
            return parsed
        days, confirmed = parsed
        if not self.bot.bot_user_id:
            return "机器人账号尚未初始化"
        notes, cutoff, pinned_note_ids = await self._collect_clean_posts(days)
        if not confirmed or not notes:
            return self._format_clean_preview(notes, days)
        deleted, skipped, unprocessed = await self._delete_clean_posts(
            notes, cutoff, pinned_note_ids
        )
        return (
            f"已删除 {deleted} 条超过 {days} 天未被互动的帖子 · "
            f"跳过 {skipped} 条 · 未处理 {unprocessed} 条"
        )

    def _handle_set_bool(self, label: str, key: str, args: str) -> str:
        action = args.strip().lower()
        if action not in {"on", "off"}:
            return f"用法: ^{label} on|off"
        value = action == "on"
        self._set_global_config_value(key, value)
        return f"{label}: {action}"

    async def _handle_model(self, args: str) -> str:
        model = args.strip()
        if not model:
            saved = await self.db.get_plugin_data(self.name, ConfigKeys.OPENAI_MODEL)
            suffix = f"\n已保存覆盖: {saved}" if saved else ""
            return f"当前模型: {self.openai.model}{suffix}"
        if model.lower() in {"reset", "default"}:
            await self.db.delete_plugin_data(self.name, ConfigKeys.OPENAI_MODEL)
            self.openai.model = self._default_model
            self._set_global_config_value(ConfigKeys.OPENAI_MODEL, self._default_model)
            return f"已恢复默认模型: {self._default_model}"
        self.openai.model = model
        self._set_global_config_value(ConfigKeys.OPENAI_MODEL, model)
        await self.db.set_plugin_data(self.name, ConfigKeys.OPENAI_MODEL, model)
        return f"已切换模型: {model}"

    @staticmethod
    def _format_code_block(title: str, lines: list[str]) -> str:
        title = title.strip() or "输出"
        if not title.endswith((":", "：")):
            title += ":"
        return "\n".join([title, "```", *(lines or ["(空)"]), "```"])

    @staticmethod
    def _format_plain_list(items: list[str]) -> str:
        return "\n".join(items) if items else "(空)"

    @staticmethod
    def _format_plain_list_update(message: str, items: list[str]) -> str:
        return "\n".join([message, "", *(items or ["(空)"])])

    async def _apply_saved_response_user_list(self, key: str) -> None:
        saved = await self.db.get_plugin_data(self.name, key)
        if not saved:
            return
        try:
            decoded = json.loads(saved)
        except json.JSONDecodeError:
            decoded = saved
        normalized = normalize_tokens(decoded, lower=True)
        self._set_global_config_value(key, normalized)
        self._log_plugin_action("applied config override", f"{key}={len(normalized)}")

    async def _save_response_user_list(self, key: str, items: list[str]) -> None:
        self._set_global_config_value(key, items)
        await self.db.set_plugin_data(
            self.name, key, json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        )

    async def _reset_response_user_list(self, key: str, baseline: list[str]) -> None:
        self._set_global_config_value(key, list(baseline))
        await self.db.delete_plugin_data(self.name, key)

    async def _handle_response_user_list(
        self, label: str, key: str, args: str, baseline: list[str]
    ) -> str:
        current = normalize_tokens(self.global_config.get(key), lower=True)
        parts = args.strip().split(maxsplit=1)
        if not parts or parts[0].lower() in {"list", "status", "show"}:
            return self._format_plain_list(current)
        action = parts[0].lower()
        values = normalize_tokens(parts[1] if len(parts) > 1 else "", lower=True)
        if action in {"clear", "empty"}:
            updated: list[str] = []
        elif action in {"reset", "default"}:
            await self._reset_response_user_list(key, baseline)
            return self._format_plain_list_update(f"已恢复 {label}", baseline)
        elif action in {"add", "+", "append"} and values:
            existing = set(current)
            updated = current + [value for value in values if value not in existing]
        elif action in {"del", "remove", "-"} and values:
            removed = set(values)
            updated = [value for value in current if value not in removed]
        elif action in {"set", "="}:
            updated = values
        else:
            return f"用法: ^{label} [list|add|del|set|clear|reset]"
        await self._save_response_user_list(key, updated)
        return self._format_plain_list_update(f"已更新 {label}", updated)
