from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from support.factories import plugin_context

from plugins.mllm.plugin import MLLMPlugin
from twipsybot.plugin import FileRef, MentionEvent, MessageEvent, NoteRef, UserRef

_USER = UserRef(id="user-1", username="alice", host=None)
_IMAGE_PART = {"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U="}


def _file(
    file_id: str,
    mime: str | None = "image/png",
    *,
    url: str | None = None,
    thumbnail_url: str | None = None,
    name: str | None = None,
) -> FileRef:
    return FileRef(
        id=file_id,
        mime_type=mime,
        url=url or f"https://example.com/{file_id}",
        thumbnail_url=thumbnail_url,
        raw={},
        name=name,
    )


def _note(note_id: str, *files: FileRef) -> NoteRef:
    return NoteRef(id=note_id, text="", cw=None, user=_USER, files=files, raw={})


def _message(text: str, *files: FileRef) -> MessageEvent:
    return MessageEvent(
        id="message-1", text=text, user=_USER, room_id=None, files=files, raw={}
    )


def _plugin(config: dict[str, Any] | None = None) -> tuple[MLLMPlugin, AsyncMock]:
    fetch = AsyncMock(return_value=b"image")
    plugin = MLLMPlugin(
        plugin_context(
            {"enabled": True, **(config or {})},
            misskey=SimpleNamespace(drive=SimpleNamespace(fetch_bytes=fetch)),
        )
    )
    return plugin, fetch


@pytest.mark.parametrize(
    ("config", "text", "expected"),
    [
        ({}, "", {"media": [_IMAGE_PART]}),
        (
            {"default_prompt": " describe "},
            "",
            {"media": [_IMAGE_PART], "context": "describe"},
        ),
        ({"default_prompt": " describe "}, "what", {"media": [_IMAGE_PART]}),
    ],
    ids=["no-prompt", "empty-text", "text-present"],
)
async def test_mllm_default_prompt_only_applies_to_empty_text(
    config: dict[str, Any], text: str, expected: dict[str, Any]
) -> None:
    plugin, _ = _plugin(config)

    assert await plugin.on_context(_message(text, _file("file-1"))) == expected


@pytest.mark.parametrize(
    "mime", [None, "", "application/pdf", "application/octet-stream", "text/plain"]
)
async def test_mllm_only_accepts_image_audio_and_video(mime: str | None) -> None:
    plugin, fetch = _plugin()

    assert await plugin.on_context(_message("hi", _file("doc", mime))) is None
    assert await plugin.on_context(_message("hi")) is None
    fetch.assert_not_awaited()


async def test_mllm_builds_parts_by_mime() -> None:
    plugin, fetch = _plugin({"use_thumbnail": True})
    video = _file(
        "video",
        "video/mp4",
        url="https://example.com/files/raw.bin",
        thumbnail_url="https://example.com/thumb.webp",
        name="clip.mp4",
    )

    result = await plugin.on_context(
        _message("", video, _file("a.mp3", "audio/mpeg"), _file("a.ogg", "audio/ogg"))
    )

    assert result == {
        "media": [
            {
                "type": "input_file",
                "filename": "clip.mp4",
                "file_data": "data:video/mp4;base64,aW1hZ2U=",
            },
            {
                "type": "input_audio",
                "input_audio": {"data": "aW1hZ2U=", "format": "mp3"},
            },
            {
                "type": "input_file",
                "filename": "a.ogg",
                "file_data": "data:audio/ogg;base64,aW1hZ2U=",
            },
        ]
    }
    assert fetch.await_args_list[0].args[0] == "https://example.com/files/raw.bin"


@pytest.mark.parametrize(
    ("config", "url", "thumbnail_url", "fetched", "mime"),
    [
        ({}, "o.png", "t.webp", "o.png", "image/png"),
        ({"use_thumbnail": True}, "o.png", "t.webp", "t.webp", "image/webp"),
        ({"use_thumbnail": True}, "o.png", "o.png", "o.png", "image/png"),
        ({"use_thumbnail": True}, "o.png", None, "o.png", "image/png"),
        ({"use_thumbnail": True}, None, "t.webp", "t.webp", "image/webp"),
    ],
    ids=["original", "thumbnail", "same-url", "thumbnail-missing", "original-missing"],
)
async def test_mllm_selects_image_source_by_preference(
    config: dict[str, Any],
    url: str | None,
    thumbnail_url: str | None,
    fetched: str,
    mime: str,
) -> None:
    plugin, fetch = _plugin(config)
    file = FileRef(
        id="file-1",
        mime_type="image/png",
        url=url and f"https://example.com/{url}",
        thumbnail_url=thumbnail_url and f"https://example.com/{thumbnail_url}",
        raw={},
    )

    result = await plugin.on_context(_message("", file))

    assert result is not None
    [part] = result.get("media", [])
    assert part["image_url"].startswith(f"data:{mime};")
    fetch.assert_awaited_once()
    assert fetch.await_args_list[0].args[0] == f"https://example.com/{fetched}"


async def test_mllm_skips_image_without_any_url() -> None:
    plugin, fetch = _plugin({"use_thumbnail": True})
    file = FileRef(
        id="file-1", mime_type="image/png", url=None, thumbnail_url=None, raw={}
    )

    assert await plugin.on_context(_message("", file)) is None
    fetch.assert_not_awaited()


async def test_mllm_skips_failed_download() -> None:
    plugin, fetch = _plugin()
    fetch.side_effect = OSError("down")

    assert await plugin.on_context(_message("", _file("file-1"))) is None


async def test_mllm_collects_quote_and_reply_files_within_budget() -> None:
    plugin, fetch = _plugin({"max_files": 2})
    event = MentionEvent(
        id="note-1",
        text="what is this",
        cw=None,
        user=_USER,
        files=(_file("own", "video/mp4"),),
        raw={},
        reply=_note("note-2", _file("reply")),
        renote=_note("note-3", _file("quote")),
    )

    result = await plugin.on_context(event)

    assert result is not None
    assert [part["type"] for part in result.get("media", [])] == [
        "input_file",
        "input_image",
    ]
    assert [call.args[0] for call in fetch.await_args_list] == [
        "https://example.com/own",
        "https://example.com/quote",
    ]


@pytest.mark.parametrize(
    ("value", "expected"), [("6 MB", 6_000_000), ("6 MiB", 6 * 1024 * 1024)]
)
def test_mllm_parses_size_units(value: str, expected: int) -> None:
    plugin, _ = _plugin({"max_bytes": value})

    assert plugin.settings.max_bytes == expected


@pytest.mark.parametrize("value", [1024.9, -1, "invalid", True])
def test_mllm_rejects_invalid_size(value: Any) -> None:
    context = plugin_context({"enabled": True, "max_bytes": value})

    with pytest.raises(ValueError, match="max_bytes"):
        MLLMPlugin(context)
