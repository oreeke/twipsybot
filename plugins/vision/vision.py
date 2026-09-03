import base64
from typing import Any

from loguru import logger
from pydantic import ByteSize, Field, field_validator

from twipsybot.plugin import (
    PLUGIN_API_VERSION,
    FileRef,
    HandledResult,
    MentionEvent,
    MessageEvent,
    PluginBase,
    PluginConfig,
)


class _Config(PluginConfig):
    max_images: int = Field(3, ge=1)
    max_bytes: ByteSize = ByteSize(6 * 1024 * 1024)
    use_thumbnail: bool = True
    default_prompt: str = "请描述图片内容。"

    @field_validator("max_bytes", mode="before")
    @classmethod
    def _reject_boolean_size(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("must be a byte size")
        return value


class VisionPlugin(PluginBase):
    api_version = PLUGIN_API_VERSION
    config_class = _Config
    settings: _Config
    description = "理解 @提及或聊天中的图片并生成回复"

    @staticmethod
    def _make_text_part(text: str, *, use_responses: bool) -> dict[str, Any]:
        if use_responses:
            return {"type": "input_text", "text": text}
        return {"type": "text", "text": text}

    @staticmethod
    def _normalize_image_mime(value: Any) -> str | None:
        return value if isinstance(value, str) and value.startswith("image/") else None

    def _select_direct_url(self, file: FileRef) -> str | None:
        first, second = (
            (file.thumbnail_url, file.url)
            if self.settings.use_thumbnail
            else (file.url, file.thumbnail_url)
        )
        return self._normalize_url(first) or self._normalize_url(second)

    async def _try_fetch_bytes_by_url(self, direct_url: str | None) -> bytes | None:
        if not direct_url:
            return None
        try:
            return await self.context.misskey.drive.fetch_bytes(
                direct_url, max_bytes=self.settings.max_bytes
            )
        except Exception as e:
            logger.error(f"Vision failed to download image: {e!r}")
            return None

    async def _ensure_image_mime(self, fid: str, mime: str | None) -> str | None:
        if mime:
            return mime
        try:
            info = await self.context.misskey.drive.show_file(fid)
        except Exception as e:
            logger.error(f"Vision failed to read file info: {e!r}")
            return None
        return self._normalize_image_mime(info.get("type"))

    async def _try_download_bytes_by_id(self, fid: str) -> bytes | None:
        try:
            return await self.context.misskey.drive.download_bytes(
                fid,
                thumbnail=self.settings.use_thumbnail,
                max_bytes=self.settings.max_bytes,
            )
        except Exception as e:
            logger.error(f"Vision failed to download image: {e!r}")
            return None

    @staticmethod
    def _make_image_part(
        mime: str, data: bytes, *, use_responses: bool
    ) -> dict[str, Any]:
        b64 = base64.b64encode(data).decode("ascii")
        url = f"data:{mime};base64,{b64}"
        if use_responses:
            return {"type": "input_image", "image_url": url}
        return {"type": "image_url", "image_url": {"url": url}}

    @staticmethod
    def _normalize_url(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        url = value.strip().replace("`", "").strip()
        return url or None

    async def initialize(self) -> bool:
        self._log_plugin_action("initialized")
        return True

    async def on_mention(self, event: MentionEvent) -> HandledResult | None:
        text = "\n\n".join(part for part in (event.cw, event.text) if part)
        if not (parts := await self._build_user_content(text, event.files)):
            return None
        reply = await self._call_vision(parts, call_type="mention image")
        return self.handled(reply)

    async def on_message(self, event: MessageEvent) -> HandledResult | None:
        if not (parts := await self._build_user_content(event.text, event.files)):
            return None
        reply = await self._call_vision(parts, call_type="chat image")
        return self.handled(reply)

    async def _build_user_content(
        self, text: str, files: tuple[FileRef, ...]
    ) -> list[dict[str, Any]]:
        use_responses = self.context.openai.uses_responses_api
        images: list[dict[str, Any]] = []
        for file in files[: self.settings.max_images]:
            if not (
                item := await self._to_image_part(file, use_responses=use_responses)
            ):
                continue
            images.append(item)
        if not images:
            return []
        prompt = text or self.settings.default_prompt
        return [self._make_text_part(prompt, use_responses=use_responses), *images]

    async def _to_image_part(
        self, file: FileRef, *, use_responses: bool
    ) -> dict[str, Any] | None:
        fid = file.id
        mime = self._normalize_image_mime(file.mime_type)
        data = await self._try_fetch_bytes_by_url(self._select_direct_url(file))
        if not mime:
            mime = await self._ensure_image_mime(fid, mime)
        if not mime:
            return None
        if data is None:
            data = await self._try_download_bytes_by_id(fid)
            if data is None:
                return None
        return self._make_image_part(mime, data, use_responses=use_responses)

    async def _call_vision(
        self, user_content: list[dict[str, Any]], *, call_type: str
    ) -> str:
        system_prompt = self.context.openai.system_prompt.strip()
        messages: list[dict[str, Any]] = []
        if system_prompt:
            if self.context.openai.uses_responses_api:
                messages.append(
                    {
                        "role": "system",
                        "content": [
                            self._make_text_part(system_prompt, use_responses=True)
                        ],
                    }
                )
            else:
                messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})
        reply = await self.context.openai.generate_chat(
            messages,
            max_tokens=self.context.openai.max_tokens,
            temperature=self.context.openai.temperature,
        )
        logger.debug(f"Vision {call_type} reply generated; length: {len(reply)}")
        return reply
