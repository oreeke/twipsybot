from __future__ import annotations

import asyncio
import base64
import os
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, call

import pytest
from aiohttp import WSMsgType, web
from aiohttp.multipart import BodyPartReader
from aiohttp.test_utils import TestServer
from conftest import (
    MakeBot,
    WriteConfig,
)
from httpx2 import Request, Response
from openai import APIStatusError

from twipsybot import MisskeyBot
from twipsybot.app import main as app_main
from twipsybot.clients.misskey.api import MisskeyAPI
from twipsybot.clients.misskey.payloads import (
    extract_chat_text,
    extract_note_text,
    extract_user_id,
)
from twipsybot.clients.misskey.socket import _redact_access_token
from twipsybot.clients.misskey.streaming import StreamingClient
from twipsybot.clients.openai.api import OpenAIAPI
from twipsybot.clients.openai.requests import (
    make_chat_completions_request,
    make_responses_request,
)
from twipsybot.shared.constants import API_MAX_RETRIES
from twipsybot.shared.exceptions import (
    APIBadRequestError,
    APIConnectionError,
    APIPermissionError,
    APIRateLimitError,
    WebSocketConnectionError,
)


@pytest.mark.parametrize("outcome", ("success", "error", "cancel"))
async def test_streaming_event_status_tracks_busy_workers(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    client = StreamingClient("https://example.com", "token")
    started = asyncio.Event()
    release = asyncio.Event()

    async def dispatch(channel: str, event: dict[str, Any]) -> None:
        started.set()
        await release.wait()
        if outcome == "error":
            raise ValueError("dispatch failed")

    monkeypatch.setattr(client, "_dispatch_event", dispatch)
    worker = asyncio.create_task(client._worker_loop())
    client._workers.append(worker)
    try:
        await client._event_queue.put(("main", {"type": "mention"}))
        await asyncio.wait_for(started.wait(), timeout=1)
        status = client.get_event_status()
        assert status["queue_size"] == 0
        assert status["busy_workers"] == 1
        assert status["workers_alive"] == 1
        assert status["workers_total"] == client._worker_count
        assert status["queue_capacity"] == client._event_queue.maxsize
        if outcome == "cancel":
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
        else:
            await client._event_queue.put(None)
            release.set()
            await asyncio.wait_for(worker, timeout=1)
        assert client.get_event_status()["busy_workers"] == 0
        assert client.get_event_status()["workers_alive"] == 0
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await client.close()


class _BadRequest:
    def __init__(
        self,
        message: str,
        code: str | None = None,
        status_code: int | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


def test_misskey_access_token_is_redacted() -> None:
    text = 'https://example.com/streaming?i=secret&x=1 {"i":"token"}'

    assert _redact_access_token(text) == (
        'https://example.com/streaming?i=***&x=1 {"i":"***"}'
    )


def test_misskey_payload_extractors_use_current_fields() -> None:
    assert extract_chat_text({"content": "legacy", "body": "legacy"}) == ""
    assert extract_note_text({"body": "legacy"}) == ""
    assert extract_user_id({"userId": "legacy"}) is None


def test_misskey_error_format_uses_current_fields() -> None:
    assert (
        MisskeyAPI._format_error_text(
            '{"error":{"code":"INVALID_PARAM","message":"Invalid parameter","id":"id"}}'
        )
        == "INVALID_PARAM: Invalid parameter"
    )
    legacy = '{"error":{"id":"legacy","info":"Legacy error","kind":"legacy"}}'
    assert MisskeyAPI._format_error_text(legacy) == legacy


async def test_room_timeline_uses_room_id_without_fallback() -> None:
    request = AsyncMock(side_effect=APIBadRequestError("invalid roomId"))
    api = object.__new__(MisskeyAPI)
    api.make_read_request = request

    with pytest.raises(APIBadRequestError):
        await api.get_room_messages("room-1", limit=20, since_id="message-1")

    request.assert_awaited_once_with(
        "chat/messages/room-timeline",
        {"roomId": "room-1", "limit": 20, "sinceId": "message-1"},
    )


async def test_misskey_requests_use_bearer_without_duplicate_token() -> None:
    requests: list[tuple[str, str | None, dict[str, Any]]] = []

    async def api_handler(request: web.Request) -> web.Response:
        endpoint = request.match_info["endpoint"]
        if endpoint == "drive/files/create":
            payload = {}
            async for field in await request.multipart():
                assert isinstance(field, BodyPartReader)
                assert field.name is not None
                payload[field.name] = (
                    await field.read() if field.name == "file" else await field.text()
                )
        else:
            payload = await request.json()
        requests.append((endpoint, request.headers.get("Authorization"), payload))
        return web.json_response({})

    async def file_handler(request: web.Request) -> web.Response:
        requests.append(("external", request.headers.get("Authorization"), {}))
        return web.Response(body=b"image")

    app = web.Application()
    app.router.add_post("/api/{endpoint:.*}", api_handler)
    app.router.add_get("/file", file_handler)
    server = TestServer(app)
    await server.start_server()
    api = MisskeyAPI(str(server.make_url("/")).rstrip("/"), "token")
    try:
        await api.make_request("i", {"i": "override", "probe": True})
        await api.drive.upload_bytes(b"image", name="image.png")
        await api.drive.fetch_bytes(str(server.make_url("/file")))
    finally:
        await api.close()
        await server.close()

    assert requests == [
        ("i", "Bearer token", {"i": "override", "probe": True}),
        (
            "drive/files/create",
            "Bearer token",
            {"name": "image.png", "file": b"image"},
        ),
        ("external", None, {}),
    ]


async def test_misskey_api_retries_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = AsyncMock(side_effect=[APIConnectionError(), {"ok": True}])
    sleep = AsyncMock()
    api = object.__new__(MisskeyAPI)
    api._make_request_once = request
    monkeypatch.setattr("twipsybot.clients.misskey.api.asyncio.sleep", sleep)

    assert await api.make_read_request("notes/show") == {"ok": True}
    assert request.await_count == 2
    sleep.assert_awaited_once()


async def test_misskey_api_respects_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = AsyncMock(side_effect=[APIRateLimitError(retry_after=12.0), {"ok": True}])
    sleep = AsyncMock()
    api = object.__new__(MisskeyAPI)
    api._make_request_once = request
    monkeypatch.setattr("twipsybot.clients.misskey.api.asyncio.sleep", sleep)

    assert await api.make_read_request("notes/show") == {"ok": True}
    sleep.assert_awaited_once_with(12.0)


async def test_misskey_preserves_permission_error_details() -> None:
    response = SimpleNamespace(
        status=403,
        headers={},
        text=AsyncMock(
            return_value=(
                '{"error":{"code":"PERMISSION_DENIED","message":"Denied",'
                '"id":"error-id","kind":"permission"}}'
            )
        ),
    )
    api = object.__new__(MisskeyAPI)

    with pytest.raises(APIPermissionError) as exc_info:
        await api._process_response(response, "notes/create")

    assert exc_info.value.status == 403
    assert exc_info.value.code == "PERMISSION_DENIED"
    assert exc_info.value.error_id == "error-id"
    assert exc_info.value.kind == "permission"


async def test_misskey_api_stops_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = AsyncMock(side_effect=APIConnectionError())
    sleep = AsyncMock()
    api = object.__new__(MisskeyAPI)
    api._make_request_once = request
    monkeypatch.setattr("twipsybot.clients.misskey.api.asyncio.sleep", sleep)

    with pytest.raises(APIConnectionError):
        await api.make_read_request("notes/show")

    assert request.await_count == API_MAX_RETRIES + 1
    assert sleep.await_count == API_MAX_RETRIES


async def test_misskey_api_does_not_retry_writes() -> None:
    request = AsyncMock(side_effect=APIConnectionError())
    api = object.__new__(MisskeyAPI)
    api._make_request_once = request

    with pytest.raises(APIConnectionError):
        await api.make_request("notes/create")

    request.assert_awaited_once_with("notes/create", None)


async def test_create_note_leaves_reply_validation_to_misskey() -> None:
    request = AsyncMock(side_effect=APIBadRequestError("NO_SUCH_REPLY_TARGET"))
    api = object.__new__(MisskeyAPI)
    api.make_request = request

    with pytest.raises(APIBadRequestError):
        await api.create_note("reply", reply_id="missing-note")

    request.assert_awaited_once_with(
        "notes/create",
        {"text": "reply", "visibility": "public", "replyId": "missing-note"},
    )


async def test_create_note_preserves_known_specified_reply_visibility() -> None:
    request = AsyncMock(return_value={})
    api = object.__new__(MisskeyAPI)
    api.make_request = request

    await api.create_note("reply", visibility="specified", reply_id="specified-note")

    request.assert_awaited_once_with(
        "notes/create",
        {
            "text": "reply",
            "visibility": "specified",
            "replyId": "specified-note",
        },
    )


@pytest.mark.parametrize(
    ("method", "identifier_key"),
    [("send_message", "toUserId"), ("send_room_message", "toRoomId")],
)
async def test_chat_messages_truncate_text_over_misskey_limit(
    method: str, identifier_key: str
) -> None:
    request = AsyncMock(return_value={})
    api = object.__new__(MisskeyAPI)
    api.make_request = request
    await getattr(api, method)("target", "x" * 2001)

    endpoint = (
        "chat/messages/create-to-user"
        if method == "send_message"
        else "chat/messages/create-to-room"
    )
    request.assert_awaited_once_with(
        endpoint, {identifier_key: "target", "text": "x" * 1999 + "…"}
    )


@pytest.mark.parametrize("method", ("create_note", "create_renote"))
async def test_notes_truncate_text_over_misskey_limit(method: str) -> None:
    request = AsyncMock(return_value={})
    api = object.__new__(MisskeyAPI)
    api.make_request = request
    if method == "create_note":
        await api.create_note("x" * 3001)
        expected = {"text": "x" * 2999 + "…", "visibility": "public"}
    else:
        await api.create_renote("note", text="x" * 3001)
        expected = {"renoteId": "note", "text": "x" * 2999 + "…"}

    request.assert_awaited_once_with("notes/create", expected)


async def test_user_note_cleanup_requests_use_safe_scope() -> None:
    read = AsyncMock(return_value=[{"id": "note-1"}])
    write = AsyncMock(return_value={})
    api = object.__new__(MisskeyAPI)
    api.make_read_request = read
    api.make_request = write

    assert await api.get_user_notes(
        "bot-id", until_date=123456789, until_id="note-2"
    ) == [{"id": "note-1"}]
    await api.delete_note("note-1")

    read.assert_awaited_once_with(
        "users/notes",
        {
            "userId": "bot-id",
            "withReplies": False,
            "withRenotes": False,
            "withChannelNotes": False,
            "untilDate": 123456789,
            "limit": 100,
            "allowPartial": False,
            "withFiles": False,
            "untilId": "note-2",
        },
    )
    write.assert_awaited_once_with("notes/delete", {"noteId": "note-1"})


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
        "twipsybot.clients.misskey.streaming.random.uniform",
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
    monkeypatch.setattr("twipsybot.clients.misskey.streaming.random.uniform", jitter)

    await client.connect()

    jitter.assert_called_once_with(0.5, 1.0)
    reconnect.assert_awaited_once_with(0.75)


async def test_streaming_without_reconnect_exposes_initial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    error = WebSocketConnectionError("handshake failed")
    websocket = SimpleNamespace(closed=False, send_json=AsyncMock(), close=AsyncMock())
    handshake = AsyncMock(side_effect=[error, websocket])
    monkeypatch.setattr(client.transport, "ws_connect", handshake)
    monkeypatch.setattr(client, "_listen_messages", AsyncMock())

    try:
        with pytest.raises(WebSocketConnectionError) as exc_info:
            await client.connect(reconnect=False)

        assert exc_info.value is error
        assert not client.running
        assert client.state == "disconnected"
        assert not client._workers
        assert client.ws_connection is None

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
            assert client.ws_connection is None
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
            await client.close()


async def test_streaming_without_reconnect_still_listens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    client.running = True
    client.ws_connection = cast(Any, SimpleNamespace(closed=False))
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
    client.ws_connection = cast(Any, SimpleNamespace(closed=False, receive=receive))

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
    monkeypatch.setattr(client, "_enqueue_event", enqueue)
    for channel_id, channel_name, event_type in (
        ("first-id", first_channel, first_type),
        ("second-id", second_channel, second_type),
    ):
        params = {"antennaId": channel_id} if channel_name == "antenna" else {}
        client.channels[channel_id] = {"name": channel_name, "params": params}
        await client._handle_channel_message(
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
    monkeypatch.setattr(client, "_enqueue_event", enqueue)
    client.channels = {
        "local-id": {"name": "localTimeline", "params": {}},
        "antenna-id": {"name": "antenna", "params": {"antennaId": "a-1"}},
    }

    for channel_id in client.channels:
        await client._handle_channel_message(
            {
                "id": channel_id,
                "type": "note",
                "body": {"id": "same-note", "text": "hello"},
            }
        )

    assert enqueue.await_count == 2


async def test_streaming_reconnect_runs_resubscribe_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    close = AsyncMock()
    sleep = AsyncMock()
    reconnect = AsyncMock()
    monkeypatch.setattr(client, "_close_websocket", close)
    monkeypatch.setattr(client, "_connect_and_resubscribe", reconnect)
    monkeypatch.setattr("twipsybot.clients.misskey.socket.asyncio.sleep", sleep)

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
    monkeypatch.setattr(client, "_send_control", send)

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
        channel_id = await client._ensure_chat_user_stream({"fromUserId": "user-1"})
        assert channel_id is not None
        tasks.append(client._chat_channel_tasks[channel_id])
        await asyncio.wait_for(timer_started.wait(), timeout=2)

        for _ in range(2):
            previous = client._chat_channel_tasks[channel_id]
            timer_started.clear()
            client._refresh_chat_channel_timer(channel_id)
            current = client._chat_channel_tasks[channel_id]
            tasks.append(current)
            await asyncio.gather(previous, return_exceptions=True)

            assert previous.cancelled()
            assert client._chat_channel_tasks.get(channel_id) is current
            assert client._chat_user_channel_ids.get("user-1") == channel_id
            assert client._chat_channel_other_ids.get(channel_id) == "user-1"
            assert channel_id in client.channels
            await asyncio.wait_for(timer_started.wait(), timeout=2)

        expire.set()
        await asyncio.wait_for(tasks[-1], timeout=2)

        assert channel_id not in client.channels
        assert not client._chat_channel_tasks
        assert not client._chat_user_channel_ids
        assert not client._chat_channel_other_ids
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
    monkeypatch.setattr(client, "_send_channel_message", send)
    try:
        await client._handle_main_new_chat_message(message)

        channel_id = next(
            channel_id
            for channel_id, info in client.channels.items()
            if info == {"name": channel_name, "params": params}
        )
        send.assert_awaited_once_with(channel_id, "read", {"id": message["id"]})
    finally:
        await client.close()


async def test_streaming_channel_waits_for_misskey_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    send_json = AsyncMock()
    client.ws_connection = cast(
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
    client.on_message(handler)
    monkeypatch.setattr(
        client,
        "connect_channel",
        AsyncMock(side_effect=WebSocketConnectionError("rejected")),
    )

    await client._handle_main_new_chat_message(
        {"id": "message-1", "fromUserId": "user-1", "text": "hello"}
    )

    handler.assert_awaited_once()
    assert handler.await_args is not None
    assert handler.await_args.args[0]["id"] == "message-1"


async def test_streaming_flushes_send_buffer_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    client.ws_connection = cast(Any, SimpleNamespace(closed=False))
    messages = [
        {"type": "ch", "body": {"id": "main-id", "type": "first"}},
        {"type": "ch", "body": {"id": "main-id", "type": "second"}},
    ]
    client._send_buffer.extend(messages)
    send = AsyncMock()
    monkeypatch.setattr(client, "_send_control", send)

    await client._flush_send_buffer()

    assert send.await_args_list == [call(message) for message in messages]
    assert not client._send_buffer


async def test_streaming_warns_once_per_send_buffer_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StreamingClient("https://example.com", "token")
    warning = Mock()
    monkeypatch.setattr("twipsybot.clients.misskey.socket.logger.warning", warning)
    maxlen = client._send_buffer.maxlen
    assert maxlen is not None

    for index in range(maxlen + 2):
        client._buffer_outgoing({"index": index})

    warning.assert_called_once()
    client.ws_connection = cast(Any, SimpleNamespace(closed=False))
    monkeypatch.setattr(client, "_send_control", AsyncMock())
    await client._flush_send_buffer()
    client._send_buffer.extend({"index": index} for index in range(maxlen))
    client._buffer_outgoing({"index": maxlen})

    assert warning.call_count == 2


async def test_openai_generates_png_bytes() -> None:
    api = OpenAIAPI("test", image_model="gpt-image-1")
    generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode())]
        )
    )
    api.client = cast(Any, SimpleNamespace(images=SimpleNamespace(generate=generate)))

    assert await api.generate_image("一只猫") == b"png"
    generate.assert_awaited_once_with(
        model="gpt-image-1",
        prompt="一只猫",
    )


