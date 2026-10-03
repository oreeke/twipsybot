from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from plugins.iincho.plugin import IinchoPlugin, _Sample
from plugins.keyact.plugin import KeyActPlugin
from plugins.radar.plugin import RadarPlugin
from plugins.topics.plugin import TopicsPlugin
from plugins.vision.plugin import VisionPlugin
from twipsybot.plugin import (
    AutoPostEvent,
    FileRef,
    MentionEvent,
    MessageEvent,
    TimelineNoteEvent,
    UserRef,
)


def _context(config: dict[str, Any], **services: Any) -> Any:
    defaults = {
        "name": "Test",
        "config": config,
        "storage": SimpleNamespace(),
        "misskey": SimpleNamespace(),
        "openai": SimpleNamespace(),
        "bot": SimpleNamespace(),
    }
    defaults.update(services)
    return SimpleNamespace(**defaults)


def test_keyact_parses_boolean_strings() -> None:
    plugin = KeyActPlugin(
        _context(
            {
                "enabled": True,
                "mention_enabled": "false",
                "chat_enabled": "true",
                "case_sensitive": "false",
                "rules": [],
            }
        )
    )

    assert plugin.settings.mention_enabled is False
    assert plugin.settings.chat_enabled is True
    assert plugin.settings.case_sensitive is False


def test_builtin_plugins_parse_boolean_strings() -> None:
    radar = RadarPlugin(
        _context({"enabled": "true", "reply": "false", "quote": "true"})
    )
    topics = TopicsPlugin(
        _context({"enabled": "true", "rss_ai": "false", "txt_ai_prefix": "{topic}"})
    )
    vision = VisionPlugin(
        _context(
            {
                "enabled": "true",
                "use_thumbnail": "false",
                "default_prompt": "describe",
            }
        )
    )

    assert radar._enabled is True
    assert radar.settings.reply_enabled is False
    assert radar.settings.quote_enabled is True
    assert topics.settings.rss_ai is False
    assert vision.settings.use_thumbnail is False


@pytest.mark.parametrize("field", ("prompt", "system_prompt"))
def test_iincho_requires_prompts(field: str) -> None:
    config = {
        "interval": "5m",
        "prompt": "summarize",
        "system_prompt": "analyst",
        field: " ",
    }
    context = _context(config)

    with pytest.raises(ValueError, match=rf"{field} must not be empty"):
        IinchoPlugin(context)


@pytest.mark.parametrize(
    ("config", "field"),
    [
        ({"reply": True, "reply_ai": True, "reply_ai_prompt": ""}, "reply_ai_prompt"),
        ({"quote": True, "quote_ai": True, "quote_ai_prompt": ""}, "quote_ai_prompt"),
    ],
)
def test_radar_requires_enabled_ai_prompt(config: dict[str, Any], field: str) -> None:
    context = _context(config)

    with pytest.raises(ValueError, match=rf"{field} must not be empty"):
        RadarPlugin(context)


@pytest.mark.parametrize(
    ("config", "field"),
    [
        ({"source": "txt", "txt_ai_prefix": ""}, "txt_ai_prefix"),
        ({"source": "rss", "rss_ai": True, "rss_ai_prefix": ""}, "rss_ai_prefix"),
    ],
)
def test_topics_requires_active_prompt(config: dict[str, Any], field: str) -> None:
    context = _context(config)

    with pytest.raises(ValueError, match=rf"{field} must not be empty"):
        TopicsPlugin(context)


async def test_keyact_matches_body_when_mention_has_cw() -> None:
    plugin = KeyActPlugin(
        _context(
            {
                "enabled": True,
                "rules": [{"keywords": ["ping"], "response": "pong"}],
            }
        )
    )
    await plugin.initialize()
    event = MentionEvent(
        id="note-1",
        text="ping",
        cw="content warning",
        user=UserRef(id="user-1", username="alice", host=None),
        files=(),
        raw={},
    )

    assert await plugin.on_mention(event) == {"handled": True, "response": "pong"}


async def test_keyact_normalizes_case_once_when_loading_rules() -> None:
    plugin = KeyActPlugin(
        _context(
            {
                "enabled": True,
                "rules": [{"keywords": ["PING"], "response": "pong"}],
            }
        )
    )
    await plugin.initialize()
    event = MessageEvent(
        id="message-1",
        text="ping",
        user=UserRef(id="user-1", username="alice", host=None),
        room_id=None,
        files=(),
        raw={},
    )

    assert await plugin.on_message(event) == {"handled": True, "response": "pong"}


