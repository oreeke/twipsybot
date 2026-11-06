from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from support.events import message as chat_message
from support.events import notification

from twipsybot.clients.misskey.events import Incoming
from twipsybot.clients.misskey.models import ChannelType
from twipsybot.clients.misskey.stream import StreamingClient
from twipsybot.shared.errors import WebSocketConnectionError


@asynccontextmanager
async def listening(
    client: StreamingClient, **kwargs: Any
) -> AsyncIterator[asyncio.Task[None]]:
    listener = asyncio.create_task(client.connect(**kwargs))
    try:
        yield listener
    finally:
        await client.disconnect()
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)


@pytest.fixture
def jitter(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    bounds: list[tuple[float, float]] = []

    def uniform(low: float, high: float) -> float:
        bounds.append((low, high))
        return 0

    monkeypatch.setattr("twipsybot.clients.misskey.stream.random.uniform", uniform)
    return bounds


@pytest.fixture
def routed(client: StreamingClient, monkeypatch: pytest.MonkeyPatch) -> Mock:
    enqueue = Mock()
    monkeypatch.setattr(client.events, "enqueue", enqueue)
    client.channels["main-id"] = {"name": "main", "params": {}}
    return enqueue


@pytest.mark.parametrize("outcome", ("success", "error", "cancel"))
async def test_event_status_tracks_busy_workers(
    client: StreamingClient, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
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
    await client.events.queue.put(("main", Incoming("mention", None, "main-id")))
    await asyncio.wait_for(started.wait(), timeout=1)
    status = client.events.status()
    assert status["queue_size"] == 0
    assert status["busy_workers"] == 1
    assert status["workers_alive"] == 1
    if outcome == "cancel":
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
    else:
        await client.events.queue.put(None)
        release.set()
        await asyncio.wait_for(worker, timeout=1)
    status = client.events.status()
    assert (status["busy_workers"], status["workers_alive"]) == (0, 0)


async def test_reconnect_backoff_doubles_to_cap_and_resets_after_success(
    ws_server: Any,
    live_client: StreamingClient,
    jitter: list[tuple[float, float]],
) -> None:
    ws_server.refusals = 7

    async with listening(live_client):
        await ws_server.until(lambda: ws_server.connections == 1)
        await ws_server.drop()
        await ws_server.until(lambda: ws_server.connections == 2)

    assert jitter == [
        (0.5, 1.0),
        (1.0, 2.0),
        (2.0, 4.0),
        (4.0, 8.0),
        (8.0, 16.0),
        (15.0, 30.0),
        (15.0, 30.0),
        (0.5, 1.0),
    ]


async def test_reconnect_resubscribes_existing_channels(
    ws_server: Any,
    live_client: StreamingClient,
    jitter: list[tuple[float, float]],
) -> None:
    channels = ["main", ("antenna", {"antennaId": "a-1"})]

    async with listening(live_client, channels=channels):
        await ws_server.until(lambda: len(ws_server.sent("connect")) == 2)
        await ws_server.drop()
        await ws_server.until(lambda: len(ws_server.sent("connect")) == 4)

    first, second = ws_server.messages[:2], ws_server.messages[2:4]
    assert first == second
    assert [m["body"]["channel"] for m in first] == ["main", "antenna"]
    assert first[1]["body"]["params"] == {"antennaId": "a-1"}
    assert all(m["body"]["pong"] is True for m in first)


async def test_connect_without_reconnect_exposes_initial_failure(
    ws_server: Any, live_client: StreamingClient
) -> None:
    ws_server.refusals = 1

    with pytest.raises(WebSocketConnectionError) as exc_info:
        await live_client.connect(reconnect=False)

    assert isinstance(exc_info.value.__cause__, aiohttp.ClientError)
    assert not live_client.running
    assert live_client.state == "disconnected"
    assert not live_client.events.workers
    assert live_client.socket.ws is None

    async with listening(live_client, reconnect=False):
        await ws_server.until(lambda: live_client.state == "connected")
        assert live_client.running


@pytest.mark.parametrize("reconnect", [False, True])
async def test_disconnect_stops_listener(
    ws_server: Any, live_client: StreamingClient, reconnect: bool
) -> None:
    async with listening(live_client, reconnect=reconnect) as listener:
        await ws_server.until(lambda: bool(ws_server.sent("connect")))
        await live_client.disconnect()
        await asyncio.wait_for(listener, timeout=2)

    assert not live_client.running
    assert live_client.state == "disconnected"
    assert live_client.socket.ws is None
    assert ws_server.sent("disconnect")


async def test_invalid_json_is_skipped_and_listening_continues(
    ws_server: Any, live_client: StreamingClient
) -> None:
    received: list[Any] = []
    live_client.events.on("mention", received.append)

    async with listening(live_client):
        await ws_server.until(lambda: bool(ws_server.sent("connect")))
        channel_id = ws_server.sent("connect")[0]["body"]["id"]
        await ws_server.send("{")
        await ws_server.send(
            {
                "type": "channel",
                "body": {
                    "id": channel_id,
                    "type": "mention",
                    "body": {"id": "n-1", "text": "hi"},
                },
            }
        )
        await ws_server.until(lambda: bool(received))


async def test_channel_waits_for_misskey_confirmation(
    ws_server: Any, live_client: StreamingClient
) -> None:
    ws_server.confirm = False

    async with listening(live_client):
        await ws_server.until(lambda: live_client.state == "connected")
        task = asyncio.create_task(
            live_client.connect_channel("chatRoom", {"roomId": "room-1"})
        )
        await ws_server.until(lambda: len(ws_server.sent("connect")) == 2)
        sent = ws_server.sent("connect")[1]["body"]
        await asyncio.sleep(0.05)

        assert not task.done()

        await ws_server.send({"type": "connected", "body": {"id": sent["id"]}})

        assert await asyncio.wait_for(task, timeout=2) == sent["id"]


async def test_channel_limit_is_enforced(client: StreamingClient) -> None:
    for index in range(32):
        await client.connect_channel("antenna", {"antennaId": str(index)})

    with pytest.raises(WebSocketConnectionError, match="at most 32"):
        await client.connect_channel("antenna", {"antennaId": "overflow"})


async def test_offline_messages_are_dropped_not_replayed_on_connect(
    ws_server: Any, live_client: StreamingClient
) -> None:
    await live_client.send_channel_message("main-id", "stale", {})

    await live_client.connect_once()
    await live_client.send_channel_message("main-id", "fresh", {})

    await ws_server.until(lambda: len(ws_server.sent("ch")) == 1)
    assert [m["body"]["type"] for m in ws_server.sent("ch")] == ["fresh"]


def test_full_queue_drops_event_without_blocking(
    client: StreamingClient,
) -> None:
    client.events.queue = asyncio.Queue(maxsize=1)
    event = notification({"id": "n-1", "type": "follow"})
    incoming = Incoming("notification", event, "main-id")

    assert client.events.enqueue("main", incoming) is True
    assert client.events.enqueue("main", incoming) is False


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
def test_event_deduplication_respects_channel_type(
    client: StreamingClient,
    routed: Mock,
    first_channel: str,
    first_type: str,
    second_channel: str,
    second_type: str,
    expected_count: int,
) -> None:
    for channel_id, channel_name, event_type in (
        ("first-id", first_channel, first_type),
        ("second-id", second_channel, second_type),
    ):
        params = {"antennaId": channel_id} if channel_name == "antenna" else {}
        client.channels[channel_id] = {"name": channel_name, "params": params}
        client.events.handle_channel_message(
            {
                "id": channel_id,
                "type": event_type,
                "body": {"id": "same-event", "text": "hello"},
            }
        )

    assert [entry.args[0] for entry in routed.call_args_list] == [
        first_channel,
        second_channel,
    ][:expected_count]


def test_event_dropped_before_enqueue_is_not_marked_processed(
    client: StreamingClient, routed: Mock
) -> None:
    routed.side_effect = [False, True]
    client.channels["local-id"] = {"name": "localTimeline", "params": {}}
    message = {"id": "local-id", "type": "note", "body": {"id": "same-note"}}

    client.events.handle_channel_message(message)
    client.events.handle_channel_message(message)

    assert routed.call_count == 2


@pytest.mark.parametrize(
    ("event_type", "key", "kind"),
    [
        ("mention", "mention", "mention"),
        ("reply", "mention", "reply"),
        ("unreadNotification", "notification", None),
    ],
)
def test_main_events_are_decoded_into_typed_events(
    client: StreamingClient,
    routed: Mock,
    event_type: str,
    key: str,
    kind: str | None,
) -> None:
    client.events.handle_channel_message(
        {
            "id": "main-id",
            "type": event_type,
            "body": {"id": "e-1", "type": "reaction", "text": "hi"},
        }
    )

    incoming = routed.call_args_list[-1].args[1]
    assert (incoming.key, incoming.channel_id) == (key, "main-id")
    assert incoming.event.id == "e-1"
    if kind:
        assert incoming.event.kind == kind


@pytest.mark.parametrize("body", [None, "text", {}, {"text": "no id"}])
def test_events_without_required_fields_are_skipped(
    client: StreamingClient, routed: Mock, body: Any
) -> None:
    for event_type in ("mention", "newChatMessage", "notification"):
        client.events.handle_channel_message(
            {"id": "main-id", "type": event_type, "body": body}
        )

    routed.assert_not_called()


def test_unsupported_and_invalid_events_log_at_different_levels(
    client: StreamingClient, routed: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    warning = Mock()
    monkeypatch.setattr("twipsybot.clients.misskey.events.logger.warning", warning)

    client.events.handle_channel_message(
        {"id": "main-id", "type": "follow", "body": {"id": "u-1"}}
    )
    warning.assert_not_called()

    client.events.handle_channel_message(
        {"id": "main-id", "type": "mention", "body": {"text": "no id"}}
    )
    warning.assert_called_once()


def test_chat_user_message_reuses_sender_from_main_notice(
    client: StreamingClient, routed: Mock
) -> None:
    client.channels["chat-id"] = {"name": "chatUser", "params": {"otherId": "user-1"}}
    sender = {"id": "user-1", "username": "alice"}

    client.events.handle_channel_message(
        {
            "id": "main-id",
            "type": "newChatMessage",
            "body": {"id": "m-1", "fromUserId": "user-1", "fromUser": sender},
        }
    )
    client.events.handle_channel_message(
        {
            "id": "chat-id",
            "type": "message",
            "body": {"id": "m-2", "fromUserId": "user-1", "text": "hi"},
        }
    )

    event = routed.call_args_list[-1].args[1].event
    assert (event.id, event.user.username) == ("m-2", "alice")
    assert event.raw["fromUser"] == sender


def test_event_dump_records_original_payload_even_when_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token", log_dump_events=True)
    dump = Mock()
    monkeypatch.setattr("twipsybot.clients.misskey.events.maybe_log_event_dump", dump)
    monkeypatch.setattr(client.events, "enqueue", Mock(return_value=True))
    client.channels["local-id"] = {"name": "localTimeline", "params": {}}
    message = {"id": "local-id", "type": "note", "body": {"id": "n-1"}}
    unknown = {"id": "gone", "type": "note"}

    client.events.handle_channel_message(message)
    client.events.handle_channel_message(message)
    client.events.handle_channel_message(unknown)

    payloads = [call.kwargs["payload"] for call in dump.call_args_list]
    assert payloads == [message, message, unknown]
    assert "streamingChannel" not in message["body"]


@pytest.mark.parametrize("inner_type", ["note", "mention", "follow"])
async def test_main_notifications_reach_only_notification_handler(
    client: StreamingClient, inner_type: str
) -> None:
    handlers = {name: AsyncMock() for name in ("note", "mention", "notification")}
    for name, handler in handlers.items():
        client.events.on(name, handler)
    event = notification({"id": "n-1", "type": inner_type, "note": {"id": "note-1"}})

    await client.events.dispatch("main", Incoming("notification", event, "main-id"))

    handlers["notification"].assert_awaited_once_with(event)
    handlers["note"].assert_not_awaited()
    handlers["mention"].assert_not_awaited()


async def test_chat_idle_timer_replacement_keeps_channel_until_expiry(
    client: StreamingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    timer_started = asyncio.Event()
    expire = asyncio.Event()

    async def wait_for_expiry(delay: float) -> None:
        timer_started.set()
        await expire.wait()

    monkeypatch.setattr(
        "twipsybot.clients.misskey.events.asyncio.sleep", wait_for_expiry
    )
    channel_id = await client.events.open_chat_channel(
        ChannelType.CHAT_USER, {"otherId": "user-1"}
    )
    assert channel_id is not None
    await asyncio.wait_for(timer_started.wait(), timeout=2)

    for _ in range(2):
        previous = client.events.chat_tasks[channel_id]
        timer_started.clear()
        client.events.schedule_chat_disconnect(channel_id)
        current = client.events.chat_tasks[channel_id]
        await asyncio.gather(previous, return_exceptions=True)

        assert previous.cancelled()
        assert client.events.chat_tasks.get(channel_id) is current
        assert channel_id in client.channels
        await asyncio.wait_for(timer_started.wait(), timeout=2)

    expire.set()
    await asyncio.wait_for(current, timeout=2)

    assert channel_id not in client.channels
    assert not client.events.chat_tasks


@pytest.mark.parametrize(
    ("message", "channel_name", "params"),
    [
        (
            {"id": "message-1", "fromUserId": "user-1"},
            "chatUser",
            {"otherId": "user-1"},
        ),
        (
            {"id": "message-2", "fromUserId": "user-1", "toRoomId": "room-1"},
            "chatRoom",
            {"roomId": "room-1"},
        ),
    ],
)
async def test_main_chat_message_is_marked_read_on_matching_channel(
    client: StreamingClient,
    monkeypatch: pytest.MonkeyPatch,
    message: dict[str, str],
    channel_name: str,
    params: dict[str, str],
) -> None:
    send = AsyncMock()
    monkeypatch.setattr(client, "send_channel_message", send)

    await client.events.handle_main_chat_message(chat_message(message))

    channel_id = next(
        channel_id
        for channel_id, info in client.channels.items()
        if info == {"name": channel_name, "params": params}
    )
    send.assert_awaited_once_with(channel_id, "read", {"id": message["id"]})


async def test_main_chat_message_survives_channel_rejection(
    client: StreamingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert handler.await_args_list[-1].args[0].id == "message-1"


async def test_handler_returning_awaitable_is_awaited(
    client: StreamingClient,
) -> None:
    received = []

    async def handler(data: Any) -> None:
        received.append(data)

    client.events.on("note", lambda data: handler(data))

    await client.events.call_handlers("note", {"id": "note-1"})

    assert received == [{"id": "note-1"}]