async def test_openai_forwards_explicit_image_options() -> None:
    api = OpenAIAPI(
        "test",
        image_model="gpt-image-2",
        image_size="2048x2048",
        image_quality="medium",
    )
    generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=None, url="https://example.com")]
        )
    )
    api.client = cast(Any, SimpleNamespace(images=SimpleNamespace(generate=generate)))

    assert await api.generate_image("一只猫") == "https://example.com"
    generate.assert_awaited_once_with(
        model="gpt-image-2",
        prompt="一只猫",
        size="2048x2048",
        quality="medium",
    )


def test_openai_loads_token_encoding_lazily_into_data_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    encoding = SimpleNamespace(encode_ordinary=lambda text: list(text))
    load_encoding = Mock(return_value=encoding)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    monkeypatch.setattr(
        "twipsybot.clients.openai.api.tiktoken.encoding_for_model", load_encoding
    )

    api = OpenAIAPI("test")

    load_encoding.assert_not_called()
    assert api.trim_chat_history([{"role": "user", "content": "hello"}], 9)
    load_encoding.assert_called_once_with("gpt-5-mini")
    assert os.environ["TIKTOKEN_CACHE_DIR"] == str(
        (tmp_path / "data" / "tiktoken").resolve()
    )


def test_openai_token_encoding_failure_uses_character_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_encoding = Mock(side_effect=OSError("offline"))
    monkeypatch.setattr(
        "twipsybot.clients.openai.api.tiktoken.encoding_for_model", load_encoding
    )
    api = OpenAIAPI("test")
    history = [
        {"role": "user", "content": "123456"},
        {"role": "assistant", "content": "ok"},
    ]

    assert api.trim_chat_history(history, 10) == [history[-1]]
    assert api.trim_chat_history(history, 10) == [history[-1]]
    load_encoding.assert_called_once_with("gpt-5-mini")


