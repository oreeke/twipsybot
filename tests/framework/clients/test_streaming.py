from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, call

import aiohttp
import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from support.events import message as chat_message
from support.events import notification
from support.types import MakeBot, WriteConfig

from twipsybot.clients.misskey.events import Incoming
from twipsybot.clients.misskey.models import ChannelType
from twipsybot.clients.misskey.stream import StreamingClient
from twipsybot.shared.errors import (
    WebSocketConnectionError,
)


@pytest.mark.parametrize("outcome", ("success", "error", "cancel"))
async def test_streaming_event_status_tracks_busy_workers(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    client = StreamingClient("https://example.com", "token")
    started = asyncio.Event()
    release = asyncio.Event()

    async def dispatch(channel: str, incoming: Incoming) -> None:
        started.set()
        await release.wait()
        if outcome == "error":
            raise ValueError("dispatch failed")

    monkeypatch.setattr(client.events, "dispatch", dispatch)
    worker = asyncio.create_task(client.events.worker_loop())
    client.events.workers.append(worker)
    try:
        await client.events.queue.put(("main", Incoming("mention", None, "main-id")))
        await asyncio.wait_for(started.wait(), timeout=1)
        status = client.events.status()
        assert status["queue_size"] == 0
        assert status["busy_workers"] == 1
        assert status["workers_alive"] == 1
        assert status["workers_total"] == client.events.worker_count
        assert status["queue_capacity"] == client.events.queue.maxsize
        if outcome == "cancel":
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
        else:
            await client.events.queue.put(None)
            release.set()
            await asyncio.wait_for(worker, timeout=1)
        assert client.events.status()["busy_workers"] == 0
        assert client.events.status()["workers_alive"] == 0
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await client.close()


async def test_streaming_reconnect_backoff_caps_and_resets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    client.running = True
    connect_once = AsyncMock()
    listen = AsyncMock(side_effect=[WebSocketConnectionError()] * 7 + [None])
    reconnect = AsyncMock(side_effect=[WebSocketConnectionError()] * 5 + [None, None])
    monkeypatch.setattr(client, "connect_once", connect_once)
    monkeypatch.setattr(client, "_listen_messages", listen)
    monkeypatch.setattr(client, "_reconnect_with_backoff", reconnect)
    monkeypatch.setattr(
        "twipsybot.clients.misskey.stream.random.uniform",
        lambda lower, upper: upper,
    )

    await client.connect()

    connect_once.assert_awaited_once_with([], raise_on_error=False)
    assert reconnect.await_args_list == [
        call(1.0),
        call(2.0),
        call(4.0),
        call(8.0),
        call(16.0),
        call(30.0),
        call(1.0),
    ]


async def test_streaming_reconnect_uses_jitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    client.running = True
    monkeypatch.setattr(client, "connect_once", AsyncMock())
    monkeypatch.setattr(
        client,
        "_listen_messages",
        AsyncMock(side_effect=[WebSocketConnectionError(), None]),
    )
    reconnect = AsyncMock()
    monkeypatch.setattr(client, "_reconnect_with_backoff", reconnect)
    jitter = Mock(return_value=0.75)
    monkeypatch.setattr("twipsybot.clients.misskey.stream.random.uniform", jitter)

    await client.connect()

    jitter.assert_called_once_with(0.5, 1.0)
    reconnect.assert_awaited_once_with(0.75)


async def test_streaming_without_reconnect_exposes_initial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    error = aiohttp.ClientConnectionError("handshake failed")
    websocket = SimpleNamespace(closed=False, send_json=AsyncMock(), close=AsyncMock())
    handshake = AsyncMock(side_effect=[error, websocket])
    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", handshake)
    monkeypatch.setattr(client, "_listen_messages", AsyncMock())

    try:
        with pytest.raises(WebSocketConnectionError) as exc_info:
            await client.connect(reconnect=False)

        assert exc_info.value.__cause__ is error
        assert not client.running
        assert client.state == "disconnected"
        assert not client.events.workers
        assert client.socket.ws is None

        await client.connect(reconnect=False)

        assert handshake.await_count == 2
        assert client.running
        assert client.state == "connected"
        websocket.send_json.assert_awaited_once()
    finally:
        await client.close()


@pytest.mark.parametrize("reconnect", [False, True])
async def test_streaming_disconnect_stops_listener(reconnect: bool) -> None:
    subscribed = asyncio.Event()

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        websocket = web.WebSocketResponse()
        await websocket.prepare(request)
        async for message in websocket:
            if message.type == WSMsgType.TEXT:
                subscribed.set()
        return websocket

    app = web.Application()
    app.router.add_get("/streaming", websocket_handler)
    async with TestServer(app) as server:
        client = StreamingClient(str(server.make_url("/")), "token")
        listener = asyncio.create_task(client.connect(reconnect=reconnect))
        try:
            await asyncio.wait_for(subscribed.wait(), timeout=2)
            await client.disconnect()
            await asyncio.wait_for(listener, timeout=2)

            assert not client.running
            assert client.state == "disconnected"
            assert client.socket.ws is None
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
            await client.close()


async def test_streaming_without_reconnect_still_listens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    client.running = True
    client.socket.ws = cast(Any, SimpleNamespace(closed=False))
    connect_once = AsyncMock()
    monkeypatch.setattr(client, "connect_once", connect_once)
    listen = AsyncMock()
    monkeypatch.setattr(client, "_listen_messages", listen)

    await client.connect(reconnect=False)

    connect_once.assert_awaited_once_with([], raise_on_error=True)
    listen.assert_awaited_once_with()


async def test_streaming_skips_invalid_json() -> None:
    client = StreamingClient("https://example.com", "token")
    client.running = True
    receive = AsyncMock(
        side_effect=[
            SimpleNamespace(type=WSMsgType.TEXT, data="{"),
            SimpleNamespace(type=WSMsgType.CLOSED, data=None),
        ]
    )
    client.socket.ws = cast(Any, SimpleNamespace(closed=False, receive=receive))

    with pytest.raises(WebSocketConnectionError):
        await client._listen_messages()

    assert receive.await_count == 2


@pytest.mark.parametrize(
    ("first_channel", "first_type", "second_channel", "second_type", "expected_count"),
    [
        ("localTimeline", "note", "antenna", "note", 2),
        ("antenna", "note", "localTimeline", "note", 2),
        ("homeTimeline", "note", "localTimeline", "note", 2),
        ("localTimeline", "note", "localTimeline", "note", 1),
        ("antenna", "note", "antenna", "note", 1),
        ("main", "newChatMessage", "chatUser", "message", 1),
        ("chatUser", "message", "main", "newChatMessage", 1),
        ("main", "mention", "main", "mention", 1),
        ("main", "mention", "main", "reply", 2),
    ],
)
async def test_streaming_event_deduplication_respects_channel_type(
    monkeypatch: pytest.MonkeyPatch,
    first_channel: str,
    first_type: str,
    second_channel: str,
    second_type: str,
    expected_count: int,
) -> None:
    client = StreamingClient("https://example.com", "token")
    enqueue = AsyncMock()
    monkeypatch.setattr(client.events, "enqueue", enqueue)
    for channel_id, channel_name, event_type in (
        ("first-id", first_channel, first_type),
        ("second-id", second_channel, second_type),
    ):
        params = {"antennaId": channel_id} if channel_name == "antenna" else {}
        client.channels[channel_id] = {"name": channel_name, "params": params}
        await client.events.handle_channel_message(
            {
                "id": channel_id,
                "type": event_type,
                "body": {"id": "same-event", "text": "hello"},
            }
        )

    assert enqueue.await_count == expected_count
    assert [entry.args[0] for entry in enqueue.await_args_list] == [
        first_channel,
        second_channel,
    ][:expected_count]


async def test_streaming_does_not_dedupe_event_dropped_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    enqueue = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(client.events, "enqueue", enqueue)
    client.channels = {
        "local-id": {"name": "localTimeline", "params": {}},
        "antenna-id": {"name": "antenna", "params": {"antennaId": "a-1"}},
    }

    for channel_id in client.channels:
        await client.events.handle_channel_message(
            {
                "id": channel_id,
                "type": "note",
                "body": {"id": "same-note", "text": "hello"},
            }
        )

    assert enqueue.await_count == 2


@pytest.mark.parametrize(
    ("event_type", "key", "kind"),
    [
        ("mention", "mention", "mention"),
        ("reply", "mention", "reply"),
        ("unreadNotification", "notification", None),
    ],
)
async def test_main_events_are_decoded_into_typed_events(
    monkeypatch: pytest.MonkeyPatch, event_type: str, key: str, kind: str | None
) -> None:
    client = StreamingClient("https://example.com", "token")
    enqueue = AsyncMock()
    monkeypatch.setattr(client.events, "enqueue", enqueue)
    client.channels["main-id"] = {"name": "main", "params": {}}

    await client.events.handle_channel_message(
        {
            "id": "main-id",
            "type": event_type,
            "body": {"id": "e-1", "type": "reaction", "text": "hi"},
        }
    )

    assert enqueue.await_args is not None
    incoming = enqueue.await_args.args[1]
    assert (incoming.key, incoming.channel_id) == (key, "main-id")
    assert incoming.event.id == "e-1"
    if kind:
        assert incoming.event.kind == kind


@pytest.mark.parametrize("body", [None, "text", {}, {"text": "no id"}])
async def test_events_without_required_fields_are_skipped(
    monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    client = StreamingClient("https://example.com", "token")
    enqueue = AsyncMock()
    monkeypatch.setattr(client.events, "enqueue", enqueue)
    client.channels["main-id"] = {"name": "main", "params": {}}

    for event_type in ("mention", "newChatMessage", "notification"):
        await client.events.handle_channel_message(
            {"id": "main-id", "type": event_type, "body": body}
        )

    enqueue.assert_not_awaited()


async def test_unsupported_and_invalid_events_log_at_different_levels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    warning = Mock()
    monkeypatch.setattr("twipsybot.clients.misskey.events.logger.warning", warning)
    client.channels["main-id"] = {"name": "main", "params": {}}

    await client.events.handle_channel_message(
        {"id": "main-id", "type": "follow", "body": {"id": "u-1"}}
    )
    warning.assert_not_called()

    await client.events.handle_channel_message(
        {"id": "main-id", "type": "mention", "body": {"text": "no id"}}
    )
    warning.assert_called_once()


async def test_chat_user_message_reuses_sender_from_main_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    enqueue = AsyncMock()
    monkeypatch.setattr(client.events, "enqueue", enqueue)
    client.channels = {
        "main-id": {"name": "main", "params": {}},
        "chat-id": {"name": "chatUser", "params": {"otherId": "user-1"}},
    }
    sender = {"id": "user-1", "username": "alice"}

    await client.events.handle_channel_message(
        {
            "id": "main-id",
            "type": "newChatMessage",
            "body": {"id": "m-1", "fromUserId": "user-1", "fromUser": sender},
        }
    )
    await client.events.handle_channel_message(
        {
            "id": "chat-id",
            "type": "message",
            "body": {"id": "m-2", "fromUserId": "user-1", "text": "hi"},
        }
    )

    assert enqueue.await_args is not None
    event = enqueue.await_args.args[1].event
    assert (event.id, event.user.username) == ("m-2", "alice")
    assert event.raw["fromUser"] == sender


async def test_event_dump_records_original_payload_even_when_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token", log_dump_events=True)
    dump = Mock()
    monkeypatch.setattr("twipsybot.clients.misskey.events.maybe_log_event_dump", dump)
    monkeypatch.setattr(client.events, "enqueue", AsyncMock(return_value=True))
    client.channels["local-id"] = {"name": "localTimeline", "params": {}}
    message = {"id": "local-id", "type": "note", "body": {"id": "n-1"}}

    await client.events.handle_channel_message(message)
    await client.events.handle_channel_message(message)
    await client.events.handle_channel_message({"id": "gone", "type": "note"})

    payloads = [call.kwargs["payload"] for call in dump.call_args_list]
    assert payloads == [message, message, {"id": "gone", "type": "note"}]
    assert "streamingChannel" not in message["body"]


@pytest.mark.parametrize("inner_type", ["note", "mention", "follow"])
async def test_streaming_routes_main_notifications_to_notification_handler(
    inner_type: str,
) -> None:
    client = StreamingClient("https://example.com", "token")
    on_note = AsyncMock()
    on_mention = AsyncMock()
    on_notification = AsyncMock()
    client.events.on("note", on_note)
    client.events.on("mention", on_mention)
    client.events.on("notification", on_notification)
    event = notification({"id": "n-1", "type": inner_type, "note": {"id": "note-1"}})

    await client.events.dispatch("main", Incoming("notification", event, "main-id"))

    on_note.assert_not_awaited()
    on_mention.assert_not_awaited()
    on_notification.assert_awaited_once_with(event)


async def test_streaming_reconnect_runs_resubscribe_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    close = AsyncMock()
    sleep = AsyncMock()
    reconnect = AsyncMock()
    monkeypatch.setattr(client.socket, "close", close)
    monkeypatch.setattr(client, "_connect_and_resubscribe", reconnect)
    monkeypatch.setattr("twipsybot.clients.misskey.stream.asyncio.sleep", sleep)

    await client._reconnect_with_backoff(4.0)

    close.assert_awaited_once_with()
    sleep.assert_awaited_once_with(4.0)
    reconnect.assert_awaited_once_with()


async def test_streaming_resubscribes_existing_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    client.channels = {
        "main-id": {"name": "main", "params": {}},
        "antenna-id": {"name": "antenna", "params": {"antennaId": "a-1"}},
    }
    send = AsyncMock()
    monkeypatch.setattr(client.socket, "send_control", send)

    await client._resubscribe_channels()

    assert send.await_args_list == [
        call(
            {
                "type": "connect",
                "body": {
                    "channel": "main",
                    "id": "main-id",
                    "params": {},
                    "pong": True,
                },
            }
        ),
        call(
            {
                "type": "connect",
                "body": {
                    "channel": "antenna",
                    "id": "antenna-id",
                    "params": {"antennaId": "a-1"},
                    "pong": True,
                },
            }
        ),
    ]


async def test_streaming_chat_timer_preserves_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    timer_started = asyncio.Event()
    expire = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def wait_for_expiry(delay: float) -> None:
        assert delay == 120
        timer_started.set()
        await expire.wait()

    monkeypatch.setattr(
        "twipsybot.clients.misskey.events.asyncio.sleep", wait_for_expiry
    )
    try:
        channel_id = await client.events.open_chat_channel(
            ChannelType.CHAT_USER, {"otherId": "user-1"}
        )
        assert channel_id is not None
        tasks.append(client.events.chat_tasks[channel_id])
        await asyncio.wait_for(timer_started.wait(), timeout=2)

        for _ in range(2):
            previous = client.events.chat_tasks[channel_id]
            timer_started.clear()
            client.events.schedule_chat_disconnect(channel_id)
            current = client.events.chat_tasks[channel_id]
            tasks.append(current)
            await asyncio.gather(previous, return_exceptions=True)

            assert previous.cancelled()
            assert client.events.chat_tasks.get(channel_id) is current
            assert channel_id in client.channels
            await asyncio.wait_for(timer_started.wait(), timeout=2)

        expire.set()
        await asyncio.wait_for(tasks[-1], timeout=2)

        assert channel_id not in client.channels
        assert not client.events.chat_tasks
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.close()


@pytest.mark.parametrize(
    ("message", "channel_name", "params"),
    [
        (
            {"id": "message-1", "fromUserId": "user-1"},
            "chatUser",
            {"otherId": "user-1"},
        ),
        (
            {
                "id": "message-2",
                "fromUserId": "user-1",
                "toRoomId": "room-1",
            },
            "chatRoom",
            {"roomId": "room-1"},
        ),
    ],
)
async def test_main_chat_message_uses_matching_misskey_channel(
    monkeypatch: pytest.MonkeyPatch,
    message: dict[str, str],
    channel_name: str,
    params: dict[str, str],
) -> None:
    client = StreamingClient("https://example.com", "token")
    send = AsyncMock()
    monkeypatch.setattr(client, "send_channel_message", send)
    try:
        await client.events.handle_main_chat_message(chat_message(message))

        channel_id = next(
            channel_id
            for channel_id, info in client.channels.items()
            if info == {"name": channel_name, "params": params}
        )
        send.assert_awaited_once_with(channel_id, "read", {"id": message["id"]})
    finally:
        await client.close()


async def test_streaming_channel_waits_for_misskey_confirmation() -> None:
    client = StreamingClient("https://example.com", "token")
    send_json = AsyncMock()
    client.socket.ws = cast(
        Any, SimpleNamespace(closed=False, send_json=send_json, close=AsyncMock())
    )

    task = asyncio.create_task(client.connect_channel("chatRoom", {"roomId": "room-1"}))
    await asyncio.sleep(0)
    assert send_json.await_args is not None
    sent = send_json.await_args.args[0]
    channel_id = sent["body"]["id"]

    assert sent["body"]["pong"] is True
    assert not task.done()

    await client._process_message({"type": "connected", "body": {"id": channel_id}})

    assert await task == channel_id
    assert channel_id in client._confirmed_channel_ids
    await client.close()


async def test_streaming_rejects_more_than_official_channel_limit() -> None:
    client = StreamingClient("https://example.com", "token")
    try:
        for index in range(32):
            await client.connect_channel("antenna", {"antennaId": str(index)})

        with pytest.raises(WebSocketConnectionError, match="at most 32"):
            await client.connect_channel("antenna", {"antennaId": "overflow"})
    finally:
        await client.close()


async def test_main_chat_message_survives_channel_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    handler = AsyncMock()
    client.events.on("message", handler)
    monkeypatch.setattr(
        client,
        "connect_channel",
        AsyncMock(side_effect=WebSocketConnectionError("rejected")),
    )

    await client.events.handle_main_chat_message(
        chat_message({"id": "message-1", "fromUserId": "user-1", "text": "hello"})
    )

    handler.assert_awaited_once()
    assert handler.await_args is not None
    assert handler.await_args.args[0].id == "message-1"


async def test_streaming_flushes_send_buffer_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    client.socket.ws = cast(Any, SimpleNamespace(closed=False))
    messages = [
        {"type": "ch", "body": {"id": "main-id", "type": "first"}},
        {"type": "ch", "body": {"id": "main-id", "type": "second"}},
    ]
    client.socket.buffer.extend(messages)
    send = AsyncMock()
    monkeypatch.setattr(client.socket, "send_control", send)

    await client.socket.flush()

    assert send.await_args_list == [call(message) for message in messages]
    assert not client.socket.buffer


async def test_streaming_warns_once_per_send_buffer_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    warning = Mock()
    monkeypatch.setattr("twipsybot.clients.misskey.bridge.logger.warning", warning)
    maxlen = client.socket.buffer.maxlen
    assert maxlen is not None

    for index in range(maxlen + 2):
        client.socket.buffer_outgoing({"index": index})

    warning.assert_called_once()
    client.socket.ws = cast(Any, SimpleNamespace(closed=False))
    monkeypatch.setattr(client.socket, "send_control", AsyncMock())
    await client.socket.flush()
    client.socket.buffer.extend({"index": index} for index in range(maxlen))
    client.socket.buffer_outgoing({"index": maxlen})

    assert warning.call_count == 2


async def test_streaming_awaits_wrapped_async_handler(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    received = []

    async def handler(data):
        received.append(data)

    bot.streaming.events.handlers["note"] = [lambda data: handler(data)]

    await bot.streaming.events.call_handlers("note", {"id": "note-1"})

    assert received == [{"id": "note-1"}]
