from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from support.factories import plugin_context

from plugins.topics.plugin import TopicsPlugin
from twipsybot.plugin import AutoPostEvent


class MemoryStorage:
    def __init__(self, **values: str) -> None:
        self.values = dict(values)
        self.failing = False

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        if self.failing:
            raise OSError("storage down")
        self.values[key] = value


class Feeds:
    def __init__(self, server: TestServer, pages: dict[str, bytes | int]) -> None:
        self._server = server
        self.pages = pages

    def url(self, name: str) -> str:
        return str(self._server.make_url(f"/feed/{name}"))


def item(
    guid: str,
    title: str,
    link: str | None = None,
    *,
    summary: str = "",
    day: int | None = None,
) -> str:
    permalink = "true" if link else "false"
    parts = [f'<guid isPermaLink="{permalink}">{guid}</guid><title>{title}</title>']
    if link:
        parts.append(f"<link>{link}</link>")
    if summary:
        parts.append(f"<description><![CDATA[{summary}]]></description>")
    if day:
        date = format_datetime(datetime(2026, 9, day, tzinfo=UTC), usegmt=True)
        parts.append(f"<pubDate>{date}</pubDate>")
    return f"<item>{''.join(parts)}</item>"


def rss(*items: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"{''.join(items)}</channel></rss>"
    ).encode()


@pytest_asyncio.fixture
async def feeds() -> AsyncIterator[Feeds]:
    pages: dict[str, bytes | int] = {}

    async def handle(request: web.Request) -> web.Response:
        page = pages[request.match_info["name"]]
        if isinstance(page, int):
            return web.Response(status=page)
        return web.Response(body=page, content_type="application/rss+xml")

    app = web.Application()
    app.router.add_get("/feed/{name}", handle)
    server = TestServer(app)
    await server.start_server()
    try:
        yield Feeds(server, pages)
    finally:
        await server.close()


def make_plugin(
    config: dict[str, Any], storage: MemoryStorage, bot: Any = None
) -> TopicsPlugin:
    return TopicsPlugin(
        plugin_context(
            {"enabled": True, **config}, storage=storage, bot=bot or SimpleNamespace()
        )
    )


async def start(
    config: dict[str, Any], storage: MemoryStorage | None = None, bot: Any = None
) -> TopicsPlugin:
    plugin = make_plugin(config, storage or MemoryStorage(), bot)
    assert await plugin.initialize() is True
    return plugin


async def post(plugin: TopicsPlugin) -> Any:
    return await plugin.on_auto_post(AutoPostEvent(datetime.now(UTC)))


async def post_and_publish(plugin: TopicsPlugin) -> list[str] | None:
    result = await post(plugin)
    if result is None:
        return None
    for content in result["contents"]:
        await plugin.on_auto_post_published(content)
    return result["contents"]


def test_topics_rss_list_accepts_lines_with_comments() -> None:
    plugin = make_plugin(
        {"source": "rss", "rss_list": "# 科技\nhttps://a/rss\n\nhttps://b/rss#x"},
        MemoryStorage(),
    )

    assert plugin.settings.rss_list == ("https://a/rss", "https://b/rss#x")


def test_topics_list_accepts_single_string() -> None:
    plugin = make_plugin({"list": "prompts/topics.md\n"}, MemoryStorage())

    assert plugin.settings.topics == ("prompts/topics.md",)


def test_topics_rejects_list_start_line_below_one() -> None:
    storage = MemoryStorage()

    with pytest.raises(ValueError, match="list_start_line"):
        make_plugin({"list_start_line": 0}, storage)


@pytest.mark.parametrize(
    "config",
    [
        pytest.param({"list": ["prompts/none.md"]}, id="empty-list"),
        pytest.param({"list": ["topic"]}, id="list-storage-failure"),
        pytest.param({"source": "rss"}, id="rss-storage-failure"),
    ],
)
async def test_topics_initialize_fails_when_list_is_empty_or_storage_fails(
    config: dict[str, Any],
) -> None:
    storage = MemoryStorage()
    storage.failing = "list" not in config or "topic" in config["list"]
    plugin = make_plugin(config, storage)

    assert await plugin.initialize() is False


async def test_topics_list_rotates_from_configured_start_line_and_resumes() -> None:
    storage = MemoryStorage()
    config = {
        "list_ai_prefix": "Topic: {topic}",
        "list": ["first", "second", "third"],
        "list_start_line": 5,
    }
    plugin = await start(config, storage)

    assert [await post(plugin) for _ in range(3)] == [
        {"prompt": "Topic: second"},
        {"prompt": "Topic: third"},
        {"prompt": "Topic: first"},
    ]
    assert storage.values["last_used_line"] == "1"

    restarted = await start(config, storage)

    assert await post(restarted) == {"prompt": "Topic: second"}