async def test_streaming_startup_failure_closes_initialized_services(
    monkeypatch: pytest.MonkeyPatch, write_config: WriteConfig
) -> None:
    config = write_config()
    bot = MisskeyBot(config)
    monkeypatch.setattr(bot, "_initialize_services", AsyncMock())
    monkeypatch.setattr(bot, "_setup_scheduler", Mock())
    monkeypatch.setattr(
        bot.connect,
        "setup_streaming",
        AsyncMock(side_effect=RuntimeError("streaming failed")),
    )
    monkeypatch.setattr(bot.plugin_manager, "shutdown_plugins", AsyncMock())
    monkeypatch.setattr(bot.plugin_manager, "cleanup_plugins", AsyncMock())
    monkeypatch.setattr(bot.runtime, "cleanup_tasks", AsyncMock())
    monkeypatch.setattr(bot.streaming, "close", AsyncMock())
    monkeypatch.setattr(bot.misskey, "close", AsyncMock())
    monkeypatch.setattr(bot.openai, "close", AsyncMock())
    monkeypatch.setattr(bot.db, "close", AsyncMock())
    bot.scheduler = SimpleNamespace(running=True, shutdown=Mock())
    monkeypatch.setattr(app_main, "Config", lambda: config)
    monkeypatch.setattr(app_main, "MisskeyBot", lambda _: bot)
    runner = app_main.BotRunner()

    with pytest.raises(RuntimeError, match="streaming failed"):
        await runner.run()

    assert bot.runtime.running is False
    bot.plugin_manager.shutdown_plugins.assert_awaited_once()
    bot.plugin_manager.cleanup_plugins.assert_awaited_once()
    bot.scheduler.shutdown.assert_called_once_with(wait=False)
    bot.runtime.cleanup_tasks.assert_awaited_once()
    bot.streaming.close.assert_awaited_once()
    bot.misskey.close.assert_awaited_once()
    bot.openai.close.assert_awaited_once()
    bot.db.close.assert_awaited_once()


