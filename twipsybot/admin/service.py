import asyncio
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from ..clients.misskey.payloads import (
    extract_first_text,
    extract_user_handle,
    extract_user_id,
    extract_username,
)
from ..shared.config_keys import ConfigKeys
from ..shared.utils import normalize_tokens
from .handlers import CmdHandlersMixin


class AdminCommandService(CmdHandlersMixin):
    def __init__(self, bot: Any, config: Mapping[str, Any]):
        self.bot = bot
        self.config = dict(config)
        self.name = "Admin"
        self.global_config = bot.config
        self.db = bot.db
        self.plugin_manager = bot.plugin_manager
        self.misskey = bot.misskey
        self.openai = bot.openai
        self._default_model = self.openai.model
        self.allowed_users = frozenset(
            normalize_tokens(self.config.get("allowed_users", []))
        )
        self._allowed_users_lower = frozenset(
            value.lower() for value in self.allowed_users
        )
        self.commands = self.config.get("commands", {})
        self._setup_default_commands()
        self._init_baselines()
        self._command_alias_index = self._build_command_alias_index()
        self._command_handlers = self._build_command_handlers()

    def _global_get(self, key: str, default: Any) -> Any:
        cfg = getattr(self, "global_config", None)
        return cfg.get(key) if cfg else default

    def _init_baselines(self) -> None:
        self._baseline_response_whitelist = normalize_tokens(
            self._global_get(ConfigKeys.BOT_RESPONSE_WHITELIST, []), lower=True
        )
        self._baseline_response_blacklist = normalize_tokens(
            self._global_get(ConfigKeys.BOT_RESPONSE_BLACKLIST, []), lower=True
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
            "autopost": lambda args: self._handle_set_bool(
                "autopost", ConfigKeys.BOT_AUTO_POST_ENABLED, args
            ),
            "mention": lambda args: self._handle_set_bool(
                "mention", ConfigKeys.BOT_RESPONSE_MENTION, args
            ),
            "chat": lambda args: self._handle_set_bool(
                "chat", ConfigKeys.BOT_RESPONSE_CHAT, args
            ),
            "whitelist": lambda args: self._handle_response_user_list(
                "whitelist",
                ConfigKeys.BOT_RESPONSE_WHITELIST,
                args,
                self._baseline_response_whitelist,
            ),
            "blacklist": lambda args: self._handle_response_user_list(
                "blacklist",
                ConfigKeys.BOT_RESPONSE_BLACKLIST,
                args,
                self._baseline_response_blacklist,
            ),
        }

    def _setup_default_commands(self):
        if not self.commands:
            self.commands = {
                "help": {"description": "可用命令", "aliases": []},
                "status": {"description": "机器人状态", "aliases": []},
                "model": {
                    "description": "查看/切换模型 (用法: model [模型名]|reset)",
                    "aliases": [],
                },
                "autopost": {
                    "description": "自动发帖开关 (用法: autopost on|off)",
                    "aliases": [],
                },
                "mention": {
                    "description": "响应提及开关 (用法: mention on|off)",
                    "aliases": [],
                },
                "chat": {
                    "description": "响应聊天开关 (用法: chat on|off)",
                    "aliases": [],
                },
                "whitelist": {
                    "description": "查看/修改白名单 (用法: whitelist [list|add|del|set|clear|reset])",
                    "aliases": [],
                },
                "blacklist": {
                    "description": "查看/修改黑名单 (用法: blacklist [list|add|del|set|clear|reset])",
                    "aliases": [],
                },
            }

    async def start(self) -> None:
        self._log_plugin_action("initialized", f"Command groups: {len(self.commands)}")
        model = await self.db.get_plugin_data(self.name, ConfigKeys.OPENAI_MODEL)
        if model:
            self.openai.model = model
            self._set_global_config_value(ConfigKeys.OPENAI_MODEL, model)
            self._log_plugin_action("applied model override", model)
        await self._apply_saved_response_user_list(ConfigKeys.BOT_RESPONSE_WHITELIST)
        await self._apply_saved_response_user_list(ConfigKeys.BOT_RESPONSE_BLACKLIST)

    @staticmethod
    def handled(response: str) -> str:
        return response

    def _log_plugin_action(self, action: str, details: str = "") -> None:
        logger.info(f"Admin {action}{': ' + details if details else ''}")

    def _set_global_config_value(self, path: str, value: Any) -> None:
        keys = path.split(".")
        config = self.global_config.data
        for key in keys[:-1]:
            if not isinstance(config.get(key), dict):
                config[key] = {}
            config = config[key]
        config[keys[-1]] = value

    def _is_authorized(self, user_id: str, handle: str | None) -> bool:
        return user_id in self.allowed_users or (
            handle is not None and handle.lower() in self._allowed_users_lower
        )

    def _canonical_handle(self, username: str, handle: str | None) -> str | None:
        if isinstance(handle, str) and (h := handle.strip()):
            if "@" in h:
                return h
            if username and h != username:
                return None
        if not username or username == "unknown":
            return None
        misskey = getattr(self, "misskey", None)
        instance_url = getattr(misskey, "instance_url", None) if misskey else None
        if not isinstance(instance_url, str) or not instance_url:
            return None
        host = urlparse(instance_url).hostname
        return f"{username}@{host}" if host else None

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
                return self.handled(
                    self._format_command_output("命令", "您没有权限使用命令。")
                )
            command_text = text[1:].strip()
            parts = command_text.split(maxsplit=1)
            command_name = self._find_command(parts[0])
            args = parts[1] if len(parts) > 1 else ""
            if command_name:
                who = handle or username
                self._log_plugin_action("ran command", f"@{who}: ^{command_text}")
                result = await self._execute_command(command_name, args)
                return self.handled(
                    self._format_command_output(
                        self._get_command_title(command_name), result
                    )
                )
            return self.handled(
                self._format_command_output(
                    f"^{parts[0]}",
                    f"未知命令: {parts[0]}\n使用 ^help 查看可用命令。",
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error handling command: {e}")
            return self.handled(
                self._format_command_output("命令", "命令处理失败，请稍后重试。")
            )