async def test_keyact_reads_line_rules() -> None:
    plugin = KeyActPlugin(
        _context(
            {
                "enabled": True,
                "rules": "# 问候\nping, hi = pong\n帮助，help | ? = 看文档\\n#话题\n",
            }
        )
    )
    await plugin.initialize()

    assert [(rule.keywords, rule.response) for rule in plugin.rules] == [
        (("ping", "hi"), "pong"),
        (("帮助", "help", "?"), "看文档\n#话题"),
    ]


def test_keyact_rejects_rule_without_response() -> None:
    with pytest.raises(ValueError, match="keywords = response"):
        KeyActPlugin(_context({"enabled": True, "rules": "ping"}))


def test_topics_rss_list_accepts_lines_with_comments() -> None:
    plugin = TopicsPlugin(
        _context(
            {"enabled": True, "rss_list": "# 科技\nhttps://a/rss\n\nhttps://b/rss#x"}
        )
    )

    assert plugin.settings.rss_list == ("https://a/rss", "https://b/rss#x")
    assert plugin.settings.rss_post_mode == "rotate"


async def test_topics_rss_ai_uses_public_openai_service() -> None:
    generate_text = AsyncMock(return_value="rewritten title\nignored")
    openai = SimpleNamespace(
        generate_text=generate_text,
        system_prompt="system",
        max_tokens=100,
        temperature=0.5,
    )
    plugin = TopicsPlugin(
        _context(
            {
                "enabled": True,
                "source": "rss",
                "rss_ai": True,
                "rss_ai_prefix": "{title}",
            },
            openai=openai,
        )
    )

    result = await plugin._rewrite_rss_title_with_ai(
        "original", "https://example.com", summary="summary"
    )

    assert result == "rewritten title"
    generate_text.assert_awaited_once_with(
        "original",
        "system",
        max_tokens=100,
        temperature=0.5,
    )


@pytest.mark.parametrize("topic", ("science", "https://example.com/article"))
async def test_topics_txt_uses_configured_prompt(topic: str) -> None:
    plugin = TopicsPlugin(
        _context({"enabled": True, "txt_ai_prefix": "Topic:\n{topic}"})
    )
    plugin._get_next_topic = AsyncMock(return_value=topic)

    result = await plugin.on_auto_post(AutoPostEvent(datetime.now(UTC)))

    assert result == {"prompt": f"Topic:\n{topic}"}


async def test_topics_prefers_custom_file_from_prompts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "topics.txt").write_text("custom topic\n", encoding="utf-8")
    plugin = TopicsPlugin(_context({"enabled": True, "txt_ai_prefix": "{topic}"}))

    await plugin._load_topics()

    assert plugin.topics == ["custom topic"]


async def test_topics_txt_rotates_from_configured_start_line() -> None:
    values: dict[str, str] = {}

    async def get_value(key: str) -> str | None:
        return values.get(key)

    async def set_value(key: str, value: str) -> None:
        values[key] = value

    storage = SimpleNamespace(
        get=AsyncMock(side_effect=get_value), set=AsyncMock(side_effect=set_value)
    )
    plugin = TopicsPlugin(
        _context(
            {
                "enabled": True,
                "txt_ai_prefix": "Topic: {topic}",
                "txt_start_line": 2,
            },
            storage=storage,
        )
    )

    async def load_topics() -> None:
        plugin.topics = ["first", "second", "third"]

    plugin._load_topics = load_topics

    assert await plugin.initialize() is True
    event = AutoPostEvent(datetime.now(UTC))
    assert await plugin.on_auto_post(event) == {"prompt": "Topic: second"}
    assert await plugin.on_auto_post(event) == {"prompt": "Topic: third"}
    assert await plugin.on_auto_post(event) == {"prompt": "Topic: first"}
    assert values["last_used_line"] == "1"


async def test_topics_initializes_rss_storage_defaults() -> None:
    storage = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    plugin = TopicsPlugin(_context({"enabled": True, "source": "rss"}, storage=storage))

    assert await plugin.initialize() is True
    storage.set.assert_any_await("rss_recent_keys", "[]")
    storage.set.assert_any_await("rss_last_feed_idx", "0")