async def test_topics_list_mixes_inline_topics_and_prompt_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "extra.md").write_text("a\n\n b \n", encoding="utf-8")
    plugin = await start(
        {
            "list_ai_prefix": "{topic}",
            "list": ["#tag", "prompts/extra.md", "prompts/missing.txt", "../x.md"],
        }
    )

    assert [(await post(plugin))["prompt"] for _ in range(4)] == [
        "#tag",
        "a",
        "b",
        "../x.md",
    ]


@pytest.mark.parametrize("prefix", ("", "  "))
async def test_topics_list_blank_prefix_still_prompts_with_topic(prefix: str) -> None:
    plugin = await start({"list": ["science"], "list_ai_prefix": prefix})

    prompt = (await post(plugin))["prompt"]

    assert "science" in prompt
    assert prompt.strip() != "science"


async def test_topics_list_still_returns_topic_when_storage_write_fails() -> None:
    storage = MemoryStorage()
    plugin = await start(
        {"list": ["first", "second"], "list_ai_prefix": "{topic}"}, storage
    )
    storage.failing = True

    assert await post(plugin) == {"prompt": "first"}
    assert await post(plugin) == {"prompt": "first"}


async def test_topics_rss_initialize_keeps_existing_state() -> None:
    storage = MemoryStorage(rss_recent_keys='["old"]')

    await start({"source": "rss"}, storage)

    assert storage.values == {"rss_recent_keys": '["old"]', "rss_last_feed_idx": "0"}


async def test_topics_rss_without_feeds_posts_nothing() -> None:
    plugin = await start({"source": "rss", "rss_list": []})

    assert await post(plugin) is None


async def test_topics_rss_batch_posts_latest_unpublished_entry_per_feed(
    feeds: Feeds,
) -> None:
    feeds.pages |= {
        "a": rss(
            item("a1", "Older A", "https://x/a1", day=1),
            item("a2", "Latest A", "https://x/a2", summary="Summary A", day=2),
        ),
        "down": 503,
        "b": rss(
            item("b1", "Published B", "https://x/b1", day=3),
            item("b2", "Available B", "https://x/b2", day=2),
        ),
    }
    storage = MemoryStorage()
    plugin = await start(
        {
            "source": "rss",
            "rss_post_mode": "batch",
            "rss_list": [feeds.url("a"), feeds.url("down"), feeds.url("b")],
        },
        storage,
    )

    first = await post(plugin)
    assert first == {
        "contents": [
            "📡 Summary A\n\n📎 https://x/a2",
            "📡 Published B\n\n📎 https://x/b1",
        ]
    }
    assert storage.values["rss_recent_keys"] == "[]"

    await plugin.on_auto_post_published("unknown content")
    assert storage.values["rss_recent_keys"] == "[]"

    for content in first["contents"]:
        await plugin.on_auto_post_published(content)

    assert await post_and_publish(plugin) == [
        "📡 Older A\n\n📎 https://x/a1",
        "📡 Available B\n\n📎 https://x/b2",
    ]
    assert await post(plugin) is None


@pytest.mark.parametrize(
    ("summary", "primary"),
    [
        pytest.param("<p>New &amp; improved</p>", "New & improved", id="html"),
        pytest.param("x" * 2000, "x" * 1200, id="long"),
        pytest.param("", "Release", id="missing"),
    ],
)
async def test_topics_rss_renders_summary_and_skips_entries_without_link(
    feeds: Feeds, summary: str, primary: str
) -> None:
    feeds.pages["a"] = rss(
        item("1", "Release", "https://x/release", summary=summary, day=1),
        item("2", "Missing link", day=2),
    )
    plugin = await start(
        {"source": "rss", "rss_post_mode": "batch", "rss_list": [feeds.url("a")]}
    )

    assert await post_and_publish(plugin) == [f"📡 {primary}\n\n📎 https://x/release"]
    assert await post(plugin) is None


@pytest.mark.parametrize("mode", ("batch", "rotate"))
async def test_topics_rss_prefers_first_entry_when_entries_are_undated(
    feeds: Feeds, mode: str
) -> None:
    feeds.pages["a"] = rss(
        item("1", "First", "https://x/1"), item("2", "Second", "https://x/2")
    )
    plugin = await start(
        {"source": "rss", "rss_post_mode": mode, "rss_list": [feeds.url("a")]}
    )

    assert await post(plugin) == {"contents": ["📡 First\n\n📎 https://x/1"]}


async def test_topics_rss_records_key_only_after_publish_succeeds(
    feeds: Feeds,
) -> None:
    feeds.pages["a"] = rss(item("1", "Title", "https://x/1", day=1))
    storage = MemoryStorage()
    plugin = await start(
        {"source": "rss", "rss_post_mode": "batch", "rss_list": [feeds.url("a")]},
        storage,
    )
    content = "📡 Title\n\n📎 https://x/1"

    assert await post(plugin) == {"contents": [content]}
    storage.failing = True
    with pytest.raises(RuntimeError, match="persist published RSS entry"):
        await plugin.on_auto_post_published(content)
    storage.failing = False

    assert await post_and_publish(plugin) == [content]
    assert await post(plugin) is None


