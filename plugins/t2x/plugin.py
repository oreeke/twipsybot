import re
from functools import partial

from loguru import logger
from pydantic import ConfigDict, Field, SecretStr, model_validator

from twipsybot.plugin import (
    HandledResult,
    MentionEvent,
    MessageEvent,
    PluginBase,
    PluginConfig,
    UserRef,
)

_IMAGE_COMMAND = re.compile(
    r"^\s*(?:@\S+\s+)*/img\s+(?P<prompt>\S.*)$", re.IGNORECASE | re.DOTALL
)
_DENIED = "您没有权限使用命令。"
_FAILED = "图片生成失败，请稍后再试。"


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
    api_version = 3
    priority = 800
    config_class = _Config
    settings: _Config

    async def on_mention(self, event: MentionEvent) -> HandledResult | None:
        return await self._handle(event.text, event.user)

    async def on_message(self, event: MessageEvent) -> HandledResult | None:
        return await self._handle(event.text, event.user)

    async def _handle(self, text: str, user: UserRef) -> HandledResult | None:
        if not (match := _IMAGE_COMMAND.match(text)):
            return None
        if not self.context.bot.is_admin(user.id, user.handle):
            return self.handled(_DENIED)
        if not self.settings.image.model:
            logger.warning("T2X image model is not configured")
            return self.handled(_FAILED)
        prompt = match["prompt"].strip()
        return self.handled(
            "正在生成图片，请稍候…", followup=partial(self._followup, prompt)
        )

    async def _followup(self, prompt: str) -> HandledResult:
        try:
            file_id = await self._generate_image(prompt)
        except Exception:
            logger.exception("Image generation failed")
            return self.handled(_FAILED)
        return self.handled("图片生成完成", file_id)

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
