from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from conftest import plugin_context

from plugins.vision.plugin import VisionPlugin
from twipsybot.plugin import FileRef, MessageEvent, UserRef


@pytest.mark.parametrize(
    ("uses_responses_api", "expected_image"),
    [
        (
            False,
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,aW1hZ2U="},
            },
        ),
        (
            True,
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,aW1hZ2U=",
            },
        ),
    ],
)
async def test_vision_handles_image_only_without_default_prompt(
    uses_responses_api: bool, expected_image: dict[str, Any]
) -> None:
    drive = SimpleNamespace(fetch_bytes=AsyncMock(return_value=b"image"))
    misskey = SimpleNamespace(drive=drive)
    generate_chat = AsyncMock(return_value="image reply")
    openai = SimpleNamespace(
        uses_responses_api=uses_responses_api,
        system_prompt="system",
        max_tokens=100,
        temperature=0.5,
        generate_chat=generate_chat,
    )
    plugin = VisionPlugin(
        plugin_context(
            {"enabled": True, "max_images": 1, "default_prompt": ""},
            misskey=misskey,
            openai=openai,
        )
    )
    event = MessageEvent(
        id="message-1",
        text="",
        user=UserRef(id="user-1", username="alice", host=None),
        room_id=None,
        files=(
            FileRef(
                id="file-1",
                mime_type="image/png",
                url="https://example.com/image.png",
                thumbnail_url=None,
                raw={},
            ),
        ),
        raw={},
    )

    assert await plugin.on_message(event) == {
        "handled": True,
        "response": "image reply",
    }
    generate_chat.assert_awaited_once()
    call = generate_chat.await_args
    assert call is not None
    messages = call.args[0]
    assert messages[-1]["content"] == [expected_image]


async def test_vision_skips_attachment_without_official_metadata() -> None:
    drive = SimpleNamespace(
        fetch_bytes=AsyncMock(return_value=b"image"),
        show_file=AsyncMock(return_value={"type": "image/png"}),
        download_bytes=AsyncMock(),
    )
    plugin = VisionPlugin(
        plugin_context(
            {"enabled": True, "default_prompt": "describe"},
            misskey=SimpleNamespace(drive=drive),
        )
    )
    file = FileRef(
        id="file-1",
        mime_type=None,
        url="https://example.com/image.png",
        thumbnail_url=None,
        raw={},
    )

    result = await plugin._to_image_part(file, use_responses=False)

    assert result is None
    drive.show_file.assert_not_awaited()
    drive.fetch_bytes.assert_not_awaited()
    drive.download_bytes.assert_not_awaited()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("6 MB", 6_000_000),
        ("6 MiB", 6 * 1024 * 1024),
    ],
)
def test_vision_size_parsing(value: Any, expected: int) -> None:
    plugin = VisionPlugin(
        plugin_context(
            {"enabled": True, "max_bytes": value, "default_prompt": "describe"}
        )
    )

    assert plugin.settings.max_bytes == expected


@pytest.mark.parametrize("value", (1024.9, -1, "invalid", True))
def test_vision_rejects_invalid_size(value: Any) -> None:
    context = plugin_context({"enabled": True, "max_bytes": value})

    with pytest.raises(ValueError, match="max_bytes"):
        VisionPlugin(context)


def test_vision_parses_boolean_strings() -> None:
    plugin = VisionPlugin(
        plugin_context(
            {"enabled": "true", "use_thumbnail": "false", "default_prompt": "describe"}
        )
    )

    assert plugin.settings.use_thumbnail is False
