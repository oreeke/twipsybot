import inspect
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from cachetools import TTLCache
from loguru import logger

from ..bot.engine.pipeline import Reply
from ..clients.misskey.payloads import (
    extract_first_text,
    extract_user_handle,
    extract_user_id,
    extract_username,
)
from ..shared.config_keys import ConfigKeys
from ..shared.utils import normalize_tokens
from . import handlers

if TYPE_CHECKING:
    from ..bot.engine.core import Neuro

_FOLLOWER_TTL = 60


@dataclass(frozen=True, slots=True)
class _Command:
    run: Callable[[str], Any]
    title: str
    usage: str = ""
    private_only: bool = False

    def help(self, name: str) -> str:
        usage = f" (用法: {name} {self.usage})" if self.usage else ""
        return f"{name} - {self.title}{usage}"


def _code_block(title: str, text: str) -> str:
    lines = text.splitlines() if text.strip() else ["(空)"]
    heading = title if title.endswith((":", "：")) else f"{title}:"
    return "\n".join([heading, "```", *lines, "```"])


def _strip_mentions(text: str) -> str:
    value = text.strip()
    while value.startswith("@"):
        parts = value.split(maxsplit=1)
        value = parts[1] if len(parts) == 2 else ""
    return value


def _is_slash_command(text: str) -> bool:
    token = next(iter(_strip_mentions(text).split(maxsplit=1)), "")
    return len(token) > 1 and token.startswith("/")


def _extract_slash_command(text: str) -> tuple[str, str] | None:
    if len(parts := _strip_mentions(text).split(maxsplit=1)) != 2:
        return None
    token, argument = parts
    if not token.startswith("/") or len(token) == 1:
        return None
    return token[1:].casefold(), argument.strip()


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
                partial(handlers.clean_posts, bot), "清理帖子", "posts <天数> [-y]"
            ),
            "reload": _Command(
                partial(handlers.reload_plugin, bot), "重载插件配置", "<插件名>"
            ),
            "mention": _Command(
                partial(set_bool, ConfigKeys.REPLY_MENTION, "mention"),
                "响应提及开关",
                "on|off",
            ),
            "chat": _Command(
                partial(set_bool, ConfigKeys.REPLY_CHAT, "chat"),
                "响应聊天开关",
                "on|off",
            ),
            "whitelist": _Command(
                partial(user_list, ConfigKeys.REPLY_WHITELIST, "whitelist"),
                "查看/修改白名单",
                "[list|add|del|set|clear]",
            ),
            "blacklist": _Command(
                partial(user_list, ConfigKeys.REPLY_BLACKLIST, "blacklist"),
                "查看/修改黑名单",
                "[list|add|del|set|clear]",
            ),
        }
        self._slash_commands = {
            "img": _Command(
                bot.image.generate_response, "生成图片", "<一只小狗在月球上弹吉他>"
            ),
            "post": _Command(
                bot.auto_post.generate_response,
                "手动发帖",
                "[-p|-h|-f] [-l] <发一篇关于夏天的帖子，150 字>",
                private_only=True,
            ),
        }
        self._followers = TTLCache[str, bool](maxsize=1024, ttl=_FOLLOWER_TTL)
        logger.info(f"Admin initialized: Command groups: {len(self._commands)}")

    def _help_text(self) -> str:
        return "\n\n".join(
            "\n".join(command.help(prefix + name) for name, command in table.items())
            for prefix, table in (("^", self._commands), ("/", self._slash_commands))
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

    async def _is_follower(self, user_id: str | None) -> bool:
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

    async def handle_slash_command(
        self,
        text: str,
        *,
        user_id: str | None,
        username: str,
        handle: str | None,
        private: bool,
    ) -> Reply | None:
        if not _is_slash_command(text):
            return None
        handle = self._canonical_handle(username, handle) or handle
        admin = bool(user_id and self._is_authorized(user_id, handle))
        if not admin and not await self._is_follower(user_id):
            logger.info(f"Ignored slash command from non-follower: @{username}")
            return Reply("")
        if not (parsed := _extract_slash_command(text)):
            return None
        name, argument = parsed
        if not (command := self._slash_commands.get(name)):
            return None
        if command.private_only and not private:
            return Reply("")
        if not admin:
            return Reply("您没有权限使用命令。")
        return await command.run(argument)

    async def _execute(self, name: str, command: _Command, args: str) -> str:
        try:
            result = command.run(args)
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:
            logger.error(f"Error executing command {name}: {e}")
            return f"命令执行失败: {e!s}"
        return f"用法: ^{name} {command.usage}" if result is None else result

    async def on_message(self, message: dict[str, Any]) -> str | None:
        text = extract_first_text(message, "text", "content")
        if not text.startswith("^") or not (user_id := extract_user_id(message)):
            return None
        try:
            username = extract_username(message)
            handle = self._canonical_handle(username, extract_user_handle(message))
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
