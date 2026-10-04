import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from ..bot.engine.pipeline import AIResponse, CommandResult
from ..clients.misskey.payloads import (
    extract_first_text,
    extract_user_handle,
    extract_user_id,
    extract_username,
)
from ..shared.config_keys import ConfigKeys
from ..shared.utils import normalize_tokens
from .handlers import CmdHandlersMixin


@dataclass(frozen=True, slots=True)
class _SlashCommand:
    handler: Callable[[str], Awaitable[AIResponse]]
    usage: str
    description: str
    requires_auth: bool = True
    private_only: bool = False


class AdminCommandService(CmdHandlersMixin):
    def __init__(self, bot: Any):
        self.bot = bot
        self.global_config = bot.config
        self.plugin_manager = bot.plugin_manager
        self.openai = bot.openai
        self.refresh()
        self.commands: dict[str, Any] = {}
        self._setup_default_commands()
        self._command_alias_index = self._build_command_alias_index()
        self._command_handlers = self._build_command_handlers()
        self._slash_commands = {
            "img": _SlashCommand(
                self.bot.image.generate_response,
                "/img",
                "生成图片 (用法: /img <一只小狗在月球上弹吉他>)",
            ),
            "post": _SlashCommand(
                self.bot.auto_post.generate_response,
                "/post",
                "手动发帖 (用法: /post [-p|-h|-f] [-l] <发一篇关于夏天的帖子，150 字>)",
                private_only=True,
            ),
        }

    def _get_help_text(self) -> str:
        slash_commands = [
            f"{command.usage} - {command.description}"
            for command in self._slash_commands.values()
        ]
        return "\n\n".join((super()._get_help_text(), "\n".join(slash_commands)))

    def refresh(self) -> None:
        self.allowed_users = frozenset(
            normalize_tokens(self.global_config.get(ConfigKeys.BOT_ADMINS))
        )
        self._allowed_users_lower = frozenset(
            value.lower() for value in self.allowed_users
        )

    def _build_command_alias_index(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for name, info in self.commands.items():
            if not isinstance(name, str) or not name:
                continue
            aliases = info.get("aliases", []) if isinstance(info, dict) else []
            for alias in aliases or []:
                if isinstance(alias, str) and (a := alias.strip()):
                    index.setdefault(a.lower(), name)
        return index

    def _build_command_handlers(self) -> dict[str, Any]:
        return {
            "help": lambda args: self._get_help_text(),
            "status": lambda args: self._get_status_text(),
            "model": self._handle_model,
            "autopost": self._handle_autopost,
            "clean": self._handle_clean,
            "reload": self._handle_reload,
            "mention": lambda args: self._handle_set_bool(
                "mention", ConfigKeys.REPLY_MENTION, args
            ),
            "chat": lambda args: self._handle_set_bool(
                "chat", ConfigKeys.REPLY_CHAT, args
            ),
            "whitelist": lambda args: self._handle_response_user_list(
                "whitelist", ConfigKeys.REPLY_WHITELIST, args
            ),
            "blacklist": lambda args: self._handle_response_user_list(
                "blacklist", ConfigKeys.REPLY_BLACKLIST, args
            ),
        }

    def _setup_default_commands(self):
        if not self.commands:
            self.commands = {
                "help": {"description": "可用命令", "aliases": []},
                "status": {"description": "机器人状态", "aliases": []},
                "model": {
                    "description": "查看/切换模型 (用法: ^model [模型名]|reset)",
                    "aliases": [],
                },
                "autopost": {
                    "description": "自动发帖 (用法: ^autopost rotation|schedule|off|reset)",
                    "aliases": [],
                },
                "clean": {
                    "description": "清理帖子 (用法: ^clean posts <天数> [-y])",
                    "aliases": [],
                },
                "reload": {
                    "description": "重载插件配置 (用法: ^reload <插件名>)",
                    "aliases": [],
                },
                "mention": {
                    "description": "响应提及开关 (用法: ^mention on|off)",
                    "aliases": [],
                },
                "chat": {
                    "description": "响应聊天开关 (用法: ^chat on|off)",
                    "aliases": [],
                },
                "whitelist": {
                    "description": "查看/修改白名单 (用法: ^whitelist [list|add|del|set|clear])",
                    "aliases": [],
                },
                "blacklist": {
                    "description": "查看/修改黑名单 (用法: ^blacklist [list|add|del|set|clear])",
                    "aliases": [],
                },
            }

    def start(self) -> None:
        self._log_plugin_action("initialized", f"Command groups: {len(self.commands)}")

    async def _handle_autopost(self, args: str) -> str:
        if args.strip().lower() == "reset":
            await self.bot.auto_post.reset_daily_counters()
            return "自动发帖计数器已重置"
        mode = args.strip().lower()
        if mode not in {"rotation", "schedule", "off"}:
            return "用法: ^autopost rotation|schedule|off|reset"
        if mode == "schedule" and not self.global_config.get(ConfigKeys.POST_TIMES):
            return "定时发帖需要先在 twipsybot cfg 中添加时间点"
        await self.bot.settings.update(
            {
                ConfigKeys.POST_ROTATION: mode == "rotation",
                ConfigKeys.POST_SCHEDULE: mode == "schedule",
            }
        )
        return f"autopost: {mode}"

    async def _handle_reload(self, args: str) -> str:
        parts = args.split()
        if len(parts) != 1:
            return "用法: ^reload <插件名>"
        name = parts[0]
        statuses = await self.bot.settings.reload()
        match statuses.get(name) or await self.plugin_manager.reload_plugin(name):
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

    async def blacklist_response_user(self, user_id: str) -> None:
        blacklist = normalize_tokens(
            self.global_config.get(ConfigKeys.REPLY_BLACKLIST), lower=True
        )
        normalized = user_id.strip().lower()
        if normalized and normalized not in blacklist:
            await self.bot.settings.update(
                {ConfigKeys.REPLY_BLACKLIST: [*blacklist, normalized]}
            )

    def _log_plugin_action(self, action: str, details: str = "") -> None:
        logger.info(f"Admin {action}{': ' + details if details else ''}")

    def _is_authorized(self, user_id: str, handle: str | None) -> bool:
        return user_id in self.allowed_users or (
            handle is not None and handle.lower() in self._allowed_users_lower
        )

    @staticmethod
    def _extract_slash_command(text: str) -> tuple[str, str] | None:
        value = text.strip()
        if not value:
            return None
        while value.startswith("@"):
            parts = value.split(maxsplit=1)
            if len(parts) != 2:
                return None
            value = parts[1]
        parts = value.split(maxsplit=1)
        token = parts[0]
        if not token.startswith("/") or len(token) == 1 or len(parts) != 2:
            return None
        argument = parts[1].strip()
        return (token[1:].casefold(), argument) if argument else None

    def handle_slash_command(
        self,
        text: str,
        *,
        user_id: str | None,
        username: str,
        handle: str | None,
        private: bool,
    ) -> CommandResult | None:
        parsed = self._extract_slash_command(text)
        if parsed is None:
            return None
        name, argument = parsed
        command = self._slash_commands.get(name)
        if command is None:
            return None
        if command.private_only and not private:
            return CommandResult()
        authorized_handle = self._canonical_handle(username, handle) or handle
        if command.requires_auth and (
            not user_id or not self._is_authorized(user_id, authorized_handle)
        ):
            return CommandResult(AIResponse("您没有权限使用命令。"))
        return CommandResult(execute=lambda: command.handler(argument))

    def _canonical_handle(self, username: str, handle: str | None) -> str | None:
        if isinstance(handle, str) and (h := handle.strip()):
            if "@" in h:
                return h
            if username and h != username:
                return None
        if not username or username == "unknown":
            return None
        return self.bot.limits.canonical_handle(username)

    def _find_command(self, cmd: str) -> str | None:
        cmd_lower = cmd.lower()
        if cmd_lower in self.commands:
            return cmd_lower
        return self._command_alias_index.get(cmd_lower)

    def _get_command_title(self, command: str) -> str:
        info = self.commands.get(command)
        desc = info.get("description") if isinstance(info, dict) else None
        if isinstance(desc, str) and (title := desc.strip()):
            for sep in ("(", "（"):
                if (idx := title.find(sep)) > 0:
                    title = title[:idx].rstrip()
                    break
            return title
        return f"^{command}"

    def _format_command_output(self, title: str, text: str) -> str:
        lines = text.splitlines() if (text or "").strip() else ["(空)"]
        return self._format_code_block(title, lines)

    async def _execute_command(self, command: str, args: str = "") -> str:
        handler = self._command_handlers.get(command)
        if not handler:
            return f"未知命令: {command}"
        try:
            result = handler(args)
            return await result if asyncio.iscoroutine(result) else result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error executing command {command}: {e}")
            return f"命令执行失败: {e!s}"

    async def on_message(self, message_data: dict[str, Any]) -> str | None:
        text = extract_first_text(message_data, "text", "content")
        if not text.startswith("^"):
            return None
        try:
            user_id = extract_user_id(message_data)
            username = extract_username(message_data)
            handle = self._canonical_handle(username, extract_user_handle(message_data))
            if not user_id:
                return None
            if not self._is_authorized(user_id, handle):
                return self._format_command_output("命令", "您没有权限使用命令。")
            command_text = text[1:].strip()
            parts = command_text.split(maxsplit=1)
            command_name = self._find_command(parts[0])
            args = parts[1] if len(parts) > 1 else ""
            if command_name:
                who = handle or username
                self._log_plugin_action("ran command", f"@{who}: ^{command_text}")
                result = await self._execute_command(command_name, args)
                return self._format_command_output(
                    self._get_command_title(command_name), result
                )
            return self._format_command_output(
                f"^{parts[0]}",
                f"未知命令: {parts[0]}\n使用 ^help 查看可用命令。",
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error handling command: {e}")
            return self._format_command_output("命令", "命令处理失败，请稍后重试。")