async def test_topics_rss_batch_selects_latest_unpublished_entry_per_feed() -> None:
    plugin = TopicsPlugin(
        _context(
            {
                "enabled": True,
                "source": "rss",
                "rss_list": ["feed-a", "feed-b"],
                "rss_post_mode": "batch",
            }
        )
    )
    candidates = [
        {
            "key": "older-a",
            "title": "Older A",
            "link": "https://example.com/older-a",
            "summary": "",
            "ts": 1,
            "feed_idx": 0,
            "entry_idx": 1,
        },
        {
            "key": "latest-a",
            "title": "Latest A",
            "link": "https://example.com/latest-a",
            "summary": "Summary A",
            "ts": 2,
            "feed_idx": 0,
            "entry_idx": 0,
        },
        {
            "key": "published-b",
            "title": "Published B",
            "link": "https://example.com/published-b",
            "summary": "",
            "ts": 3,
            "feed_idx": 1,
            "entry_idx": 0,
        },
        {
            "key": "available-b",
            "title": "Available B",
            "link": "https://example.com/available-b",
            "summary": "",
            "ts": 2,
            "feed_idx": 1,
            "entry_idx": 1,
        },
    ]
    plugin._get_recent_rss_keys = AsyncMock(return_value=["published-b"])
    plugin._fetch_all_rss_candidates = AsyncMock(return_value=candidates)

    assert await plugin.on_auto_post(AutoPostEvent(datetime.now(UTC))) == {
        "contents": [
            "📡 Summary A\n\n📎 https://example.com/latest-a",
            "📡 Available B\n\n📎 https://example.com/available-b",
        ]
    }
    assert plugin._pending_rss == {
        "📡 Summary A\n\n📎 https://example.com/latest-a": [("latest-a", None)],
        "📡 Available B\n\n📎 https://example.com/available-b": [("available-b", None)],
    }