async def test_topics_rss_keeps_only_latest_2000_published_keys(feeds: Feeds) -> None:
    feeds.pages["a"] = rss(item("1", "Title", "https://x/1", day=1))
    old_keys = [f"key-{index}" for index in range(2000)]
    storage = MemoryStorage(
        rss_recent_keys="[" + ",".join(f'"{key}"' for key in old_keys) + "]"
    )
    plugin = await start(
        {"source": "rss", "rss_post_mode": "batch", "rss_list": [feeds.url("a")]},
        storage,
    )

    await post_and_publish(plugin)

    saved = storage.values["rss_recent_keys"]
    assert saved.count('"') == 2000 * 2
    assert '"key-0"' not in saved
    assert '"key-1"' in saved


async def test_topics_rss_rotate_advances_through_feeds_after_publish(
    feeds: Feeds,
) -> None:
    feeds.pages |= {
        "a": rss(item("a1", "A", "https://x/a1", day=1)),
        "down": 503,
        "b": rss(item("b1", "B", "https://x/b1", day=1)),
    }
    storage = MemoryStorage()
    plugin = await start(
        {
            "source": "rss",
            "rss_list": [feeds.url("a"), feeds.url("down"), feeds.url("b")],
        },
        storage,
    )

    assert await post(plugin) == {"contents": ["📡 A\n\n📎 https://x/a1"]}
    assert storage.values["rss_last_feed_idx"] == "0"

    await plugin.on_auto_post_published("📡 A\n\n📎 https://x/a1")
    assert storage.values["rss_last_feed_idx"] == "1"

    assert await post_and_publish(plugin) == ["📡 B\n\n📎 https://x/b1"]
    assert storage.values["rss_last_feed_idx"] == "0"

    assert await post(plugin) is None
    assert storage.values["rss_last_feed_idx"] == "1"

    feeds.pages["a"] = rss(item("a2", "A2", "https://x/a2", day=2))
    assert await post(plugin) == {"contents": ["📡 A2\n\n📎 https://x/a2"]}


async def test_topics_rss_rotate_restarts_from_first_feed_when_position_is_out_of_range(
    feeds: Feeds,
) -> None:
    feeds.pages |= {
        "a": rss(item("a1", "A", "https://x/a1")),
        "b": rss(item("b1", "B", "https://x/b1")),
    }
    storage = MemoryStorage(rss_last_feed_idx="9")
    plugin = await start(
        {"source": "rss", "rss_list": [feeds.url("a"), feeds.url("b")]}, storage
    )

    assert await post(plugin) == {"contents": ["📡 A\n\n📎 https://x/a1"]}


async def test_topics_rss_ai_rewrites_with_configured_prefix(feeds: Feeds) -> None:
    feeds.pages["a"] = rss(item("1", "Title", "https://x/1", summary="Sum"))
    generate = AsyncMock(return_value=" rewritten\nsecond line ")
    plugin = await start(
        {
            "source": "rss",
            "rss_ai": True,
            "rss_ai_prefix": "{title}|{summary}|{link}",
            "rss_list": [feeds.url("a")],
        },
        bot=SimpleNamespace(generate_auto_post=generate),
    )

    assert await post(plugin) == {
        "contents": ["📡 rewritten\nsecond line\n\n📎 https://x/1"]
    }
    generate.assert_awaited_once_with("Title|Sum|https://x/1")


@pytest.mark.parametrize(
    ("prefix", "generate"),
    [
        pytest.param(
            "{title}", AsyncMock(side_effect=RuntimeError("down")), id="error"
        ),
        pytest.param("{title}", AsyncMock(return_value="  "), id="blank-reply"),
        pytest.param("{unknown}", AsyncMock(return_value="ignored"), id="bad-prefix"),
    ],
)
async def test_topics_rss_ai_falls_back_to_title_when_rewrite_fails(
    feeds: Feeds, prefix: str, generate: AsyncMock
) -> None:
    feeds.pages["a"] = rss(item("1", "Title", "https://x/1", summary="Sum"))
    plugin = await start(
        {
            "source": "rss",
            "rss_ai": True,
            "rss_ai_prefix": prefix,
            "rss_list": [feeds.url("a")],
        },
        bot=SimpleNamespace(generate_auto_post=generate),
    )

    assert await post(plugin) == {"contents": ["📡 Title\n\n📎 https://x/1"]}


@pytest.mark.parametrize("prefix", ("", "  "))
async def test_topics_rss_ai_blank_prefix_still_prompts_with_article(
    feeds: Feeds, prefix: str
) -> None:
    feeds.pages["a"] = rss(item("1", "Title", "https://x/1", summary="Sum"))
    generate = AsyncMock(return_value="rewritten")
    plugin = await start(
        {
            "source": "rss",
            "rss_ai": True,
            "rss_ai_prefix": prefix,
            "rss_list": [feeds.url("a")],
        },
        bot=SimpleNamespace(generate_auto_post=generate),
    )

    await post(plugin)

    prompt = generate.await_args_list[0].args[0]
    assert "Title" in prompt
    assert "Sum" in prompt
