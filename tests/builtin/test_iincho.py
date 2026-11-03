from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from plugins.iincho.plugin import IinchoPlugin
from twipsybot.plugin import TimelineNoteEvent, UserRef

_INTERVAL = 300.0
_CF_ID = "0123456789abcdef0123456789abcdef"


def _context(config: dict[str, Any], admins: tuple[str, ...]) -> Any:
    return SimpleNamespace(
        name="iincho",
        config={
            "enabled": True,
            "interval": "5m",
            "prompt": "总结帖子数组的整体趋势。",
            "system_prompt": "你是社区趋势分析员。",
            **config,
        },
        storage=SimpleNamespace(),
        openai=SimpleNamespace(
            generate_text=AsyncMock(return_value=_trends()),
            moderate_texts=AsyncMock(
                side_effect=lambda texts: [frozenset() for _ in texts]
            ),
        ),
        misskey=SimpleNamespace(
            instance_url="https://misskey.example",
            create_note=AsyncMock(return_value={}),
            send_message=AsyncMock(return_value={}),
        ),
        bot=SimpleNamespace(user_id="bot-id", admins=admins),
    )


def _note(
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


def _trends() -> str:
    return json.dumps({"trends": ["新功能体验", "部署问题"]}, ensure_ascii=False)


def _flag(context: Any, *flags: set[str]) -> None:
    context.openai.moderate_texts.side_effect = None
    context.openai.moderate_texts.return_value = [frozenset(f) for f in flags]


def _flag_all(context: Any, *flags: str) -> None:
    context.openai.moderate_texts.side_effect = lambda texts: [
        frozenset(flags or ("hate",)) for _ in texts
    ]


class Iincho:
    def __init__(self, context: Any, parked: asyncio.Queue[None]) -> None:
        self.context = context
        self.plugin = IinchoPlugin(context)
        self.parked = parked
        self.gate = asyncio.Queue[None]()
        self.started = False

    @property
    def created(self) -> dict[str, Any]:
        return self.context.misskey.create_note.await_args.kwargs

    async def start(self) -> None:
        if not self.started:
            self.started = True
            await self.plugin.on_startup()
            async with asyncio.timeout(5):
                await self.parked.get()

    async def note(self, *args: Any, **kwargs: Any) -> None:
        await self.plugin.on_timeline_note(_note(*args, **kwargs))

    async def cycle(self) -> None:
        await self.start()
        self.gate.put_nowait(None)
        async with asyncio.timeout(5):
            await self.parked.get()


Make = Callable[..., Iincho]


@pytest.fixture
async def make_iincho(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Make]:
    real_sleep = asyncio.sleep
    parked = asyncio.Queue[None]()
    built: list[Iincho] = []

    async def sleep(delay: float, *args: Any) -> Any:
        if delay != _INTERVAL:
            return await real_sleep(delay, *args)
        parked.put_nowait(None)
        await built[0].gate.get()

    monkeypatch.setattr(asyncio, "sleep", sleep)

    def make(
        config: dict[str, Any] | None = None, *, admins: tuple[str, ...] = ()
    ) -> Iincho:
        built.append(Iincho(_context(config or {}, admins), parked))
        return built[0]

    yield make

    for iincho in built:
        await iincho.plugin.cleanup()


@pytest.mark.parametrize(
    "config",
    (
        {"interval": "4m"},
        {"interval": "nope"},
        {"interval": 0},
        {"sample_size": 10.5},
        {"min_notes": 10, "sample_size": 5},
        {"moderation": {"provider": "other"}},
        {"moderation": {"provider": "cloudflare"}},
        {"moderation": {"provider": "cloudflare", "cf_account_id": _CF_ID}},
        {
            "moderation": {
                "provider": "cloudflare",
                "cf_account_id": "../x",
                "cf_api_token": "t",
            }
        },
    ),
)
def test_iincho_rejects_invalid_config(config: dict[str, Any]) -> None:
    context = _context(config, ())

    with pytest.raises(ValueError):
        IinchoPlugin(context)


@pytest.mark.parametrize(
    "event",
    (
        _note(channel="globalTimeline"),
        _note(user_id="bot-id"),
        _note(text=""),
    ),
    ids=("other-channel", "own-note", "empty-text"),
)
async def test_iincho_ignores_ineligible_notes(
    make_iincho: Make, event: TimelineNoteEvent
) -> None:
    iincho = make_iincho({"min_notes": 1})

    await iincho.plugin.on_timeline_note(event)
    await iincho.cycle()

    iincho.context.openai.moderate_texts.assert_not_awaited()
    iincho.context.misskey.create_note.assert_not_awaited()


async def test_iincho_prepends_content_warning_to_sampled_text(
    make_iincho: Make,
) -> None:
    iincho = make_iincho({"min_notes": 1})

    await iincho.note("正文", cw="预警")
    await iincho.cycle()

    iincho.context.openai.moderate_texts.assert_awaited_once_with(["预警\n正文"])


async def test_iincho_bounds_samples_and_reports_eligible_count(
    make_iincho: Make,
) -> None:
    iincho = make_iincho({"sample_size": 2, "min_notes": 1})

    for index in range(20):
        await iincho.note(str(index), event_id=str(index), user_id=str(index))
    await iincho.cycle()

    (texts,) = iincho.context.openai.moderate_texts.await_args.args
    assert len(texts) == 2
    assert "覆盖 20 · AI 均匀抽样 2" in iincho.created["text"]


async def test_iincho_skips_window_below_min_notes(make_iincho: Make) -> None:
    iincho = make_iincho({"min_notes": 2})

    await iincho.note()
    await iincho.cycle()

    iincho.context.openai.moderate_texts.assert_not_awaited()
    iincho.context.misskey.create_note.assert_not_awaited()


async def test_iincho_publishes_anonymized_summary_with_risk_counts(
    make_iincho: Make,
) -> None:
    iincho = make_iincho(
        {"sample_size": 2, "min_notes": 2, "local_only": False}, admins=("admin-1",)
    )
    _flag(iincho.context, {"harassment", "harassment/threatening"}, {"illicit"})
    await iincho.note("第一条 https://example.com @alice", event_id="1")
    await iincho.note("第二条", event_id="2")

    await iincho.cycle()

    openai = iincho.context.openai
    prompt = openai.generate_text.await_args.args[0]
    assert "帖子数组" in prompt
    assert json.loads(prompt.partition("DATA=")[2]) == [
        "第一条 [link] [account]",
        "第二条",
    ]
    assert openai.generate_text.await_args.args[1] == "你是社区趋势分析员。"
    assert openai.generate_text.await_args.kwargs["json_output"] is True
    openai.moderate_texts.assert_awaited_once_with(
        ["第一条 [link] [account]", "第二条"]
    )
    text = iincho.created["text"]
    assert iincho.created["visibility"] == "public"
    assert iincho.created["local_only"] is False
    assert " 至 " in text
    assert "omni-moderation-latest" in text
    assert "💢 骚扰攻击 1" in text
    assert "⚖️ 违法活动 1" in text


async def test_iincho_falls_back_to_default_prompts_when_blank(
    make_iincho: Make,
) -> None:
    iincho = make_iincho(
        {"min_notes": 1, "prompt": " ", "system_prompt": ""}, admins=("admin-1",)
    )
    _flag_all(iincho.context)

    await iincho.note()
    await iincho.cycle()

    prompt, system = iincho.context.openai.generate_text.await_args.args
    assert prompt.partition("\nDATA=")[0].strip()
    assert system.strip()


async def test_iincho_maps_openai_categories_to_risk_labels(make_iincho: Make) -> None:
    iincho = make_iincho({"min_notes": 1})
    _flag(iincho.context, {"sexual", "sexual/minors", "violence/graphic"})

    await iincho.note()
    await iincho.cycle()

    text = iincho.created["text"]
    assert "🔞 色情内容 1" in text
    assert "🚸 涉未成年 1" in text
    assert "⚔️ 暴力威胁 1" in text


async def test_iincho_limits_serialized_input(make_iincho: Make) -> None:
    iincho = make_iincho({"min_notes": 1, "max_input_chars": 1000}, admins=("admin",))
    _flag_all(iincho.context)
    await iincho.note("\\" * 2000, event_id="1")
    await iincho.note("第二条", event_id="2")
    await iincho.note("第三条", event_id="3")

    await iincho.cycle()

    prompt = iincho.context.openai.generate_text.await_args.args[0]
    payload = prompt.partition("DATA=")[2]
    assert len(payload) <= 1000
    assert json.loads(payload)[0]
    iincho.context.openai.moderate_texts.assert_awaited_once_with(json.loads(payload))
    assert "覆盖 3 · AI 均匀抽样 1" in iincho.created["text"]


async def test_iincho_preserves_special_characters_in_payload_and_alerts(
    make_iincho: Make,
) -> None:
    iincho = make_iincho({"min_notes": 1}, admins=("admin-1",))
    _flag_all(iincho.context)

    await iincho.note('line 1\n"line 2"\\', event_id='id"\\')
    await iincho.cycle()

    prompt = iincho.context.openai.generate_text.await_args.args[0]
    assert json.loads(prompt.partition("DATA=")[2]) == ['line 1\n"line 2"\\']
    assert 'id"\\' in iincho.context.misskey.send_message.await_args.args[1]


async def test_iincho_keeps_notes_arriving_during_generation(make_iincho: Make) -> None:
    iincho = make_iincho({"sample_size": 2, "min_notes": 1}, admins=("admin-1",))
    _flag_all(iincho.context)

    async def generate(*args: Any, **kwargs: Any) -> str:
        await iincho.note("新窗口", event_id="new")
        return _trends()

    iincho.context.openai.generate_text.side_effect = generate
    await iincho.note("旧窗口")
    await iincho.cycle()
    await iincho.cycle()

    calls = iincho.context.openai.moderate_texts.await_args_list
    assert [call.args[0] for call in calls] == [["旧窗口"], ["新窗口"]]
    assert iincho.context.misskey.create_note.await_count == 2


async def test_iincho_notifies_every_admin_with_trends_and_flagged_note_ids(
    make_iincho: Make,
) -> None:
    iincho = make_iincho({"min_notes": 1}, admins=("admin-1", "admin-2"))
    _flag(iincho.context, {"harassment", "harassment/threatening", "illicit"})

    await iincho.note()
    await iincho.cycle()

    calls = iincho.context.misskey.send_message.await_args_list
    assert [call.args[0] for call in calls] == ["admin-1", "admin-2"]
    for call in calls:
        assert "$[fg.color=e74c3c 💢 骚扰攻击 · ⚖️ 违法活动] note-1" in call.args[1]
        assert "> 新功能体验" in call.args[1]


async def test_iincho_admin_failure_does_not_block_others_or_summary(
    make_iincho: Make,
) -> None:
    iincho = make_iincho({"min_notes": 1}, admins=("admin-1", "admin-2"))
    _flag(iincho.context, {"harassment"})
    iincho.context.misskey.send_message.side_effect = [RuntimeError("down"), {}]

    await iincho.note()
    await iincho.cycle()

    calls = iincho.context.misskey.send_message.await_args_list
    assert [call.args[0] for call in calls] == ["admin-1", "admin-2"]
    iincho.context.misskey.create_note.assert_awaited_once()


@pytest.mark.parametrize(
    ("admins", "flagged"),
    ((("admin-1",), False), ((), True)),
    ids=("clean", "no-admin"),
)
async def test_iincho_skips_trends_and_alerts_unless_admin_and_violation(
    make_iincho: Make, admins: tuple[str, ...], flagged: bool
) -> None:
    iincho = make_iincho({"min_notes": 1}, admins=admins)
    if flagged:
        _flag_all(iincho.context)

    await iincho.note()
    await iincho.cycle()

    iincho.context.openai.generate_text.assert_not_awaited()
    iincho.context.misskey.send_message.assert_not_awaited()
    iincho.context.misskey.create_note.assert_awaited_once()


@pytest.mark.parametrize(
    "reply",
    (
        "not json",
        '{"other": []}',
        '{"trends": []}',
        '{"trends": ["a", "b", "c", "d", "e", "f"]}',
        '{"trends": [" "]}',
        '{"trends": [1]}',
    ),
)
async def test_iincho_discards_window_when_ai_result_invalid(
    make_iincho: Make, reply: str
) -> None:
    iincho = make_iincho({"min_notes": 1}, admins=("admin-1",))
    _flag_all(iincho.context)
    iincho.context.openai.generate_text.return_value = reply

    await iincho.note()
    await iincho.cycle()
    await iincho.cycle()

    iincho.context.openai.generate_text.assert_awaited_once()
    iincho.context.misskey.create_note.assert_not_awaited()
    iincho.context.misskey.send_message.assert_not_awaited()


async def test_iincho_discards_window_when_moderation_count_mismatches(
    make_iincho: Make,
) -> None:
    iincho = make_iincho({"min_notes": 1})
    _flag(iincho.context)

    await iincho.note()
    await iincho.cycle()

    iincho.context.misskey.create_note.assert_not_awaited()


async def test_iincho_stops_cycles_after_cleanup(make_iincho: Make) -> None:
    iincho = make_iincho({"min_notes": 1})
    await iincho.start()
    await iincho.note()

    await iincho.plugin.cleanup()
    iincho.gate.put_nowait(None)
    await asyncio.sleep(0.01)

    iincho.context.misskey.create_note.assert_not_awaited()


@dataclass
class Cloudflare:
    replies: dict[str, Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    status: int = 200
    active: int = 0
    peak: int = 0


@pytest.fixture
async def cloudflare(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Cloudflare]:
    state = Cloudflare()

    async def run(request: web.Request) -> web.Response:
        body = await request.json()
        state.calls.append({"auth": request.headers.get("Authorization"), "body": body})
        state.active += 1
        state.peak = max(state.peak, state.active)
        try:
            await asyncio.sleep(0.01)
        finally:
            state.active -= 1
        reply = state.replies.get(body["messages"][0]["content"], {"safe": True})
        return web.json_response(
            {
                "success": state.status == 200,
                "errors": [],
                "result": {"response": reply},
            },
            status=state.status,
        )

    app = web.Application()
    app.router.add_post(f"/accounts/{_CF_ID}/ai/run/@cf/meta/llama-guard-3-8b", run)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(
        "plugins.iincho.plugin._CF_API", str(server.make_url("")).rstrip("/")
    )
    try:
        yield state
    finally:
        await server.close()


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


async def test_iincho_cloudflare_moderation_maps_categories(
    cloudflare: Cloudflare, make_iincho: Make
) -> None:
    cloudflare.replies = {
        "bad": {"safe": False, "categories": ["S1", "S4", "S6", "S10"]},
        "text": "unsafe\nS12,S7",
    }
    iincho = make_iincho(_cloudflare_config())
    await iincho.plugin.initialize()

    for name in ("safe", "bad", "text"):
        await iincho.note(name, event_id=name)
    await iincho.cycle()

    text = iincho.created["text"]
    for label in (
        "⚔️ 暴力威胁 1",
        "🚸 涉未成年 1",
        "🚫 仇恨歧视 1",
        "🔞 色情内容 1",
        "🕵️ 诽谤隐私 1",
    ):
        assert label in text
    assert "llama-guard-3-8b" in text
    assert all("cf-token" in call["auth"] for call in cloudflare.calls)
    assert cloudflare.calls[0]["body"]["response_format"] == {"type": "json_object"}
    iincho.context.openai.moderate_texts.assert_not_awaited()


async def test_iincho_cloudflare_failure_skips_cycle(
    cloudflare: Cloudflare, make_iincho: Make
) -> None:
    cloudflare.status = 429
    iincho = make_iincho(_cloudflare_config())
    await iincho.plugin.initialize()

    await iincho.note()
    await iincho.cycle()

    iincho.context.misskey.create_note.assert_not_awaited()


async def test_iincho_cloudflare_limits_concurrent_requests(
    cloudflare: Cloudflare, make_iincho: Make
) -> None:
    iincho = make_iincho(_cloudflare_config(cf_concurrency=2))
    await iincho.plugin.initialize()

    for index in range(6):
        await iincho.note(str(index), event_id=str(index))
    await iincho.cycle()

    assert len(cloudflare.calls) == 6
    assert cloudflare.peak == 2
