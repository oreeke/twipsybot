import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from ..engine.pipeline import AIResponse

if TYPE_CHECKING:
    from ..engine.core import MisskeyBot


class ImageGenerationService:
    _MAX_IMAGE_BYTES = 32 * 1024 * 1024

    def __init__(self, bot: "MisskeyBot"):
        self.bot = bot

    async def generate_and_upload(self, prompt: str) -> str:
        image = await self.bot.openai.generate_image(prompt)
        if isinstance(image, str):
            image = await self.bot.misskey.drive.fetch_bytes(
                image, max_bytes=self._MAX_IMAGE_BYTES
            )
        if len(image) > self._MAX_IMAGE_BYTES:
            raise ValueError("generated image exceeds 32 MiB")
        name, content_type = self._detect_image_type(image)
        uploaded = await self.bot.misskey.drive.upload_bytes(
            image, name=name, content_type=content_type
        )
        file_id = uploaded.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("Misskey Drive upload returned no file id")
        return file_id

    async def generate_response(self, prompt: str) -> AIResponse:
        try:
            file_id = await self.generate_and_upload(prompt)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Image generation failed")
            return AIResponse("图片生成失败，请稍后再试。")
        return AIResponse("图片生成完成", file_id)

    @staticmethod
    def _detect_image_type(data: bytes) -> tuple[str, str]:
        if data.startswith(b"\xff\xd8\xff"):
            return "generated.jpg", "image/jpeg"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "generated.webp", "image/webp"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "generated.png", "image/png"
        raise ValueError("image API returned unsupported image data")