@pytest.mark.parametrize(
    "message",
    (
        "Model does not support the Responses API",
        "This model doesn't support the Responses API",
        "Responses API is not supported",
    ),
)
def test_responses_unavailable_recognizes_model_capability(message: str) -> None:
    assert OpenAIAPI._is_responses_unavailable(_BadRequest(message))


def test_responses_unavailable_rejects_parameter_error() -> None:
    assert not OpenAIAPI._is_responses_unavailable(
        _BadRequest("Invalid max_output_tokens")
    )


async def test_responses_request_enables_json_output() -> None:
    create = AsyncMock(return_value={})
    client = SimpleNamespace(responses=SimpleNamespace(create=create))

    await make_responses_request(
        client=cast(Any, client),
        semaphore=asyncio.Semaphore(1),
        model="test",
        messages=[],
        max_tokens=None,
        temperature=None,
        json_output=True,
    )

    call = create.await_args
    assert call is not None
    assert call.kwargs["text"] == {"format": {"type": "json_object"}}


async def test_chat_completions_request_enables_json_output() -> None:
    create = AsyncMock(return_value={})
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    await make_chat_completions_request(
        client=cast(Any, client),
        semaphore=asyncio.Semaphore(1),
        model="test",
        api_base="https://api.deepseek.com",
        messages=[],
        max_tokens=None,
        temperature=None,
        json_output=True,
    )

    call = create.await_args
    assert call is not None
    assert call.kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("status_code", (404, 405, 501))
