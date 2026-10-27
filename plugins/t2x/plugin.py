from functools import partial
from typing import Annotated

from loguru import logger
from pydantic import ConfigDict, Field, SecretStr, model_validator

from twipsybot.plugin import (
    HandledResult,
    MentionEvent,
    MessageEvent,
    PluginBase,
    PluginConfig,
    tool,
)

_FAILED = "Image generation failed."


class _ImageConfig(PluginConfig):
    model_config = ConfigDict(str_strip_whitespace=True)

    base_url: str = ""
    api_key: SecretStr = SecretStr("")
    model: str = Field(default="", description="gpt-image-2")
    size: str = ""
    quality: str = ""

    @model_validator(mode="after")
    def _validate_endpoint(self) -> "_ImageConfig":
        if self.base_url and not self.api_key.get_secret_value():
            raise ValueError("base_url requires api_key")
        return self


class _Config(PluginConfig):
    image: _ImageConfig = Field(default_factory=_ImageConfig)


def _detect_image_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xff\xd8\xff"):
        return "generated.jpg", "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "generated.webp", "image/webp"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "generated.png", "image/png"
    raise ValueError("image API returned unsupported image data")


class T2XPlugin(PluginBase):
    api_version = 4
    config_class = _Config
    settings: _Config

    @tool(
        "Generate an image from a text description and send it. Generation takes "
        "a while; the image is sent automatically when ready.",
        access="admin",
        effect=True,
    )
    async def draw(
        self,
        event: MessageEvent | MentionEvent,
        prompt: Annotated[str, Field(description="Description of the image")],
    ) -> str | HandledResult:
        if not self.settings.image.model:
            logger.warning("T2X image model is not configured")
            return "Image generation is not configured and cannot be used."
        return self.handled(
            "Image generation started; it will be sent automatically when ready.",
            followup=partial(self._followup, prompt.strip()),
        )

    async def _followup(self, prompt: str) -> HandledResult:
        try:
            file_id = await self._generate_image(prompt)
        except Exception:
            logger.exception("Image generation failed")
            return self.handled(_FAILED)
        return self.handled("", file_id)

    async def _generate_image(self, prompt: str) -> str:
        config = self.settings.image
        image = await self.context.openai.generate_image(
            prompt,
            model=config.model,
            size=config.size or None,
            quality=config.quality or None,
            base_url=config.base_url or None,
            api_key=config.api_key.get_secret_value() or None,
        )
        drive = self.context.misskey.drive
        if isinstance(image, str):
            image = await drive.fetch_bytes(image)
        name, content_type = _detect_image_type(image)
        uploaded = await drive.upload_bytes(image, name=name, content_type=content_type)
        file_id = uploaded.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("Misskey Drive upload returned no file id")
        return file_id


plugin = T2XPlugin
