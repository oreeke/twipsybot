from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from plugins.t2x.plugin import T2XPlugin
from twipsybot.plugin import HandledResult, MessageEvent, UserRef

ALICE = UserRef(id="u1", username="alice", host=None)
_PNG = b"\x89PNG\r\n\x1a\nimage"
_EVENT = MessageEvent(
    id="m1", text="画猫", user=ALICE, room_id=None, files=(), raw=cast(Any, {})
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
    assert (
        ack["response"]
        == "Image generation started; it will be sent automatically when ready."
    )
    followup = ack.get("followup")
    assert followup
    return await followup()


def test_t2x_exposes_admin_only_draw_tool() -> None:
    plugin, _ = _plugin()

    [spec] = plugin._tools
    assert (spec.name, spec.access) == ("draw", "admin")
    assert spec.schema["required"] == ["prompt"]
    assert (
        spec.schema["properties"]["prompt"]["description"] == "Description of the image"
    )


async def test_t2x_acknowledges_before_generating() -> None:
    plugin, context = _plugin()

    ack = await plugin.draw(_EVENT, "一只猫")

    assert isinstance(ack, dict)
    assert "followup" in ack
    context.openai.generate_image.assert_not_awaited()


async def test_t2x_generates_uploads_and_attaches_image() -> None:
    plugin, context = _plugin({"size": "1024x1024", "quality": "high"})

    result = await _finish(await plugin.draw(_EVENT, " 一只猫 "))

    assert result == {"handled": True, "response": "", "file_id": "file-1"}
    context.openai.generate_image.assert_awaited_once_with(
        "一只猫",
        model="gpt-image-2",
        size="1024x1024",
        quality="high",
        base_url=None,
        api_key=None,
    )
    context.misskey.drive.upload_bytes.assert_awaited_once_with(
        _PNG, name="generated.png", content_type="image/png"
    )


async def test_t2x_forwards_dedicated_endpoint() -> None:
    plugin, context = _plugin(
        {"base_url": " https://img.example/v1 ", "api_key": "secret"}
    )

    await _finish(await plugin.draw(_EVENT, "一只猫"))

    kwargs = context.openai.generate_image.await_args.kwargs
    assert kwargs["base_url"] == "https://img.example/v1"
    assert kwargs["api_key"] == "secret"
    assert context.openai.generate_image.await_args.args == ("一只猫",)


def test_t2x_requires_api_key_for_dedicated_endpoint() -> None:
    with pytest.raises(ValidationError, match="base_url requires api_key"):
        _plugin({"base_url": "https://img.example/v1"})


async def test_t2x_downloads_url_before_upload() -> None:
    plugin, context = _plugin(generated="https://example.com/image.jpg")
    context.misskey.drive.fetch_bytes.return_value = b"\xff\xd8\xffimage"

    result = await _finish(await plugin.draw(_EVENT, "猫"))

    assert result.get("file_id") == "file-1"
    context.misskey.drive.fetch_bytes.assert_awaited_once_with(
        "https://example.com/image.jpg"
    )
    context.misskey.drive.upload_bytes.assert_awaited_once_with(
        b"\xff\xd8\xffimage", name="generated.jpg", content_type="image/jpeg"
    )


@pytest.mark.parametrize(
    ("data", "name", "content_type"),
    [
        (b"RIFF\x00\x00\x00\x00WEBPdata", "generated.webp", "image/webp"),
        (_PNG, "generated.png", "image/png"),
    ],
)
async def test_t2x_detects_image_type(
    data: bytes, name: str, content_type: str
) -> None:
    plugin, context = _plugin(generated=data)

    await _finish(await plugin.draw(_EVENT, "猫"))

    context.misskey.drive.upload_bytes.assert_awaited_once_with(
        data, name=name, content_type=content_type
    )


@pytest.mark.parametrize("failure", ["generate", "upload", "no-id", "invalid"])
async def test_t2x_followup_reports_failure(failure: str) -> None:
    generated: bytes = _PNG
    if failure == "invalid":
        generated = b"not-an-image"
    plugin, context = _plugin(generated=generated)
    if failure == "generate":
        context.openai.generate_image.side_effect = RuntimeError("failed")
    elif failure == "upload":
        context.misskey.drive.upload_bytes.side_effect = RuntimeError("failed")
    elif failure == "no-id":
        context.misskey.drive.upload_bytes.return_value = {}

    result = await _finish(await plugin.draw(_EVENT, "猫"))

    assert result == {"handled": True, "response": "Image generation failed."}
    if failure == "invalid":
        context.misskey.drive.upload_bytes.assert_not_awaited()


async def test_t2x_without_model_reports_unconfigured() -> None:
    plugin, context = _plugin({"model": ""})

    assert (
        await plugin.draw(_EVENT, "猫")
        == "Image generation is not configured and cannot be used."
    )
    context.openai.generate_image.assert_not_awaited()
