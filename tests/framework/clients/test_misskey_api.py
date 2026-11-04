from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from loguru import logger

from twipsybot.clients.misskey.api import MisskeyAPI
from twipsybot.clients.misskey.bridge import SharedSession
from twipsybot.clients.misskey.stream import StreamingClient
from twipsybot.shared.consts import API_MAX_RETRIES
from twipsybot.shared.errors import (
    APIBadRequestError,
    APIConnectionError,
    APIFileTooLargeError,
    APINotFoundError,
    APIPermissionError,
    AuthenticationError,
    WebSocketConnectionError,
)

PERMISSION_DENIED = (
    '{"error":{"code":"PERMISSION_DENIED","message":"Denied",'
    '"id":"error-id","kind":"permission"}}'
)
INVALID_PARAM = (
    '{"error":{"code":"INVALID_PARAM","message":"Invalid parameter","id":"id"}}'
)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(delay: float) -> None:
        delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr("twipsybot.clients.misskey.api.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "twipsybot.clients.misskey.api.random.uniform", lambda low, high: 0
    )
    return delays


async def test_websocket_failure_log_hides_access_token(ws_server: Any) -> None:
    ws_server.refusals = 1
    client = StreamingClient(ws_server.url, "secret-token")
    logs: list[str] = []
    sink = logger.add(logs.append, level="DEBUG")
    try:
        with pytest.raises(WebSocketConnectionError):
            await client.connect(reconnect=False)
    finally:
        logger.remove(sink)
        await client.close()

    assert any("i=***" in line for line in logs)
    assert not any("secret-token" in line for line in logs)


async def test_requests_use_bearer_without_duplicate_token(
    api: MisskeyAPI, misskey_server: Any
) -> None:
    await api.make_request("i", {"i": "override", "probe": True})
    await api.drive.upload_bytes(b"image", name="image.png")
    await api.drive.fetch_bytes(f"{misskey_server.url}/file")

    assert misskey_server.requests == [
        ("i", "Bearer token", {"i": "override", "probe": True}),
        ("drive/files/create", "Bearer token", {"name": "image.png", "file": b"image"}),
        ("external", None, {}),
    ]


@pytest.mark.parametrize(
    ("status", "body", "error", "code", "message"),
    [
        (400, INVALID_PARAM, APIBadRequestError, "INVALID_PARAM", "Invalid parameter"),
        (403, PERMISSION_DENIED, APIPermissionError, "PERMISSION_DENIED", "Denied"),
        (401, "nope", AuthenticationError, None, "nope"),
        (404, "missing", APINotFoundError, None, "missing"),
        (413, "big", APIFileTooLargeError, None, "big"),
        (418, "teapot", APIBadRequestError, None, "teapot"),
        (503, "down", APIConnectionError, None, "down"),
        (200, "not json", APIConnectionError, None, ""),
    ],
)
async def test_error_responses_map_to_typed_errors(
    api: MisskeyAPI,
    misskey_server: Any,
    status: int,
    body: str,
    error: type[Exception],
    code: str | None,
    message: str,
) -> None:
    misskey_server.reply(status, body)

    with pytest.raises(error) as exc_info:
        await api.make_request("notes/create")

    assert exc_info.value.code == code  # type: ignore[attr-defined]
    assert message in str(exc_info.value)


async def test_read_requests_retry_connection_errors(
    api: MisskeyAPI, misskey_server: Any, sleeps: list[float]
) -> None:
    misskey_server.reply(503)
    misskey_server.reply(200, {"ok": True})

    assert await api.make_read_request("notes/show") == {"ok": True}
    assert len(misskey_server.requests) == 2
    assert len(sleeps) == 1


async def test_read_requests_respect_retry_after(
    api: MisskeyAPI, misskey_server: Any, sleeps: list[float]
) -> None:
    misskey_server.reply(429, "slow down", **{"Retry-After": "12"})
    misskey_server.reply(200, {"ok": True})

    assert await api.make_read_request("notes/show") == {"ok": True}
    assert sleeps == [12.0]


async def test_read_requests_stop_after_max_retries(
    api: MisskeyAPI, misskey_server: Any, sleeps: list[float]
) -> None:
    misskey_server.default = (503, "down", {})

    with pytest.raises(APIConnectionError):
        await api.make_read_request("notes/show")

    assert len(misskey_server.requests) == API_MAX_RETRIES + 1
    assert len(sleeps) == API_MAX_RETRIES


async def test_write_requests_are_not_retried(
    api: MisskeyAPI, misskey_server: Any
) -> None:
    misskey_server.reply(503)

    with pytest.raises(APIConnectionError):
        await api.make_request("notes/create")

    assert len(misskey_server.requests) == 1


