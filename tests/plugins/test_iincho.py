from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import MappingProxyType, SimpleNamespace
from typing import Any, Self
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from plugins.iincho.plugin import _PROMPT, _SYSTEM_PROMPT, IinchoPlugin, _Sample
from twipsybot.plugin import TimelineNoteEvent, UserRef


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


async def test_iincho_blank_prompts_use_defaults() -> None:
    context = _iincho_context(
        {"prompt": " ", "system_prompt": "", "admin_ids": ["admin-1"]}
    )
    _iincho_flag_all(context)
    context.openai.generate_text.return_value = _iincho_result()

    await IinchoPlugin(context)._generate([_Sample("1", "帖子")])

    prompt, system = context.openai.generate_text.await_args.args
    assert prompt.startswith(f"{_PROMPT}\nDATA=")
    assert system == _SYSTEM_PROMPT


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
    plugin = IinchoPlugin(_iincho_context(_cloudflare_config(cf_concurrency=2)))
    active = peak = 0

    class _Response:
        ok = True
        status = 200

        async def __aenter__(self) -> Self:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            return self

        async def __aexit__(self, *_: object) -> None:
            nonlocal active
            active -= 1

        async def json(self, **_: Any) -> dict[str, Any]:
            return {"success": True, "result": {"response": {"safe": True}}}

    plugin._cf_session = SimpleNamespace(post=lambda *_, **__: _Response())  # type: ignore[assignment]
    plugin._moderate = plugin._moderate_cloudflare

    assert await plugin._moderate(["a"] * 6) == [frozenset()] * 6
    assert peak == 2
