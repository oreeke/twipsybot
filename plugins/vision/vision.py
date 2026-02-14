import base64
from typing import Any

import humanfriendly
from loguru import logger

from twipsybot.plugin import PluginBase, PluginHookResult
from twipsybot.shared.config_keys import ConfigKeys
from twipsybot.shared.utils import (
    extract_chat_text,
    extract_note_text,
    normalize_payload,
)


class VisionPlugin(PluginBase):
    description = "视觉插件，识别提及（@）或聊天中的图片并回复"

    def __init__(self, context):
        super().__init__(context)
        self.max_images = int(self.config.get("max_images", 3))
        self.max_bytes = self._parse_size(self.config.get("max_bytes"), 6 * 1024 * 1024)
        self.use_thumbnail = bool(self.config.get("use_thumbnail", True))
        self.default_prompt = str(
            self.config.get("default_prompt", "请描述图片内容并回答用户的问题。")
        )

    def _use_responses_api(self) -> bool:
        mode = self.global_config.get(ConfigKeys.OPENAI_API_MODE, "auto")
        if not isinstance(mode, str):
            return True
        return mode.strip().lower() != "chat"

    @staticmethod
    def _make_text_part(text: str, *, use_responses: bool) -> dict[str, Any]:
        if use_responses:
            return {"type": "input_text", "text": text}
        return {"type": "text", "text": text}

    @staticmethod
    def _normalize_image_mime(value: Any) -> str | None:
        return value if isinstance(value, str) and value.startswith("image/") else None

    def _select_direct_url(self, file_like: dict[str, Any]) -> str | None:
        a, b = (
            ("thumbnailUrl", "url") if self.use_thumbnail else ("url", "thumbnailUrl")
        )
        return self._normalize_url(file_like.get(a)) or self._normalize_url(
            file_like.get(b)
        )

    async def _try_fetch_bytes_by_url(self, direct_url: str | None) -> bytes | None:
        if not direct_url:
            return None
        try:
            return await self.misskey.drive.fetch_bytes(
                direct_url, max_bytes=self.max_bytes
            )
        except Exception as e:
            logger.error(f"Vision failed to download image: {e!r}")
            return None

    async def _ensure_image_mime(self, fid: str, mime: str | None) -> str | None:
        if mime:
            return mime
        try:
            info = await self.drive.show_file(fid)
        except Exception as e:
            logger.error(f"Vision failed to read file info: {e!r}")
            return None
        return self._normalize_image_mime(info.get("type"))

    async def _try_download_bytes_by_id(self, fid: str) -> bytes | None:
        try:
            return await self.drive.download_bytes(
                fid, thumbnail=self.use_thumbnail, max_bytes=self.max_bytes
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

    @staticmethod
    def _parse_size(value: Any, default: int) -> int:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return max(0, int(value))
        if not isinstance(value, str):
            return default
        try:
            return max(0, int(humanfriendly.parse_size(value)))
        except Exception:
            return default

    async def initialize(self) -> bool:
        self._log_plugin_action("initialized")
        return True

    async def on_mention(self, mention_data: dict[str, Any]) -> PluginHookResult | None:
        if not (parts := await self._build_user_content(mention_data, kind="mention")):
            return None
        reply = await self._call_vision(parts, call_type="mention image")
        return self.handled(reply)

    async def on_message(self, message_data: dict[str, Any]) -> PluginHookResult | None:
        if not (parts := await self._build_user_content(message_data, kind="chat")):
            return None
        reply = await self._call_vision(parts, call_type="chat image")
        return self.handled(reply)

    @staticmethod
    def _extract_text(data: dict[str, Any], *, kind: str) -> str:
        data = normalize_payload(data, kind=kind)
        if kind == "chat":
            return extract_chat_text(data)
        return extract_note_text(data, include_cw=True, allow_body_fallback=True)

    @staticmethod
    def _extract_files(data: dict[str, Any], *, kind: str) -> list[dict[str, Any]]:
        data = normalize_payload(data, kind=kind)
        files: list[dict[str, Any]] = []
        if kind == "chat":
            if isinstance(data.get("file"), dict):
                files.append(data["file"])
            if fid := data.get("fileId"):
                files.append({"id": fid})
            return VisionPlugin._dedupe_files(files)
        if isinstance(data.get("files"), list):
            files.extend([f for f in data["files"] if isinstance(f, dict)])
        if isinstance(data.get("fileIds"), list):
            files.extend(
                [{"id": fid} for fid in data["fileIds"] if isinstance(fid, str)]
            )
        return VisionPlugin._dedupe_files(files)

    @staticmethod
    def _dedupe_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for f in files:
            fid = f.get("id")
            if not isinstance(fid, str) or fid in seen:
                continue
            seen.add(fid)
            out.append(f)
        return out

    async def _build_user_content(
        self, data: dict[str, Any], *, kind: str
    ) -> list[dict[str, Any]]:
        use_responses = self._use_responses_api()
        text = self._extract_text(data, kind=kind)
        files = self._extract_files(data, kind=kind)[: self.max_images]
        images: list[dict[str, Any]] = []
        for f in files:
            if not (item := await self._to_image_part(f, use_responses=use_responses)):
                continue
            images.append(item)
        if not images:
            return []
        prompt = text or self.default_prompt
        return [self._make_text_part(prompt, use_responses=use_responses), *images]

    async def _to_image_part(
        self, file_like: dict[str, Any], *, use_responses: bool
    ) -> dict[str, Any] | None:
        fid = file_like.get("id")
        if not isinstance(fid, str):
            return None
        mime = self._normalize_image_mime(file_like.get("type"))
        data = await self._try_fetch_bytes_by_url(self._select_direct_url(file_like))
        if data is not None and not mime:
            mime = await self._ensure_image_mime(fid, mime)
        if data is None or not mime:
            mime = await self._ensure_image_mime(fid, mime)
            if not mime:
                return None
            data = await self._try_download_bytes_by_id(fid)
            if data is None:
                return None
        if not mime or data is None:
            return None
        return self._make_image_part(mime, data, use_responses=use_responses)

    async def _call_vision(
        self, user_content: list[dict[str, Any]], *, call_type: str
    ) -> str:
        system_prompt = (
            self.global_config.get(ConfigKeys.BOT_SYSTEM_PROMPT, "") or ""
        ).strip()
        messages: list[dict[str, Any]] = []
        if system_prompt:
            if self._use_responses_api():
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
        reply = await self.openai.generate_chat(
            messages,
            max_tokens=self.global_config.get(ConfigKeys.OPENAI_MAX_TOKENS),
            temperature=self.global_config.get(ConfigKeys.OPENAI_TEMPERATURE),
        )
        logger.debug(f"Vision {call_type} reply generated; length: {len(reply)}")
        return reply