async def test_request_timeouts_raise_connection_errors(
    api: MisskeyAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SimpleNamespace(post=Mock(side_effect=TimeoutError))
    monkeypatch.setattr(SharedSession, "session", property(lambda _: session))

    with pytest.raises(APIConnectionError):
        await api.make_request("notes/create")


@pytest.mark.parametrize("retry", [False, True])
async def test_call_api_retries_only_when_requested(
    api: MisskeyAPI,
    misskey_server: Any,
    sleeps: list[float],
    retry: bool,
) -> None:
    params = {"query": "misskey"}
    misskey_server.reply(503)

    call = api.call_api("/notes/search", params, retry=retry)
    if retry:
        await call
    else:
        with pytest.raises(APIConnectionError):
            await call

    assert [r.endpoint for r in misskey_server.requests] == ["notes/search"] * (
        2 if retry else 1
    )
    assert misskey_server.requests[0].payload == {"query": "misskey"}
    assert params == {"query": "misskey"}


@pytest.mark.parametrize(
    ("endpoint", "params", "match"),
    [
        *(
            (endpoint, None, "invalid endpoint")
            for endpoint in (
                "",
                "/",
                "../i",
                "notes//show",
                "notes/",
                "notes?x=1",
                "a#b",
                "a b",
                "a\\b",
                "http://x/api/i",
                "notes/%2e%2e",
                "笔记/show",
            )
        ),
        ("notes/show", {"i": "override"}, "'i'"),
    ],
)
async def test_call_api_rejects_unsafe_input(
    api: MisskeyAPI,
    misskey_server: Any,
    endpoint: str,
    params: dict[str, str] | None,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        await api.call_api(endpoint, params)

    assert not misskey_server.requests


@pytest.mark.parametrize(
    ("call", "endpoint", "expected"),
    [
        (
            lambda api: api.create_note("x" * 3001),
            "notes/create",
            {"text": "x" * 2999 + "…", "visibility": "public"},
        ),
        (
            lambda api: api.create_renote("note", text="x" * 3001),
            "notes/create",
            {"renoteId": "note", "text": "x" * 2999 + "…"},
        ),
        (
            lambda api: api.send_message("target", "x" * 2001),
            "chat/messages/create-to-user",
            {"toUserId": "target", "text": "x" * 1999 + "…"},
        ),
        (
            lambda api: api.send_room_message("target", "x" * 2001),
            "chat/messages/create-to-room",
            {"toRoomId": "target", "text": "x" * 1999 + "…"},
        ),
        (
            lambda api: api.create_note(
                "reply", visibility="specified", reply_id="specified-note"
            ),
            "notes/create",
            {"text": "reply", "visibility": "specified", "replyId": "specified-note"},
        ),
        (
            lambda api: api.create_note("text", visibility=""),
            "notes/create",
            {"text": "text", "visibility": ""},
        ),
        (
            lambda api: api.create_note("", file_ids=["f1"]),
            "notes/create",
            {"visibility": "public", "fileIds": ["f1"]},
        ),
        (
            lambda api: api.send_message("u1", "", "f1"),
            "chat/messages/create-to-user",
            {"toUserId": "u1", "fileId": "f1"},
        ),
    ],
    ids=[
        "note-truncated",
        "renote-truncated",
        "chat-truncated",
        "room-truncated",
        "specified-visibility-kept",
        "empty-visibility-kept",
        "note-file-only-omits-text",
        "chat-file-only-omits-text",
    ],
)
async def test_note_and_chat_requests_build_expected_payload(
    api: MisskeyAPI,
    misskey_server: Any,
    call: Callable[[MisskeyAPI], Awaitable[Any]],
    endpoint: str,
    expected: dict[str, Any],
) -> None:
    await call(api)

    assert misskey_server.requests == [(endpoint, "Bearer token", expected)]


async def test_user_note_cleanup_requests_use_safe_scope(
    api: MisskeyAPI, misskey_server: Any
) -> None:
    misskey_server.reply(200, [{"id": "note-1"}])

    notes = await api.get_user_notes("bot-id", until_date=123456789, until_id="note-2")

    assert notes == [{"id": "note-1"}]
    assert misskey_server.requests[0].payload == {
        "userId": "bot-id",
        "withReplies": False,
        "withRenotes": False,
        "withChannelNotes": False,
        "untilDate": 123456789,
        "limit": 100,
        "allowPartial": False,
        "withFiles": False,
        "untilId": "note-2",
    }
