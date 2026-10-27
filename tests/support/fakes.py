from __future__ import annotations

import itertools
import json
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

DEFAULT_AI_REPLY = "这是 AI 生成的回复"


class FakeMisskeyServer:
    def __init__(
        self,
        server: TestServer,
        calls: dict[str, list[dict[str, Any]]],
        responses: dict[str, Any],
    ):
        self._server = server
        self.calls = calls
        self._responses = responses

    @property
    def url(self) -> str:
        return str(self._server.make_url("/")).rstrip("/")

    def set_response(self, endpoint: str, response: Any) -> None:
        self._responses[endpoint] = response


def build_misskey_app(
    calls: dict[str, list[dict[str, Any]]], responses: dict[str, Any]
) -> web.Application:
    note_ids = itertools.count(1)
    responses.update(
        {
            "i": lambda payload: {"id": "bot-id", "username": "testbot"},
            "notes/create": lambda payload: {
                "createdNote": {
                    "id": f"note-{next(note_ids)}",
                    "visibility": payload.get("visibility", "public"),
                }
            },
            "notes/show": lambda payload: {"visibility": "public"},
            "notes/conversation": lambda payload: [],
            "chat/messages/create-to-user": lambda payload: {"id": "msg-1"},
            "chat/messages/create-to-room": lambda payload: {"id": "msg-1"},
            "chat/messages/user-timeline": lambda payload: [],
            "chat/messages/room-timeline": lambda payload: [],
            "antennas/list": lambda payload: [],
            "users/show": lambda payload: {
                "id": payload.get("userId"),
                "isFollowed": True,
            },
        }
    )

    async def handle(request: web.Request) -> web.Response:
        endpoint = request.match_info["endpoint"]
        payload = await request.json()
        calls.setdefault(endpoint, []).append(payload)
        responder = responses.get(endpoint)
        return web.json_response(responder(payload) if responder else {})

    app = web.Application()
    app.router.add_post("/api/{endpoint:.*}", handle)
    return app


class FakeOpenAIServer:
    def __init__(
        self, server: TestServer, calls: list[dict[str, Any]], reply: dict[str, Any]
    ):
        self._server = server
        self.calls = calls
        self._reply = reply

    @property
    def url(self) -> str:
        return str(self._server.make_url("/v1")).rstrip("/")

    def set_reply(self, text: str) -> None:
        self._reply["text"] = text

    def set_tool_call(self, name: str, **arguments: Any) -> None:
        self._reply["tool"] = {"name": name, "arguments": arguments}


def build_openai_app(
    calls: list[dict[str, Any]], reply: dict[str, Any]
) -> web.Application:
    async def chat_completions(request: web.Request) -> web.Response:
        payload = await request.json()
        calls.append(payload)
        message: dict[str, Any] = {"role": "assistant", "content": reply["text"]}
        finish_reason = "stop"
        if (tool := reply.get("tool")) and payload["messages"][-1]["role"] != "tool":
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": tool["name"],
                            "arguments": json.dumps(tool["arguments"]),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        return web.json_response(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": payload.get("model", "test-model"),
                "choices": [
                    {"index": 0, "finish_reason": finish_reason, "message": message}
                ],
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_completions)
    return app
