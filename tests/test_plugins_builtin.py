from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

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
        ({"reply": True, "reply_ai": True}, "reply_ai_prompt"),
        ({"quote": True, "quote_ai": True}, "quote_ai_prompt"),
    ],
)
def test_radar_requires_enabled_ai_prompt(config: dict[str, Any], field: str) -> None:
    context = _context(config)

    with pytest.raises(ValueError, match=rf"{field} must not be empty"):
        RadarPlugin(context)


@pytest.mark.parametrize(
    ("config", "field"),
    [
        ({"source": "txt"}, "txt_ai_prefix"),
        ({"source": "rss", "rss_ai": True}, "rss_ai_prefix"),
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


async def test_topics_initializes_rss_storage_defaults() -> None:
    storage = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    plugin = TopicsPlugin(_context({"enabled": True, "source": "rss"}, storage=storage))

    assert await plugin.initialize() is True
    storage.set.assert_any_await("rss_recent_keys", "[]")
    storage.set.assert_any_await("rss_last_feed_idx", "0")


async def test_topics_rss_is_recorded_only_after_publish() -> None:
    storage = SimpleNamespace()
    plugin = TopicsPlugin(
        _context(
            {"enabled": True, "source": "rss", "rss_list": ["feed"]},
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
            {"enabled": True, "max_images": 1},
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


async def test_radar_reacts_through_public_misskey_service() -> None:
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

    create_reaction.assert_awaited_once_with("note-1", "heart")

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

    assert plugin._window.total == 3
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
    context = _iincho_context({"sample_size": 2, "min_notes": 2})
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
    assert created["text"].startswith("📊 Iincho 时间线观察\n\n🕒")
    assert "本地时间线观察" not in created["text"]
    assert "概览" not in created["text"]
    assert "热点" not in created["text"]
    assert "氛围" not in created["text"]
    assert "🚨 违规审查：" in created["text"]
    assert "💢 骚扰攻击 1" in created["text"]
    assert "⚖️ 违法活动 1" in created["text"]


async def test_iincho_limits_serialized_input() -> None:
    context = _iincho_context({"min_notes": 1, "max_input_chars": 1000})
    context.openai.generate_text.return_value = _iincho_result()
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
    context = _iincho_context({"sample_size": 2, "min_notes": 1})
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
        assert message.startswith("🚨 Iincho 近期小报告\n\n🔥 热点")
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


async def test_iincho_sends_trends_to_admin_without_violations() -> None:
    context = _iincho_context({"min_notes": 1, "admin_ids": ["admin-1"]})
    context.openai.generate_text.return_value = _iincho_result()
    plugin = IinchoPlugin(context)
    await plugin.on_timeline_note(_iincho_event())

    await plugin._process_window()

    message = context.misskey.send_message.await_args.args[1]
    assert "🔥 热点\n• 新功能体验\n• 部署问题" in message
    assert "🚨 违规审查：\n✅ 未发现明显违规" in message


async def test_iincho_discards_invalid_ai_result_without_retry() -> None:
    context = _iincho_context({"min_notes": 1})
    context.openai.generate_text.return_value = "not json"
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
