from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from plugins.t2x.plugin import T2XPlugin
from twipsybot.plugin import HandledResult, MentionEvent, MessageEvent, UserRef

ALICE = UserRef(id="u1", username="alice", host=None)
_PNG = b"\x89PNG\r\n\x1a\nimage"
_FAILED = {"handled": True, "response": "图片生成失败，请稍后再试。"}


def _plugin(
    image: dict[str, Any] | None = None,
    *,
    admin: bool = True,
    generated: bytes | str = _PNG,
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
        bot=SimpleNamespace(is_admin=lambda user_id, handle: admin),
    )
    return T2XPlugin(cast(Any, context)), context


def _message(text: str) -> MessageEvent:
    return MessageEvent(
        id="m1", text=text, user=ALICE, room_id=None, files=(), raw=cast(Any, {})
    )


def _mention(text: str) -> MentionEvent:
    return MentionEvent(
        id="n1", text=text, cw=None, user=ALICE, files=(), raw=cast(Any, {})
    )


async def _finish(ack: HandledResult | None) -> HandledResult:
    assert ack is not None
    assert ack["response"] == "正在生成图片，请稍候…"
    followup = ack.get("followup")
    assert followup
    return await followup()


async def test_t2x_acknowledges_before_generating() -> None:
    plugin, context = _plugin()

    ack = await plugin.on_message(_message("/img 一只猫"))

    assert ack is not None
    assert ack["response"] == "正在生成图片，请稍候…"
    context.openai.generate_image.assert_not_awaited()


async def test_t2x_generates_uploads_and_attaches_image() -> None:
    plugin, context = _plugin({"size": "1024x1024", "quality": "high"})

    result = await _finish(await plugin.on_message(_message("/img  一只猫 ")))

    assert result == {"handled": True, "response": "图片生成完成", "file_id": "file-1"}
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

    await _finish(await plugin.on_mention(_mention("@bot\t/IMG\n一只猫")))

    kwargs = context.openai.generate_image.await_args.kwargs
    assert kwargs["base_url"] == "https://img.example/v1"
    assert kwargs["api_key"] == "secret"
    assert context.openai.generate_image.await_args.args == ("一只猫",)


def test_t2x_requires_api_key_for_dedicated_endpoint() -> None:
    with pytest.raises(ValidationError, match="base_url requires api_key"):
        _plugin({"base_url": "https://img.example/v1"})


@pytest.mark.parametrize(
    "text", ["/img", "/img   ", "/image 猫", "画一只猫", "x /img 猫"]
)
async def test_t2x_ignores_other_text(text: str) -> None:
    plugin, context = _plugin()

    assert await plugin.on_message(_message(text)) is None
    context.openai.generate_image.assert_not_awaited()


async def test_t2x_rejects_non_admin() -> None:
    plugin, context = _plugin(admin=False)

    result = await plugin.on_mention(_mention("/img 一只猫"))

    assert result == {"handled": True, "response": "您没有权限使用命令。"}
    context.openai.generate_image.assert_not_awaited()


async def test_t2x_downloads_url_before_upload() -> None:
    plugin, context = _plugin(generated="https://example.com/image.jpg")
    context.misskey.drive.fetch_bytes.return_value = b"\xff\xd8\xffimage"

    result = await _finish(await plugin.on_message(_message("/img 猫")))

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

    await _finish(await plugin.on_message(_message("/img 猫")))

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

    result = await _finish(await plugin.on_message(_message("/img 猫")))

    assert result == _FAILED
    if failure == "invalid":
        context.misskey.drive.upload_bytes.assert_not_awaited()


async def test_t2x_without_model_fails_immediately() -> None:
    plugin, context = _plugin({"model": ""})

    result = await plugin.on_message(_message("/img 猫"))

    assert result == _FAILED
    context.openai.generate_image.assert_not_awaited()
