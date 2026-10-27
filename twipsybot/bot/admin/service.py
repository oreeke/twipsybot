import inspect
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from cachetools import TTLCache
from loguru import logger

from ...clients.misskey.models import MessageEvent
from ...shared.keys import ConfigKeys
from ...shared.utils import normalize_tokens
from . import handlers

if TYPE_CHECKING:
    from ..engine.core import Neuro

_FOLLOWER_TTL = 60


@dataclass(frozen=True, slots=True)
class _Command:
    run: Callable[[str], Any]
    title: str
    usage: str = ""

    def help(self, name: str) -> str:
        usage = f" (用法: {name} {self.usage})" if self.usage else ""
        return f"{name} - {self.title}{usage}"


def _code_block(title: str, text: str) -> str:
    lines = text.splitlines() if text.strip() else ["(空)"]
    heading = title if title.endswith((":", "：")) else f"{title}:"
    return "\n".join([heading, "```", *lines, "```"])


class AdminCommandService:
    def __init__(self, bot: "Neuro"):
        self.bot = bot
        user_list = partial(handlers.edit_user_list, bot)
        set_bool = partial(handlers.set_bool, bot)
        self._commands = {
            "help": _Command(lambda _: self._help_text(), "可用命令"),
            "status": _Command(lambda _: handlers.status_text(bot), "机器人状态"),
            "model": _Command(
                partial(handlers.set_model, bot), "查看/切换模型", "[模型名]|reset"
            ),
            "autopost": _Command(
                partial(handlers.set_autopost, bot),
                "自动发帖",
                "rotation|schedule|off|reset",
            ),
            "clean": _Command(
                partial(handlers.clean_posts, bot), "清理旧贴", "posts <天数> [-y]"
            ),
            "mention": _Command(
                partial(set_bool, ConfigKeys.REPLY_MENTION, "mention"),
                "提及",
                "on|off",
            ),
            "chat": _Command(
                partial(set_bool, ConfigKeys.REPLY_CHAT, "chat"),
                "聊天",
                "on|off",
            ),
            "whitelist": _Command(
                partial(user_list, ConfigKeys.REPLY_WHITELIST, "whitelist"),
                "白名单",
                "[list|add|del|set|clear]",
            ),
            "blacklist": _Command(
                partial(user_list, ConfigKeys.REPLY_BLACKLIST, "blacklist"),
                "黑名单",
                "[list|add|del|set|clear]",
            ),
            "plugin": _Command(
                partial(handlers.manage_plugins, bot),
                "插件列表",
                "on|off <插件名>",
            ),
        }
        self._followers = TTLCache[str, bool](maxsize=1024, ttl=_FOLLOWER_TTL)
        logger.info(f"Admin initialized: Command groups: {len(self._commands)}")

    def _help_text(self) -> str:
        return "\n".join(
            command.help(f"^{name}") for name, command in self._commands.items()
        )

    def _canonical_handle(self, username: str, handle: str | None) -> str | None:
        if handle and (h := handle.strip()):
            if "@" in h:
                return h
            if username and h != username:
                return None
        if not username or username == "unknown":
            return None
        return self.bot.limits.canonical_handle(username)

    @property
    def admins(self) -> tuple[str, ...]:
        return tuple(handlers.admins(self.bot))

    def is_admin(self, user_id: str | None, handle: str | None) -> bool:
        handle = self._canonical_handle(handle or "", handle) or handle
        return bool(user_id and self._is_authorized(user_id, handle))

    def _is_authorized(self, user_id: str, handle: str | None) -> bool:
        admins = handlers.admins(self.bot)
        return user_id in admins or (
            handle is not None and handle.lower() in {a.lower() for a in admins}
        )

    async def blacklist_response_user(self, user_id: str) -> None:
        key = ConfigKeys.REPLY_BLACKLIST
        blacklist = normalize_tokens(self.bot.config.get(key), lower=True)
        if (normalized := user_id.strip().lower()) and normalized not in blacklist:
            await self.bot.settings.update({key: [*blacklist, normalized]})

    async def is_follower(self, user_id: str | None) -> bool:
        if not user_id:
            return False
        if (cached := self._followers.get(user_id)) is not None:
            return cached
        try:
            user = await self.bot.misskey.show_user(user_id)
        except Exception as e:
            logger.warning(f"Follower check failed: user={user_id}: {e}")
            return False
        self._followers[user_id] = followed = bool(user.get("isFollowed"))
        return followed

    async def _execute(self, name: str, command: _Command, args: str) -> str:
        try:
            result = command.run(args)
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:
            logger.error(f"Error executing command {name}: {e}")
            return f"命令执行失败: {e!s}"
        return f"用法: ^{name} {command.usage}" if result is None else result

    async def on_message(self, message: MessageEvent) -> str | None:
        text = message.text
        if not text.startswith("^") or not (user_id := message.user.id):
            return None
        try:
            username = message.user.username
            handle = self._canonical_handle(username, message.user.known_handle)
            if not self._is_authorized(user_id, handle):
                return _code_block("命令", "您没有权限使用命令。")
            command_text = text[1:].strip()
            name, *rest = command_text.split(maxsplit=1) or [""]
            if not (command := self._commands.get(name.lower())):
                return _code_block(
                    f"^{name}", f"未知命令: {name}\n使用 ^help 查看可用命令。"
                )
            logger.info(f"Admin ran command: @{handle or username}: ^{command_text}")
            result = await self._execute(name.lower(), command, " ".join(rest))
            return _code_block(command.title, result)
        except Exception as e:
            logger.error(f"Error handling command: {e}")
            return _code_block("命令", "命令处理失败，请稍后重试。")
