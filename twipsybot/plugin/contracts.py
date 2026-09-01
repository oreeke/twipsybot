from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

__all__ = (
    "BotControl",
    "DriveService",
    "MisskeyService",
    "OpenAIService",
    "PluginStorage",
)


class DriveService(Protocol):
    async def fetch_bytes(self, url: str, *, max_bytes: int | None = None) -> bytes: ...

    async def show_file(self, file_id: str) -> dict[str, Any]: ...

    async def upload_bytes(
        self, data: bytes, *, name: str, content_type: str = "image/png"
    ) -> dict[str, Any]: ...

    async def download_bytes(
        self,
        file_id: str,
        *,
        thumbnail: bool = False,
        max_bytes: int | None = None,
    ) -> bytes: ...


class MisskeyService(Protocol):
    @property
    def instance_url(self) -> str: ...

    @property
    def drive(self) -> DriveService: ...

    async def list_antennas(self) -> list[dict[str, Any]]: ...

    async def create_reaction(self, note_id: str, reaction: str) -> dict[str, Any]: ...

    async def create_note(
        self,
        text: str,
        visibility: str | None = None,
        reply_id: str | None = None,
        local_only: bool | None = None,
        validate_reply: bool = True,
    ) -> dict[str, Any]: ...

    async def create_renote(
        self,
        note_id: str,
        visibility: str | None = None,
        text: str | None = None,
        local_only: bool | None = None,
    ) -> dict[str, Any]: ...


class OpenAIService(Protocol):
    @property
    def uses_responses_api(self) -> bool: ...

    @property
    def system_prompt(self) -> str: ...

    @property
    def max_tokens(self) -> int | None: ...

    @property
    def temperature(self) -> float | None: ...

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str: ...

    async def generate_chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str: ...


class PluginStorage(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...

    async def delete(self, key: str | None = None) -> int: ...


class BotControl(Protocol):
    @property
    def user_id(self) -> str | None: ...

    @property
    def username(self) -> str | None: ...

    def actor_lock(
        self, user_id: str | None, username: str | None
    ) -> AbstractAsyncContextManager[None]: ...

    def load_antenna_selectors(self) -> list[str]: ...

    async def resolve_antenna_ids(self, selectors: Sequence[str]) -> list[str]: ...
