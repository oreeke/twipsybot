from collections.abc import Sequence
from typing import Any

from ..shared.config import Config
from ..shared.locks import KeyedAsyncLock, actor_key


class OpenAIServiceAdapter:
    def __init__(self, openai: Any, config: Config):
        self._openai = openai
        self._config = config

    @property
    def uses_responses_api(self) -> bool:
        return self._openai.uses_responses_api

    @property
    def system_prompt(self) -> str:
        return self._config.settings.bot.system_prompt

    @property
    def max_tokens(self) -> int:
        return self._config.settings.bot.max_tokens

    @property
    def temperature(self) -> float:
        return self._config.settings.bot.temperature

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_output: bool = False,
    ) -> str:
        return await self._openai.generate_text(
            prompt, system_prompt, max_tokens, temperature, json_output
        )

    async def moderate_texts(self, texts: list[str]) -> list[frozenset[str]]:
        return await self._openai.moderate_texts(texts)

    async def generate_chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        return await self._openai.generate_chat(messages, max_tokens, temperature)

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str,
        size: str | None = None,
        quality: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> bytes | str:
        return await self._openai.generate_image(
            prompt,
            model=model,
            size=size,
            quality=quality,
            base_url=base_url,
            api_key=api_key,
        )


class NamespacedPluginStorage:
    def __init__(self, db: Any, namespace: str):
        self._db = db
        self._namespace = namespace

    async def get(self, key: str) -> str | None:
        return await self._db.get_plugin_data(self._namespace, key)

    async def set(self, key: str, value: str) -> None:
        await self._db.set_plugin_data(self._namespace, key, value)

    async def delete(self, key: str | None = None) -> int:
        return await self._db.delete_plugin_data(self._namespace, key)


class BotControlAdapter:
    def __init__(self, bot: Any):
        self._bot = bot
        self._actor_locks = KeyedAsyncLock()

    @property
    def user_id(self) -> str:
        return self._bot.me.id

    @property
    def username(self) -> str:
        return self._bot.me.username

    def actor_lock(self, user_id: str | None, username: str | None):
        return self._actor_locks.hold(actor_key(user_id, username))

    @property
    def admins(self) -> tuple[str, ...]:
        return self._bot.admin.admins

    def is_admin(self, user_id: str | None, handle: str | None) -> bool:
        return self._bot.admin.is_admin(user_id, handle)

    def load_antenna_selectors(self) -> list[str]:
        return self._bot.connect.load_antenna_selectors()

    async def resolve_antenna_ids(self, selectors: Sequence[str]) -> list[str]:
        return await self._bot.connect.resolve_antenna_ids(list(selectors))

    async def generate_auto_post(self, plugin_prompt: str = "") -> str:
        return await self._bot.auto_post.generate(plugin_prompt)
