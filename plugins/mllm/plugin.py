import base64
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from loguru import logger
from pydantic import ByteSize, Field, field_validator
from pydantic_core import PydanticCustomError

from twipsybot.plugin import (
    ContextResult,
    FileRef,
    MentionEvent,
    MessageEvent,
    PluginBase,
    PluginConfig,
)

_IMAGE = "image/"
_KINDS = (_IMAGE, "audio/", "video/")
_AUDIO_FORMATS = {"audio/mpeg": "mp3", "audio/wav": "wav"}


class _Config(PluginConfig):
    max_files: int = Field(4, ge=1)
    max_bytes: ByteSize = ByteSize(6 * 1024 * 1024)
    use_thumbnail: bool = False
    default_prompt: str = ""

    @field_validator("max_bytes", mode="before")
    @classmethod
    def _reject_boolean_size(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise PydanticCustomError("byte_size", "must be a byte size")
        return value


class MLLMPlugin(PluginBase):
    api_version = 3
    priority = 750
    config_class = _Config
    settings: _Config

    def _select_source(self, file: FileRef, mime: str) -> tuple[str | None, str]:
        thumbnail = self._normalize_url(file.thumbnail_url)
        original = self._normalize_url(file.url)
        sources = [
            (thumbnail, mime if thumbnail == original else "image/webp"),
            (original, mime),
        ]
        if not self.settings.use_thumbnail:
            sources.reverse()
        return next(((url, m) for url, m in sources if url), (None, mime))

    async def _try_fetch_bytes_by_url(self, direct_url: str | None) -> bytes | None:
        if not direct_url:
            return None
        try:
            return await self.context.misskey.drive.fetch_bytes(
                direct_url, max_bytes=self.settings.max_bytes
            )
        except Exception as e:
            logger.error(f"MLLM failed to download file: {e!r}")
            return None

    @staticmethod
    def _make_part(file: FileRef, mime: str, data: bytes) -> dict[str, Any]:
        b64 = base64.b64encode(data).decode("ascii")
        if mime.startswith(_IMAGE):
            return {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"}
        if audio := _AUDIO_FORMATS.get(mime):
            return {
                "type": "input_audio",
                "input_audio": {"data": b64, "format": audio},
            }
        name = file.name or PurePosixPath(urlsplit(file.url or "").path).name or "file"
        return {
            "type": "input_file",
            "filename": name,
            "file_data": f"data:{mime};base64,{b64}",
        }

    @staticmethod
    def _normalize_url(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        url = value.strip().replace("`", "").strip()
        return url or None

    async def initialize(self) -> bool:
        self._log_plugin_action("initialized")
        return True

    async def on_context(
        self, event: MessageEvent | MentionEvent
    ) -> ContextResult | None:
        if isinstance(event, MentionEvent):
            files, text = event.attachments, event.cw or event.text
        else:
            files, text = event.files, event.text
        media: list[dict[str, Any]] = []
        for file in files:
            if len(media) >= self.settings.max_files:
                break
            if part := await self._to_part(file):
                media.append(part)
        if not media:
            return None
        result: ContextResult = {"media": media}
        if not text.strip() and (prompt := self.settings.default_prompt.strip()):
            result["context"] = prompt
        return result

    async def _to_part(self, file: FileRef) -> dict[str, Any] | None:
        mime = file.mime_type
        if not mime or not mime.startswith(_KINDS):
            return None
        if mime.startswith(_IMAGE):
            url, mime = self._select_source(file, mime)
        else:
            url = self._normalize_url(file.url)
        data = await self._try_fetch_bytes_by_url(url)
        if data is None:
            return None
        return self._make_part(file, mime, data)


plugin = MLLMPlugin
