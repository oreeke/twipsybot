from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from conftest import plugin_context

from plugins.mllm.plugin import MLLMPlugin
from twipsybot.plugin import FileRef, MentionEvent, MessageEvent, NoteRef, UserRef

_USER = UserRef(id="user-1", username="alice", host=None)
_IMAGE_PART = {"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U="}


def _file(file_id: str, mime: str | None = "image/png") -> FileRef:
    return FileRef(
        id=file_id,
        mime_type=mime,
        url=f"https://example.com/{file_id}",
        thumbnail_url=None,
        raw={},
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


async def test_mllm_returns_image_part_without_prompt_by_default() -> None:
    plugin, _ = _plugin()

    result = await plugin.on_context(_message("", _file("file-1")))

    assert result == {"media": [_IMAGE_PART]}


async def test_mllm_default_prompt_only_applies_to_empty_text() -> None:
    plugin, _ = _plugin({"default_prompt": " describe "})

    empty = await plugin.on_context(_message("", _file("file-1")))
    text = await plugin.on_context(_message("what", _file("file-1")))

    assert empty == {"media": [_IMAGE_PART], "context": "describe"}
    assert text == {"media": [_IMAGE_PART]}


@pytest.mark.parametrize(
    "mime",
    [None, "", "application/pdf", "application/octet-stream", "text/plain"],
)
async def test_mllm_only_accepts_image_audio_and_video(mime: str | None) -> None:
    plugin, fetch = _plugin()

    assert await plugin.on_context(_message("hi", _file("doc", mime))) is None
    assert await plugin.on_context(_message("hi")) is None
    fetch.assert_not_awaited()


async def test_mllm_builds_parts_by_mime() -> None:
    plugin, fetch = _plugin({"use_thumbnail": True})
    video = FileRef(
        id="video",
        mime_type="video/mp4",
        url="https://example.com/files/raw.bin",
        thumbnail_url="https://example.com/thumb.webp",
        raw={"name": "clip.mp4"},
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


async def test_mllm_skips_attachment_without_official_metadata() -> None:
    plugin, fetch = _plugin()

    assert await plugin._to_part(_file("file-1", None)) is None
    fetch.assert_not_awaited()


def test_mllm_selects_thumbnail_or_original_with_matching_mime() -> None:
    file = FileRef(
        id="file-1",
        mime_type="image/png",
        url="https://example.com/original.png",
        thumbnail_url="https://example.com/thumbnail.webp",
        raw={},
    )
    same = FileRef(
        id="file-2",
        mime_type="image/png",
        url="https://example.com/same.png",
        thumbnail_url="https://example.com/same.png",
        raw={},
    )
    original, _ = _plugin()
    thumbnail, _ = _plugin({"use_thumbnail": True})

    assert original._select_source(file, "image/png") == (file.url, "image/png")
    assert thumbnail._select_source(file, "image/png") == (
        file.thumbnail_url,
        "image/webp",
    )
    assert thumbnail._select_source(same, "image/png") == (same.url, "image/png")


def test_mllm_source_falls_back_when_preferred_url_is_missing() -> None:
    plugin, _ = _plugin({"use_thumbnail": True})
    no_urls = FileRef(
        id="file-1", mime_type="image/png", url=None, thumbnail_url=None, raw={}
    )
    only_url = _file("file-2")

    assert plugin._select_source(only_url, "image/png") == (only_url.url, "image/png")
    assert plugin._select_source(no_urls, "image/png") == (None, "image/png")


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
    ("value", "expected"),
    [
        ("6 MB", 6_000_000),
        ("6 MiB", 6 * 1024 * 1024),
    ],
)
def test_mllm_size_parsing(value: Any, expected: int) -> None:
    plugin, _ = _plugin({"max_bytes": value})

    assert plugin.settings.max_bytes == expected


@pytest.mark.parametrize("value", (1024.9, -1, "invalid", True))
def test_mllm_rejects_invalid_size(value: Any) -> None:
    context = plugin_context({"enabled": True, "max_bytes": value})

    with pytest.raises(ValueError, match="max_bytes"):
        MLLMPlugin(context)


def test_mllm_parses_boolean_strings() -> None:
    plugin, _ = _plugin({"enabled": "true", "use_thumbnail": "true"})

    assert plugin.settings.use_thumbnail is True