async def test_topics_rss_fetch_parses_valid_entries() -> None:
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
<guid>entry-1</guid>
<title>Release</title>
<link>https://example.com/release</link>
<description><![CDATA[<p>New &amp; improved</p>]]></description>
<pubDate>Tue, 15 Sep 2026 12:00:00 GMT</pubDate>
</item>
<item><title>Missing link</title></item>
</channel></rss>"""

    @asynccontextmanager
    async def response():
        yield SimpleNamespace(status=200, read=AsyncMock(return_value=rss))

    session: Any = SimpleNamespace(get=lambda url: response())
    plugin = TopicsPlugin(_context({"enabled": True, "source": "rss"}))

    candidates = await plugin._fetch_rss_candidates(
        session, "https://example.com/feed", feed_idx=2
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["title"] == "Release"
    assert candidate["link"] == "https://example.com/release"
    assert candidate["summary"] == "New & improved"
    assert candidate["ts"] == int(datetime(2026, 9, 15, 12, tzinfo=UTC).timestamp())
    assert candidate["feed_idx"] == 2
    assert candidate["entry_idx"] == 0
    assert len(candidate["key"]) == 64


async def test_topics_rss_fetch_rejects_http_error() -> None:
    @asynccontextmanager
    async def response():
        yield SimpleNamespace(status=503)

    session: Any = SimpleNamespace(get=lambda url: response())
    plugin = TopicsPlugin(_context({"enabled": True, "source": "rss"}))

    with pytest.raises(ValueError, match="HTTP 503"):
        await plugin._fetch_rss_candidates(
            session, "https://example.com/feed", feed_idx=0
        )


async def test_topics_rss_batch_isolates_failed_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = TopicsPlugin(_context({"enabled": True, "source": "rss"}))
    candidate = {"key": "available", "feed_idx": 1}

    @asynccontextmanager
    async def rss_session():
        yield SimpleNamespace()

    async def fetch_candidates(session: Any, url: str, *, feed_idx: int):
        if url == "failed":
            raise ValueError("offline")
        return [candidate]

    monkeypatch.setattr(plugin, "_rss_session", rss_session)
    plugin._fetch_rss_candidates = AsyncMock(side_effect=fetch_candidates)

    assert await plugin._fetch_all_rss_candidates(["failed", "available"]) == [
        candidate
    ]


async def test_topics_rss_is_recorded_only_after_publish() -> None:
    storage = SimpleNamespace()
    plugin = TopicsPlugin(
        _context(
            {
                "enabled": True,
                "source": "rss",
                "rss_list": ["feed"],
                "rss_post_mode": "batch",
            },
            storage=storage,
        )
    )
    entry = {
        "key": "entry-key",
        "title": "title",
        "link": "https://example.com/post",
        "summary": "",
    }
    plugin._get_recent_rss_keys = AsyncMock(return_value=[])
    plugin._fetch_all_rss_candidates = AsyncMock(return_value=[entry])
    plugin._select_latest_per_feed = lambda urls, candidates, recent_set: [entry]
    plugin._render_selected_rss_entries = AsyncMock(return_value=["content"])
    plugin._set_recent_rss_keys = AsyncMock(return_value=True)

    assert await plugin._get_next_rss_posts() == ["content"]
    plugin._set_recent_rss_keys.assert_not_awaited()

    await plugin.on_auto_post_published("content")

    plugin._set_recent_rss_keys.assert_awaited_once_with(["entry-key"])

    plugin._pending_rss["failed"] = [("failed-key", None)]
    plugin._set_recent_rss_keys = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError, match="persist published RSS entry"):
        await plugin.on_auto_post_published("failed")


async def test_topics_rss_retains_2000_published_keys() -> None:
    plugin = TopicsPlugin(_context({"enabled": True, "source": "rss"}))
    plugin._pending_rss["content"] = [("new-key", None)]
    plugin._get_recent_rss_keys = AsyncMock(
        return_value=[f"key-{index}" for index in range(2000)]
    )
    plugin._set_recent_rss_keys = AsyncMock(return_value=True)

    await plugin.on_auto_post_published("content")

    saved_keys = plugin._set_recent_rss_keys.await_args_list[0].args[0]
    assert len(saved_keys) == 2000
    assert saved_keys[0] == "key-1"
    assert saved_keys[-1] == "new-key"


async def test_topics_rotate_skips_published_and_failed_feeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = TopicsPlugin(
        _context(
            {
                "enabled": True,
                "source": "rss",
                "rss_post_mode": "rotate",
            }
        )
    )
    published = {
        "key": "published",
        "title": "Published",
        "link": "https://example.com/published",
        "summary": "",
        "ts": 3,
        "entry_idx": 0,
    }
    available = {
        "key": "available",
        "title": "Available",
        "link": "https://example.com/available",
        "summary": "",
        "ts": 2,
        "entry_idx": 0,
    }

    @asynccontextmanager
    async def rss_session():
        yield SimpleNamespace()

    monkeypatch.setattr(plugin, "_rss_session", rss_session)
    plugin._get_recent_rss_keys = AsyncMock(return_value=["published"])
    plugin._get_stored_int = AsyncMock(return_value=1)
    plugin._fetch_rss_candidates = AsyncMock(
        side_effect=[[published], ValueError("offline"), [available]]
    )

    assert await plugin._get_next_rss_posts_rotate(["feed-a", "feed-b", "feed-c"]) == [
        "📡 Available\n\n📎 https://example.com/available"
    ]
    assert [
        call.kwargs["feed_idx"] for call in plugin._fetch_rss_candidates.await_args_list
    ] == [
        1,
        2,
        0,
    ]
    assert plugin._pending_rss == {
        "📡 Available\n\n📎 https://example.com/available": [("available", 1)]
    }


async def test_topics_rotate_advances_only_after_publish() -> None:
    stored = {"rss_recent_keys": "[]", "rss_last_feed_idx": "0"}
    storage = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: stored.get(key)), set=AsyncMock()
    )
    plugin = TopicsPlugin(
        _context(
            {
                "enabled": True,
                "source": "rss",
                "rss_post_mode": "rotate",
            },
            storage=storage,
        )
    )
    entry = {
        "key": "entry-key",
        "title": "title",
        "link": "https://example.com/post",
        "summary": "",
        "ts": 1,
        "entry_idx": 0,
        "feed_idx": 0,
    }
    plugin._fetch_rss_candidates = AsyncMock(return_value=[entry])
    plugin._render_selected_rss_entries = AsyncMock(return_value=["content"])

    assert await plugin._get_next_rss_posts_rotate(["feed"]) == ["content"]
    storage.set.assert_not_awaited()

    await plugin.on_auto_post_published("content")

    storage.set.assert_any_await("rss_recent_keys", '["entry-key"]')
    storage.set.assert_any_await("rss_last_feed_idx", "0")


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
        _context(
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
        _context(
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
        _context({"enabled": True, "max_bytes": value, "default_prompt": "describe"})
    )

    assert plugin.settings.max_bytes == expected


@pytest.mark.parametrize("value", (1024.9, -1, "invalid", True))
def test_vision_rejects_invalid_size(value: Any) -> None:
    context = _context({"enabled": True, "max_bytes": value})

    with pytest.raises(ValueError, match="max_bytes"):
        VisionPlugin(context)


async def test_radar_reacts_through_public_misskey_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("plugins.radar.plugin._DELAY_RANGE", (0.0, 0.0))
    create_reaction = AsyncMock(return_value={})
    misskey = SimpleNamespace(create_reaction=create_reaction)

    @asynccontextmanager
    async def actor_lock(user_id: str | None, username: str | None):
        yield

    bot = SimpleNamespace(user_id="bot-id", username="bot", actor_lock=actor_lock)
    plugin = RadarPlugin(
        _context(
            {"enabled": True, "reaction": "heart"},
            misskey=misskey,
            bot=bot,
        )
    )
    raw = {
        "id": "note-1",
        "text": "hello",
        "user": {"id": "user-1", "username": "alice"},
        "streamingChannel": "antenna",
    }
    event = TimelineNoteEvent(
        id="note-1",
        text="hello",
        cw=None,
        user=UserRef(id="user-1", username="alice", host=None),
        channel="antenna",
        files=(),
        raw=raw,
    )

    await plugin.on_timeline_note(event)
    await plugin.on_timeline_note(event)
    await asyncio.gather(*plugin._pending.values())

    create_reaction.assert_awaited_once_with("note-1", "heart")
    assert not plugin._pending

    monkeypatch.setattr("plugins.radar.plugin._DELAY_RANGE", (60.0, 60.0))
    await plugin.on_timeline_note(
        TimelineNoteEvent(
            id="note-4",
            text="hello",
            cw=None,
            user=UserRef(id="user-1", username="alice", host=None),
            channel="antenna",
            files=(),
            raw=raw,
        )
    )
    await plugin.on_shutdown()
    assert not plugin._pending

    remote_same_name = TimelineNoteEvent(
        id="note-2",
        text="hello",
        cw=None,
        user=UserRef(id="remote-id", username="bot", host="remote.example"),
        channel="antenna",
        files=(),
        raw={},
    )
    assert plugin._should_skip_self(remote_same_name) is False


async def test_radar_ai_uses_configured_prompt() -> None:
    generate_text = AsyncMock(return_value="generated reply")
    create_note = AsyncMock(return_value={})
    plugin = RadarPlugin(
        _context(
            {
                "enabled": True,
                "reply": True,
                "reply_ai": True,
                "reply_ai_prompt": "Reply:\n{content}",
            },
            misskey=SimpleNamespace(create_note=create_note),
            openai=SimpleNamespace(
                generate_text=generate_text,
                system_prompt="system",
                max_tokens=100,
                temperature=0.5,
            ),
        )
    )

    await plugin._maybe_reply({"text": "hello"}, "note-1", "antenna")

    generate_text.assert_awaited_once_with(
        "Reply:\nhello",
        "system",
        max_tokens=100,
        temperature=0.5,
    )
    create_note.assert_awaited_once_with(
        text="generated reply", reply_id="note-1", local_only=False
    )


async def test_radar_preserves_specified_reply_visibility() -> None:
    create_note = AsyncMock(return_value={})
    plugin = RadarPlugin(
        _context(
            {"enabled": True, "reply": True, "reply_text": "reply"},
            misskey=SimpleNamespace(create_note=create_note),
        )
    )

    await plugin._maybe_reply(
        {"text": "hello", "visibility": "specified"}, "note-1", "antenna"
    )

    create_note.assert_awaited_once_with(
        text="reply",
        visibility="specified",
        reply_id="note-1",
        local_only=False,
    )


async def test_radar_preserves_reply_and_quote_precedence() -> None:
    misskey = SimpleNamespace(
        create_reaction=AsyncMock(return_value={}),
        create_note=AsyncMock(return_value={}),
        create_renote=AsyncMock(return_value={}),
    )
    plugin = RadarPlugin(
        _context(
            {
                "enabled": True,
                "reply": True,
                "reply_text": "hello {username}",
                "quote": True,
                "quote_text": "quote",
                "renote": True,
            },
            misskey=misskey,
        )
    )

    await plugin._act({"user": {"username": "alice"}}, "note-1", "antenna")

    misskey.create_note.assert_awaited_once_with(
        text="hello alice", reply_id="note-1", local_only=False
    )
    misskey.create_renote.assert_awaited_once_with(
        "note-1", visibility=None, text="quote", local_only=False
    )


def _iincho_context(config: dict[str, Any] | None = None) -> Any:
    openai = SimpleNamespace(
        generate_text=AsyncMock(),
        moderate_texts=AsyncMock(
            side_effect=lambda texts: [frozenset() for _ in texts]
        ),
    )
    misskey = SimpleNamespace(
        instance_url="https://misskey.example",
        create_note=AsyncMock(return_value={}),
        send_message=AsyncMock(return_value={}),
    )
    return SimpleNamespace(
        name="iincho",
        config={
            "enabled": True,
            "interval": "5m",
            "prompt": "总结不可信帖子数组的整体趋势。",
            "system_prompt": "你是社区趋势分析员。",
            **(config or {}),
        },
        storage=SimpleNamespace(),
        openai=openai,
        misskey=misskey,
        bot=SimpleNamespace(user_id="bot-id", username="iincho"),
    )


def _iincho_event(
    text: str = "本地帖子",
    *,
    event_id: str = "note-1",
    channel: str = "localTimeline",
    user_id: str = "user-1",
    username: str = "user",
    cw: str | None = None,
) -> TimelineNoteEvent:
    return TimelineNoteEvent(
        id=event_id,
        text=text,
        cw=cw,
        user=UserRef(id=user_id, username=username, host=None),
        channel=channel,
        files=(),
        raw=MappingProxyType({}),
    )


def _iincho_result() -> str:
    return json.dumps({"trends": ["新功能体验", "部署问题"]}, ensure_ascii=False)


def _iincho_flag_all(context: Any) -> None:
    context.openai.moderate_texts.side_effect = lambda texts: [
        frozenset({"hate"}) for _ in texts
    ]


@pytest.mark.parametrize("interval", ("4m", "nope", 0))
def test_iincho_rejects_invalid_interval(interval: Any) -> None:
    context = _iincho_context({"interval": interval})

    with pytest.raises(ValueError, match="interval"):
        IinchoPlugin(context)


def test_iincho_uses_defaults_for_one_hundred_notes() -> None:
    plugin = IinchoPlugin(_iincho_context())

    assert plugin.settings.sample_size == 100
    assert plugin.settings.max_input_chars == 24000
    assert plugin.settings.max_tokens == 2000


def test_iincho_rejects_fractional_integer_config() -> None:
    context = _iincho_context({"sample_size": 10.5})

    with pytest.raises(ValueError, match="sample_size"):
        IinchoPlugin(context)


@pytest.mark.parametrize("admin_ids", (None, "", ["", " "]))
def test_iincho_accepts_empty_admin_ids(admin_ids: Any) -> None:
    plugin = IinchoPlugin(_iincho_context({"admin_ids": admin_ids}))

    assert plugin.settings.admin_ids == ()


async def test_iincho_collects_only_eligible_local_notes() -> None:
    plugin = IinchoPlugin(_iincho_context({"sample_size": 2, "min_notes": 1}))

    await plugin.on_timeline_note(_iincho_event(channel="globalTimeline"))
    await plugin.on_timeline_note(_iincho_event(user_id="bot-id"))
    await plugin.on_timeline_note(_iincho_event(text=""))
    await plugin.on_timeline_note(_iincho_event(text="正文", cw="预警"))

    assert plugin._window.eligible == 1
    assert [sample.text for sample in plugin._window.samples] == ["预警\n正文"]


async def test_iincho_reservoir_stays_bounded() -> None:
    plugin = IinchoPlugin(_iincho_context({"sample_size": 2, "min_notes": 1}))
    plugin._rng.seed(1)

    for index in range(20):
        await plugin.on_timeline_note(
            _iincho_event(str(index), event_id=str(index), user_id=str(index))
        )

    assert plugin._window.eligible == 20
    assert len(plugin._window.samples) == 2


async def test_iincho_publishes_formatted_summary() -> None:
    context = _iincho_context(
        {"sample_size": 2, "min_notes": 2, "admin_ids": ["admin-1"]}
    )
    context.openai.generate_text.return_value = _iincho_result()
    context.openai.moderate_texts.side_effect = None
    context.openai.moderate_texts.return_value = [
        frozenset({"harassment", "harassment/threatening"}),
        frozenset({"illicit"}),
    ]
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(
        _iincho_event("第一条 https://example.com @alice", event_id="1")
    )
    await plugin.on_timeline_note(_iincho_event("第二条", event_id="2"))

    await plugin._process_window()

    prompt = context.openai.generate_text.await_args.args[0]
    assert "不可信帖子数组" in prompt
    assert "第一条" in prompt
    assert "example.com" not in prompt
    assert "@alice" not in prompt
    assert json.loads(prompt.partition("DATA=")[2]) == [
        "第一条 [链接] [账号]",
        "第二条",
    ]
    context.openai.moderate_texts.assert_awaited_once_with(
        ["第一条 [链接] [账号]", "第二条"]
    )
    assert context.openai.generate_text.await_args.kwargs["json_output"] is True
    created = context.misskey.create_note.await_args.kwargs
    assert created["visibility"] == "public"
    assert created["local_only"] is True
    assert "validate_reply" not in created
    assert created["text"].startswith("💡 Iincho 时间线观察\n\n🕒")
    assert " 至 " in created["text"]
    assert (
        "\n\n\N{TEST TUBE} 送检模型：\n\N{ROBOT FACE} omni-moderation-latest\n"
        in created["text"]
    )
    assert "本地时间线观察" not in created["text"]
    assert "概览" not in created["text"]
    assert "热点" not in created["text"]
    assert "氛围" not in created["text"]
    assert "🚨 违规审查：" in created["text"]
    assert "💢 骚扰攻击 1" in created["text"]
    assert "⚖️ 违法活动 1" in created["text"]


async def test_iincho_limits_serialized_input() -> None:
    context = _iincho_context(
        {"min_notes": 1, "max_input_chars": 1000, "admin_ids": ["admin-1"]}
    )
    context.openai.generate_text.return_value = _iincho_result()
    _iincho_flag_all(context)
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event("\\" * 2000, event_id="1"))
    await plugin.on_timeline_note(_iincho_event("第二条", event_id="2"))
    await plugin.on_timeline_note(_iincho_event("第三条", event_id="3"))

    await plugin._process_window()

    prompt = context.openai.generate_text.await_args.args[0]
    payload = prompt.partition("DATA=")[2]
    assert len(payload) <= 1000
    assert json.loads(payload)[0]
    context.openai.moderate_texts.assert_awaited_once_with(json.loads(payload))
    summary = context.misskey.create_note.await_args.kwargs["text"]
    assert "覆盖 3 篇有效帖子，AI 均匀抽样 1 篇" in summary


def test_iincho_serializes_special_characters_losslessly() -> None:
    plugin = IinchoPlugin(_iincho_context({"min_notes": 1}))

    payload, selected = plugin._serialize_samples(
        [_Sample(note_id='id"\\', text='line 1\n"line 2"\\')]
    )

    assert [sample.note_id for sample in selected] == ['id"\\']
    assert json.loads(payload) == ['line 1\n"line 2"\\']


async def test_iincho_keeps_notes_arriving_during_generation() -> None:
    context = _iincho_context(
        {"sample_size": 2, "min_notes": 1, "admin_ids": ["admin-1"]}
    )
    _iincho_flag_all(context)
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event("旧窗口"))

    async def generate(*args: Any, **kwargs: Any) -> str:
        await plugin.on_timeline_note(_iincho_event("新窗口", event_id="new"))
        return _iincho_result()

    context.openai.generate_text.side_effect = generate
    await plugin._process_window()

    assert [sample.text for sample in plugin._window.samples] == ["新窗口"]
    assert plugin._window.eligible == 1


async def test_iincho_notifies_all_admins_with_verified_note_link() -> None:
    context = _iincho_context(
        {"min_notes": 1, "admin_ids": ["admin-1", "admin-2", "admin-1"]}
    )
    context.openai.generate_text.return_value = _iincho_result()
    context.openai.moderate_texts.side_effect = None
    context.openai.moderate_texts.return_value = [
        frozenset({"harassment", "harassment/threatening", "illicit"})
    ]
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    await plugin._process_window()

    assert context.misskey.send_message.await_count == 2
    for call in context.misskey.send_message.await_args_list:
        message = call.args[1]
        assert message.startswith(
            "\N{ELECTRIC LIGHT BULB} Iincho 近期小报告\n\n🔥 热点"
        )
        assert "💢 骚扰攻击、⚖️ 违法活动: note-1" in message
        assert "https://misskey.example" not in message
        assert "• 新功能体验" in message
    assert {call.args[0] for call in context.misskey.send_message.await_args_list} == {
        "admin-1",
        "admin-2",
    }


async def test_iincho_admin_failure_does_not_block_others_or_summary() -> None:
    context = _iincho_context({"min_notes": 1, "admin_ids": "admin-1, admin-2"})
    context.openai.generate_text.return_value = _iincho_result()
    context.openai.moderate_texts.side_effect = None
    context.openai.moderate_texts.return_value = [frozenset({"harassment"})]
    context.misskey.send_message.side_effect = [RuntimeError("unavailable"), {}]
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    await plugin._process_window()

    assert [call.args[0] for call in context.misskey.send_message.await_args_list] == [
        "admin-1",
        "admin-2",
    ]
    context.misskey.create_note.assert_awaited_once()


async def test_iincho_skips_trends_and_admins_without_violations() -> None:
    context = _iincho_context({"min_notes": 1, "admin_ids": ["admin-1"]})
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    await plugin._process_window()

    context.openai.generate_text.assert_not_awaited()
    context.misskey.send_message.assert_not_awaited()
    context.misskey.create_note.assert_awaited_once()


async def test_iincho_skips_trends_without_admins() -> None:
    context = _iincho_context({"min_notes": 1})
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    await plugin._process_window()

    context.openai.generate_text.assert_not_awaited()
    context.openai.moderate_texts.assert_awaited_once_with(["本地帖子"])
    context.misskey.create_note.assert_awaited_once()


async def test_iincho_discards_invalid_ai_result_without_retry() -> None:
    context = _iincho_context({"min_notes": 1, "admin_ids": ["admin-1"]})
    context.openai.generate_text.return_value = "not json"
    _iincho_flag_all(context)
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    with pytest.raises(ValueError):
        await plugin._process_window()

    context.openai.generate_text.assert_awaited_once()
    context.misskey.create_note.assert_not_awaited()
    assert plugin._window.eligible == 0


async def test_iincho_rejects_mismatched_moderation_results() -> None:
    context = _iincho_context({"min_notes": 1})
    context.openai.generate_text.return_value = _iincho_result()
    context.openai.moderate_texts.side_effect = None
    context.openai.moderate_texts.return_value = []
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    with pytest.raises(ValueError, match="count mismatch"):
        await plugin._process_window()

    context.misskey.create_note.assert_not_awaited()


async def test_iincho_skips_small_window() -> None:
    context = _iincho_context({"min_notes": 2})
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    await plugin._process_window()

    context.openai.generate_text.assert_not_awaited()
    context.openai.moderate_texts.assert_not_awaited()
    context.misskey.create_note.assert_not_awaited()


async def test_iincho_stops_background_task() -> None:
    plugin = IinchoPlugin(_iincho_context())

    await plugin.on_startup()
    assert plugin._task is not None
    await plugin.on_shutdown()

    assert plugin._task is None


async def test_iincho_maps_openai_minors_category() -> None:
    context = _iincho_context({"min_notes": 1})
    context.openai.moderate_texts.side_effect = None
    context.openai.moderate_texts.return_value = [
        frozenset({"sexual", "sexual/minors", "violence/graphic"})
    ]
    plugin = IinchoPlugin(context)

    assert await plugin._moderate(["x"]) == [
        frozenset({"sexual", "minors", "violence"})
    ]


_CF_ID = "0123456789abcdef0123456789abcdef"


def _cloudflare_config(**overrides: Any) -> dict[str, Any]:
    return {
        "min_notes": 1,
        "moderation": {
            "provider": "cloudflare",
            "cf_account_id": _CF_ID,
            "cf_api_token": "cf-token",
            **overrides,
        },
    }


@pytest.mark.parametrize(
    "moderation",
    (
        {"provider": "cloudflare"},
        {"provider": "cloudflare", "cf_account_id": _CF_ID},
        {"provider": "cloudflare", "cf_account_id": "../x", "cf_api_token": "t"},
        {"provider": "other"},
    ),
)
def test_iincho_rejects_invalid_moderation_config(moderation: dict[str, Any]) -> None:
    context = _iincho_context({"moderation": moderation})

    with pytest.raises(ValueError):
        IinchoPlugin(context)


@asynccontextmanager
async def _cloudflare_server(
    monkeypatch: pytest.MonkeyPatch, replies: dict[str, Any], status: int = 200
):
    calls: list[dict[str, Any]] = []

    async def run(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append({"auth": request.headers.get("Authorization"), "body": body})
        response = replies[body["messages"][0]["content"]]
        return web.json_response(
            {"success": status == 200, "errors": [], "result": {"response": response}},
            status=status,
        )

    app = web.Application()
    app.router.add_post(f"/accounts/{_CF_ID}/ai/run/@cf/meta/llama-guard-3-8b", run)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(
        "plugins.iincho.plugin._CF_API", str(server.make_url("")).rstrip("/")
    )
    try:
        yield calls
    finally:
        await server.close()


async def test_iincho_cloudflare_moderation_maps_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = {
        "safe": {"safe": True, "categories": []},
        "bad": {"safe": False, "categories": ["S1", "S4", "S6", "S10"]},
        "text": "unsafe\nS12,S7",
    }
    context = _iincho_context(_cloudflare_config())
    plugin = IinchoPlugin(context)
    async with _cloudflare_server(monkeypatch, replies) as calls:
        await plugin.initialize()
        result = await plugin._moderate(list(replies))
        await plugin.cleanup()

    assert result == [
        frozenset(),
        frozenset({"violence", "minors", "hate"}),
        frozenset({"sexual", "privacy"}),
    ]
    assert {call["auth"] for call in calls} == {"Bearer cf-token"}
    assert calls[0]["body"]["response_format"] == {"type": "json_object"}
    context.openai.moderate_texts.assert_not_awaited()


async def test_iincho_cloudflare_failure_skips_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _iincho_context(_cloudflare_config())
    plugin = IinchoPlugin(context)
    async with _cloudflare_server(monkeypatch, {"本地帖子": ""}, status=429):
        await plugin.initialize()
        await plugin.on_timeline_note(_iincho_event())
        with pytest.raises(ExceptionGroup) as exc_info:
            await plugin._process_window()
        await plugin.cleanup()

    assert exc_info.group_contains(RuntimeError, match="status=429")
    context.misskey.create_note.assert_not_awaited()


async def test_iincho_cloudflare_limits_concurrency() -> None:
    plugin = IinchoPlugin(_iincho_context(_cloudflare_config(concurrency=2)))
    active = peak = 0

    class _Response:
        ok = True
        status = 200

        async def __aenter__(self) -> _Response:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            return self

        async def __aexit__(self, *_: Any) -> None:
            nonlocal active
            active -= 1

        async def json(self, **_: Any) -> dict[str, Any]:
            return {"success": True, "result": {"response": {"safe": True}}}

    plugin._cf_session = SimpleNamespace(post=lambda *_, **__: _Response())  # type: ignore[assignment]
    plugin._moderate = plugin._moderate_cloudflare

    assert await plugin._moderate(["a"] * 6) == [frozenset()] * 6
    assert peak == 2
