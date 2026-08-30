import json
from datetime import UTC, datetime
from typing import Any

from ..shared.config_keys import ConfigKeys
from ..shared.utils import normalize_tokens


def _format_duration(seconds: float) -> str:
    total = int(max(0, seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    value = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{days}d {value}" if days else value


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
        return f"开关: chat={chat} mention={mention} autopost={autopost}"

    def _get_plugin_status_text(self) -> str | None:
        plugins = self.plugin_manager.get_plugin_info()
        if not plugins:
            return None
        enabled = sum(plugin.get("enabled") is True for plugin in plugins)
        return f"插件: {enabled}/{len(plugins)} 已启用"

    def _get_help_text(self) -> str:
        lines = []
        for name, info in self.commands.items():
            description = info.get("description", "无描述")
            aliases = info.get("aliases", [])
            alias_text = f" ({', '.join(aliases)})" if aliases else ""
            lines.append(f"^{name}{alias_text} - {description}")
        return "\n".join(lines)

    def _get_status_text(self) -> str:
        bot = self.bot
        status = "运行中" if bot.runtime.running else "未运行"
        parts = [
            f"机器人状态: {status}",
            f"运行时长: {self._get_uptime_text(bot)}",
        ]
        if bot.bot_username:
            suffix = f" ({bot.bot_user_id})" if bot.bot_user_id else ""
            parts.append(f"Bot: @{bot.bot_username}{suffix}")
        if bot.openai.model:
            parts.append(f"模型: {bot.openai.model}")
        parts.append(self._get_feature_toggle_text())
        if plugin_status := self._get_plugin_status_text():
            parts.append(plugin_status)
        parts.append(f"授权用户数: {len(self.allowed_users)}")
        return "\n".join(parts)

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
