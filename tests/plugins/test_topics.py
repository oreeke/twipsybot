from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from conftest import plugin_context

from plugins.topics.plugin import _RSS_AI_PREFIX, _TXT_AI_PREFIX, TopicsPlugin
from twipsybot.plugin import AutoPostEvent


@pytest.mark.parametrize("prefix", ("", "  "))
async def test_topics_blank_prompts_use_defaults(prefix: str) -> None:
    generate_text = AsyncMock(return_value="rewritten")
    openai = SimpleNamespace(
        generate_text=generate_text, system_prompt="", max_tokens=1, temperature=0
    )
    plugin = TopicsPlugin(
        plugin_context(
            {"enabled": True, "txt_ai_prefix": prefix, "rss_ai_prefix": prefix},
            openai=openai,
        )
    )
    plugin._get_next_topic = AsyncMock(return_value="science")

    result = await plugin.on_auto_post(AutoPostEvent(datetime.now(UTC)))
    await plugin._rewrite_rss_title_with_ai("t", "l", summary="s")

    assert result == {"prompt": _TXT_AI_PREFIX.format(topic="science")}
    assert generate_text.await_args is not None
    assert generate_text.await_args.args[0] == _RSS_AI_PREFIX.format(
        summary="s", title="t", link="l"
    )


def test_topics_rss_list_accepts_lines_with_comments() -> None:
    plugin = TopicsPlugin(
        plugin_context(
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
        plugin_context(
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
        plugin_context({"enabled": True, "txt_ai_prefix": "Topic:\n{topic}"})
    )
    plugin._get_next_topic = AsyncMock(return_value=topic)

    result = await plugin.on_auto_post(AutoPostEvent(datetime.now(UTC)))

    assert result == {"prompt": f"Topic:\n{topic}"}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"topics.md": "md topic\n", "topics.txt": "txt topic\n"}, ["md topic"]),
        ({"topics.txt": "txt topic\n"}, ["txt topic"]),
    ],
)
async def test_topics_prefers_custom_file_from_prompts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    files: dict[str, str],
    expected: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name, text in files.items():
        (prompts_dir / name).write_text(text, encoding="utf-8")
    plugin = TopicsPlugin(plugin_context({"enabled": True, "txt_ai_prefix": "{topic}"}))

    await plugin._load_topics()

    assert plugin.topics == expected


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
        plugin_context(
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
    plugin = TopicsPlugin(
        plugin_context({"enabled": True, "source": "rss"}, storage=storage)
    )

    assert await plugin.initialize() is True
    storage.set.assert_any_await("rss_recent_keys", "[]")
    storage.set.assert_any_await("rss_last_feed_idx", "0")


async def test_topics_rss_batch_selects_latest_unpublished_entry_per_feed() -> None:
    plugin = TopicsPlugin(
        plugin_context(
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
    plugin = TopicsPlugin(plugin_context({"enabled": True, "source": "rss"}))

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
    plugin = TopicsPlugin(plugin_context({"enabled": True, "source": "rss"}))

    with pytest.raises(ValueError, match="HTTP 503"):
        await plugin._fetch_rss_candidates(
            session, "https://example.com/feed", feed_idx=0
        )


async def test_topics_rss_batch_isolates_failed_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = TopicsPlugin(plugin_context({"enabled": True, "source": "rss"}))
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
        plugin_context(
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
    plugin = TopicsPlugin(plugin_context({"enabled": True, "source": "rss"}))
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
        plugin_context(
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
        plugin_context(
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


def test_topics_parses_boolean_strings() -> None:
    plugin = TopicsPlugin(
        plugin_context(
            {"enabled": "true", "rss_ai": "false", "txt_ai_prefix": "{topic}"}
        )
    )

    assert plugin.settings.rss_ai is False
