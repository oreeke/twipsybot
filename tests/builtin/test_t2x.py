from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from plugins.t2x.plugin import T2XPlugin
from twipsybot.plugin import HandledResult, MessageEvent, UserRef

_ALICE = UserRef(id="u1", username="alice", host=None)
_PNG = b"\x89PNG\r\n\x1a\nimage"
_EVENT = MessageEvent(
    id="m1", text="画猫", user=_ALICE, room_id=None, files=(), raw=cast(Any, {})
)


def _plugin(
    image: dict[str, Any] | None = None, *, generated: bytes | str = _PNG
) -> tuple[T2XPlugin, SimpleNamespace]:
    drive = SimpleNamespace(
        fetch_bytes=AsyncMock(return_value=_PNG),
        upload_bytes=AsyncMock(return_value={"id": "file-1"}),
    )
    context = SimpleNamespace(
        name="t2x",
        config={"enabled": True, "image": {"model": "gpt-image-2", **(image or {})}},
        openai=SimpleNamespace(generate_image=AsyncMock(return_value=generated)),
        misskey=SimpleNamespace(drive=drive),
    )
    return T2XPlugin(cast(Any, context)), context


async def _finish(ack: str | HandledResult) -> HandledResult:
    assert isinstance(ack, dict)
    followup = ack.get("followup")
    assert followup
    return await followup()


def test_t2x_exposes_admin_only_draw_tool() -> None:
    plugin, _ = _plugin()

    [spec] = plugin._tools
    assert (spec.name, spec.access) == ("draw", "admin")
    assert spec.schema["required"] == ["prompt"]


async def test_t2x_acknowledges_then_generates_uploads_and_attaches_image() -> None:
    plugin, context = _plugin(
        {
            "size": "1024x1024",
            "quality": "high",
            "base_url": " https://img.example/v1 ",
            "api_key": "secret",
        }
    )

    ack = await plugin.draw(_EVENT, " 一只猫 ")
    context.openai.generate_image.assert_not_awaited()
    result = await _finish(ack)

    assert result == {"handled": True, "response": "", "file_ids": ["file-1"]}
    context.openai.generate_image.assert_awaited_once_with(
        "一只猫",
        model="gpt-image-2",
        size="1024x1024",
        quality="high",
        base_url="https://img.example/v1",
        api_key="secret",
    )
    context.misskey.drive.upload_bytes.assert_awaited_once_with(
        _PNG, name="generated.png", content_type="image/png"
    )


def test_t2x_requires_api_key_for_dedicated_endpoint() -> None:
    with pytest.raises(ValidationError, match="base_url requires api_key"):
        _plugin({"base_url": "https://img.example/v1"})


async def test_t2x_downloads_url_before_upload() -> None:
    plugin, context = _plugin(generated="https://example.com/image.jpg")
    context.misskey.drive.fetch_bytes.return_value = b"\xff\xd8\xffimage"

    result = await _finish(await plugin.draw(_EVENT, "猫"))

    assert result.get("file_ids") == ["file-1"]
    context.misskey.drive.fetch_bytes.assert_awaited_once_with(
        "https://example.com/image.jpg"
    )
    context.misskey.drive.upload_bytes.assert_awaited_once_with(
        b"\xff\xd8\xffimage", name="generated.jpg", content_type="image/jpeg"
    )


async def test_t2x_uploads_webp_with_detected_type() -> None:
    data = b"RIFF\x00\x00\x00\x00WEBPdata"
    plugin, context = _plugin(generated=data)

    await _finish(await plugin.draw(_EVENT, "猫"))

    context.misskey.drive.upload_bytes.assert_awaited_once_with(
        data, name="generated.webp", content_type="image/webp"
    )


@pytest.mark.parametrize(
    "fail",
    [
        lambda c: setattr(c.openai.generate_image, "side_effect", RuntimeError()),
        lambda c: setattr(c.misskey.drive.upload_bytes, "side_effect", RuntimeError()),
        lambda c: setattr(c.misskey.drive.upload_bytes, "return_value", {}),
    ],
    ids=["generate-error", "upload-error", "upload-without-id"],
)
async def test_t2x_followup_reports_failure(fail: Callable[[Any], None]) -> None:
    plugin, context = _plugin()
    fail(context)

    result = await _finish(await plugin.draw(_EVENT, "猫"))

    assert result == {"handled": True, "response": "Image generation failed."}


async def test_t2x_followup_rejects_non_image_without_uploading() -> None:
    plugin, context = _plugin(generated=b"not-an-image")

    result = await _finish(await plugin.draw(_EVENT, "猫"))

    assert result == {"handled": True, "response": "Image generation failed."}
    context.misskey.drive.upload_bytes.assert_not_awaited()


async def test_t2x_without_model_reports_unconfigured() -> None:
    plugin, context = _plugin({"model": ""})

    assert (
        await plugin.draw(_EVENT, "猫")
        == "Image generation is not configured and cannot be used."
    )
    context.openai.generate_image.assert_not_awaited()
