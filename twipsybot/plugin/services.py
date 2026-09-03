from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from ..shared.config_keys import ConfigKeys
from ..shared.locks import KeyedAsyncLock, actor_key


class DriveServiceAdapter:
    def __init__(self, drive: Any):
        self._drive = drive

    async def fetch_bytes(self, url: str, *, max_bytes: int | None = None) -> bytes:
        return await self._drive.fetch_bytes(url, max_bytes=max_bytes)

    async def show_file(self, file_id: str) -> dict[str, Any]:
        return await self._drive.show_file(file_id)

    async def upload_bytes(
        self, data: bytes, *, name: str, content_type: str = "image/png"
    ) -> dict[str, Any]:
        return await self._drive.upload_bytes(
            data, name=name, content_type=content_type
        )

    async def download_bytes(
        self,
        file_id: str,
        *,
        thumbnail: bool = False,
        max_bytes: int | None = None,
    ) -> bytes:
        return await self._drive.download_bytes(
            file_id, thumbnail=thumbnail, max_bytes=max_bytes
        )


class MisskeyServiceAdapter:
    def __init__(self, misskey: Any):
        self._misskey = misskey
        self._drive = DriveServiceAdapter(misskey.drive)

    @property
    def instance_url(self) -> str:
        return self._misskey.instance_url

    @property
    def drive(self) -> DriveServiceAdapter:
        return self._drive

    async def list_antennas(self) -> list[dict[str, Any]]:
        return deepcopy(await self._misskey.list_antennas())

    async def create_reaction(self, note_id: str, reaction: str) -> dict[str, Any]:
        return await self._misskey.create_reaction(note_id, reaction)

    async def create_note(
        self,
        text: str,
        visibility: str | None = None,
        reply_id: str | None = None,
        local_only: bool | None = None,
        validate_reply: bool = True,
    ) -> dict[str, Any]:
        return await self._misskey.create_note(
            text, visibility, reply_id, local_only, validate_reply
        )

    async def create_renote(
        self,
        note_id: str,
        visibility: str | None = None,
        text: str | None = None,
        local_only: bool | None = None,
    ) -> dict[str, Any]:
        return await self._misskey.create_renote(note_id, visibility, text, local_only)

    async def send_message(self, user_id: str, text: str) -> dict[str, Any]:
        return await self._misskey.send_message(user_id, text)


class OpenAIServiceAdapter:
    def __init__(self, openai: Any, config: Any):
        self._openai = openai
        self._config = config

    @property
    def uses_responses_api(self) -> bool:
        return self._openai.uses_responses_api

    @property
    def system_prompt(self) -> str:
        return self._config.get(ConfigKeys.BOT_SYSTEM_PROMPT, "") or ""

    @property
    def max_tokens(self) -> int | None:
        return self._config.get(ConfigKeys.OPENAI_MAX_TOKENS)

    @property
    def temperature(self) -> float | None:
        return self._config.get(ConfigKeys.OPENAI_TEMPERATURE)

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
    def user_id(self) -> str | None:
        return self._bot.bot_user_id

    @property
    def username(self) -> str | None:
        return self._bot.bot_username

    def actor_lock(self, user_id: str | None, username: str | None):
        return self._actor_locks.hold(actor_key(user_id, username))

    def load_antenna_selectors(self) -> list[str]:
        return self._bot.connect.load_antenna_selectors()

    async def resolve_antenna_ids(self, selectors: Sequence[str]) -> list[str]:
        return await self._bot.connect.resolve_antenna_ids(list(selectors))
