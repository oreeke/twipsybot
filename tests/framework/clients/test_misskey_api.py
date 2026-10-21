from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import ClientError, web
from aiohttp.multipart import BodyPartReader
from aiohttp.test_utils import TestServer

from twipsybot.clients.misskey.api import MisskeyAPI
from twipsybot.clients.misskey.bridge import _redact_access_token
from twipsybot.shared.consts import API_MAX_RETRIES
from twipsybot.shared.errors import (
    APIBadRequestError,
    APIConnectionError,
    APIPermissionError,
    APIRateLimitError,
)


def test_misskey_access_token_is_redacted() -> None:
    text = 'https://example.com/streaming?i=secret&x=1 {"i":"token"}'

    assert _redact_access_token(text) == (
        'https://example.com/streaming?i=***&x=1 {"i":"***"}'
    )


def test_misskey_error_format_uses_current_fields() -> None:
    assert (
        MisskeyAPI._parse_error(
            '{"error":{"code":"INVALID_PARAM","message":"Invalid parameter","id":"id"}}'
        )[0]
        == "INVALID_PARAM: Invalid parameter"
    )


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
    api.make_request = request
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
    api.make_request = request
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


async def test_misskey_api_stops_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = AsyncMock(side_effect=APIConnectionError())
    sleep = AsyncMock()
    api = object.__new__(MisskeyAPI)
    api.make_request = request
    monkeypatch.setattr("twipsybot.clients.misskey.api.asyncio.sleep", sleep)

    with pytest.raises(APIConnectionError):
        await api.make_read_request("notes/show")

    assert request.await_count == API_MAX_RETRIES + 1
    assert sleep.await_count == API_MAX_RETRIES


async def test_misskey_api_does_not_retry_writes() -> None:
    post = Mock(side_effect=ClientError("down"))
    shared = SimpleNamespace(session=SimpleNamespace(post=post))
    api = MisskeyAPI("https://example.com", "token", shared=cast(Any, shared))

    with pytest.raises(APIConnectionError):
        await api.make_request("notes/create")

    post.assert_called_once()


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


async def test_create_note_does_not_turn_empty_visibility_public() -> None:
    request = AsyncMock(return_value={})
    api = object.__new__(MisskeyAPI)
    api.make_request = request

    await api.create_note("text", visibility="")

    request.assert_awaited_once_with("notes/create", {"text": "text", "visibility": ""})


async def test_user_endpoints_use_misskey_api() -> None:
    read = AsyncMock(return_value={"id": "u1"})
    request = AsyncMock(return_value={})
    api = object.__new__(MisskeyAPI)
    api.make_read_request = read
    api.make_request = request

    assert await api.show_user("u1") == {"id": "u1"}
    await api.update_user_memo("u1", None)

    read.assert_awaited_once_with("users/show", {"userId": "u1"})
    request.assert_awaited_once_with(
        "users/update-memo", {"userId": "u1", "memo": None}
    )


@pytest.mark.parametrize("retry", [False, True])
async def test_call_api_normalizes_endpoint_and_selects_request(retry: bool) -> None:
    read = AsyncMock(return_value={"via": "read"})
    write = AsyncMock(return_value={"via": "write"})
    api = object.__new__(MisskeyAPI)
    api.make_read_request = read
    api.make_request = write
    params = {"query": "misskey"}

    result = await api.call_api("/notes/search", params, retry=retry)

    chosen, other = (read, write) if retry else (write, read)
    assert result == chosen.return_value
    chosen.assert_awaited_once_with("notes/search", {"query": "misskey"})
    other.assert_not_awaited()
    assert params == {"query": "misskey"}


@pytest.mark.parametrize(
    "endpoint",
    [
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
    ],
)
async def test_call_api_rejects_invalid_endpoint(endpoint: str) -> None:
    api = object.__new__(MisskeyAPI)
    api.make_request = AsyncMock()

    with pytest.raises(ValueError, match="invalid endpoint"):
        await api.call_api(endpoint)

    api.make_request.assert_not_awaited()


async def test_call_api_rejects_token_param() -> None:
    api = object.__new__(MisskeyAPI)
    api.make_request = AsyncMock()

    with pytest.raises(ValueError, match="'i'"):
        await api.call_api("notes/show", {"i": "override"})

    api.make_request.assert_not_awaited()


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
