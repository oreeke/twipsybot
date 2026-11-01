from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, NamedTuple

import pytest
from aiohttp import WSMsgType, web
from aiohttp.multipart import BodyPartReader
from aiohttp.test_utils import TestServer

from twipsybot.clients.misskey.api import MisskeyAPI
from twipsybot.clients.misskey.stream import StreamingClient


class FakeStreamingServer:
    def __init__(self) -> None:
        self.url = ""
        self.messages: list[dict[str, Any]] = []
        self.connections = 0
        self.refusals = 0
        self.confirm = True
        self.sockets: list[web.WebSocketResponse] = []

    async def handle(self, request: web.Request) -> web.StreamResponse:
        if self.refusals:
            self.refusals -= 1
            return web.Response(status=503)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        self.sockets.append(ws)
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            data = message.json()
            self.messages.append(data)
            if self.confirm and data["type"] == "connect":
                await ws.send_json(
                    {"type": "connected", "body": {"id": data["body"]["id"]}}
                )
        return ws

    def sent(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.messages if m["type"] == kind]

    async def send(self, payload: dict[str, Any] | str) -> None:
        ws = self.sockets[-1]
        if isinstance(payload, str):
            await ws.send_str(payload)
        else:
            await ws.send_json(payload)

    async def drop(self) -> None:
        await self.sockets[-1].close()

    async def until(self, condition: Callable[[], bool], timeout: float = 2) -> None:
        async with asyncio.timeout(timeout):
            while not condition():
                await asyncio.sleep(0.005)


class Recorded(NamedTuple):
    endpoint: str
    authorization: str | None
    payload: dict[str, Any]


class FakeMisskeyServer:
    def __init__(self) -> None:
        self.url = ""
        self.requests: list[Recorded] = []
        self.replies: list[tuple[int, Any, dict[str, str]]] = []
        self.default: tuple[int, Any, dict[str, str]] = (200, {}, {})

    def reply(self, status: int, body: Any = "", **headers: str) -> None:
        self.replies.append((status, body, headers))

    async def api(self, request: web.Request) -> web.Response:
        endpoint = request.match_info["endpoint"]
        payload: dict[str, Any] = {}
        if endpoint == "drive/files/create":
            async for field in await request.multipart():
                assert isinstance(field, BodyPartReader)
                assert field.name is not None
                payload[field.name] = (
                    await field.read() if field.name == "file" else await field.text()
                )
        else:
            payload = await request.json()
        self.requests.append(
            Recorded(endpoint, request.headers.get("Authorization"), payload)
        )
        status, body, headers = self.replies.pop(0) if self.replies else self.default
        if isinstance(body, str):
            return web.Response(status=status, text=body, headers=headers)
        return web.json_response(body, status=status, headers=headers)

    async def external_file(self, request: web.Request) -> web.Response:
        self.requests.append(
            Recorded("external", request.headers.get("Authorization"), {})
        )
        return web.Response(body=b"image")


@pytest.fixture
async def misskey_server() -> AsyncIterator[FakeMisskeyServer]:
    server = FakeMisskeyServer()
    app = web.Application()
    app.router.add_post("/api/{endpoint:.*}", server.api)
    app.router.add_get("/file", server.external_file)
    async with TestServer(app) as test_server:
        server.url = str(test_server.make_url("/")).rstrip("/")
        yield server


@pytest.fixture
async def api(misskey_server: FakeMisskeyServer) -> AsyncIterator[MisskeyAPI]:
    api = MisskeyAPI(misskey_server.url, "token")
    yield api
    await api.close()


@pytest.fixture
async def ws_server() -> AsyncIterator[FakeStreamingServer]:
    server = FakeStreamingServer()
    app = web.Application()
    app.router.add_get("/streaming", server.handle)
    async with TestServer(app) as test_server:
        server.url = str(test_server.make_url("/"))
        yield server


@pytest.fixture
async def client() -> AsyncIterator[StreamingClient]:
    client = StreamingClient("https://example.com", "token")
    yield client
    await client.close()


@pytest.fixture
async def live_client(ws_server: FakeStreamingServer) -> AsyncIterator[StreamingClient]:
    client = StreamingClient(ws_server.url, "token")
    yield client
    await client.close()