def test_responses_unavailable_recognizes_http_status(status_code: int) -> None:
    assert OpenAIAPI._is_responses_unavailable(
        _BadRequest("unsupported", status_code=status_code)
    )


async def test_http_405_falls_back_to_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import twipsybot.clients.openai.api as module

    error = APIStatusError(
        "method not allowed",
        response=Response(
            405, request=Request("POST", "https://example.com/responses")
        ),
        body={},
    )
    responses = AsyncMock(side_effect=error)
    fallback = AsyncMock(return_value="fallback")
    monkeypatch.setattr(module, "make_responses_request", responses)
    api = OpenAIAPI("test", api_mode="auto")
    monkeypatch.setattr(api, "_call_api_common", fallback)

    result = await api.generate_text("hello")

    assert result == "fallback"
    fallback.assert_awaited_once()
    await api.close()


async def test_openai_client_uses_sdk_retries() -> None:
    api = OpenAIAPI("test", api_mode="auto")

    assert api.client.max_retries == 2
    await api.close()


async def test_openai_moderates_texts_in_batch() -> None:
    categories = SimpleNamespace(
        to_dict=lambda: {"harassment": True, "hate": False, "illicit": True}
    )
    create = AsyncMock(
        return_value=SimpleNamespace(results=[SimpleNamespace(categories=categories)])
    )
    api = OpenAIAPI("test")
    api.client = cast(Any, SimpleNamespace(moderations=SimpleNamespace(create=create)))

    result = await api.moderate_texts(["text"])

    assert result == [frozenset({"harassment", "illicit"})]
    create.assert_awaited_once_with(model="omni-moderation-latest", input=["text"])


async def test_streaming_awaits_wrapped_async_handler(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())
    received = []

    async def handler(data):
        received.append(data)

    bot.streaming.event_handlers["note"] = [lambda data: handler(data)]

    await bot.streaming._call_handlers("note", {"id": "note-1"})

    assert received == [{"id": "note-1"}]
