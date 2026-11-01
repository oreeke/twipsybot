from __future__ import annotations

import base64
import os
from types import SimpleNamespace
from typing import Any, Self, cast
from unittest.mock import AsyncMock, Mock

import pytest
from httpx2 import Request, Response
from openai import APIStatusError, BadRequestError

from twipsybot.clients.openai.api import OpenAIAPI
from twipsybot.shared.errors import APIConnectionError


def _status_error(cls: type[APIStatusError], status: int) -> APIStatusError:
    request = Request("POST", "https://example.com/v1")
    return cls("failed", response=Response(status, request=request), body={})


def _api(
    *, chat: Any = (), responses: Any = (), **kwargs: Any
) -> tuple[OpenAIAPI, AsyncMock, AsyncMock]:
    chat_create = AsyncMock(side_effect=chat)
    responses_create = AsyncMock(side_effect=responses)
    api = OpenAIAPI("test", "m", **kwargs)
    api.client = cast(
        Any,
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
            responses=SimpleNamespace(create=responses_create),
        ),
    )
    return api, chat_create, responses_create


def _images_api(generate: AsyncMock, **kwargs: Any) -> OpenAIAPI:
    api = OpenAIAPI("main", "m", **kwargs)
    api.client = cast(Any, SimpleNamespace(images=SimpleNamespace(generate=generate)))
    return api


def _choice(content: str | None, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=None),
        finish_reason=finish_reason,
    )


def _chat_reply(text: str | None, *calls: tuple[str, str, str]) -> SimpleNamespace:
    message = SimpleNamespace(
        content=text,
        tool_calls=[
            SimpleNamespace(id=i, function=SimpleNamespace(name=n, arguments=a))
            for i, n, a in calls
        ]
        or None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")]
    )


def _responses_reply(
    text: str = "", output: Any = None, status: str = "completed"
) -> SimpleNamespace:
    return SimpleNamespace(status=status, output=output, output_text=text)


class _Tools:
    specs: tuple[dict[str, Any], ...] = (
        {"name": "t_x", "description": "d", "parameters": {"type": "object"}},
    )

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run(self, name: str, arguments: str) -> str:
        self.calls.append((name, arguments))
        return f"out:{arguments}"


async def test_generate_image_returns_png_bytes() -> None:
    generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode())]
        )
    )
    api = _images_api(generate)

    assert await api.generate_image("一只猫", model="gpt-image-2") == b"png"
    generate.assert_awaited_once_with(model="gpt-image-2", prompt="一只猫")


async def test_generate_image_forwards_options_and_returns_url() -> None:
    generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=None, url="https://example.com")]
        )
    )
    api = _images_api(generate)

    image = await api.generate_image(
        "一只猫", model="gpt-image-2.5-flare", size="2048x2048", quality="medium"
    )

    assert image == "https://example.com"
    generate.assert_awaited_once_with(
        model="gpt-image-2.5-flare",
        prompt="一只猫",
        size="2048x2048",
        quality="medium",
    )


async def test_generate_image_rejects_endpoint_without_key() -> None:
    generate = AsyncMock()
    api = _images_api(generate)

    with pytest.raises(ValueError, match="base_url requires api_key"):
        await api.generate_image("猫", model="m", base_url="https://img.example/v1")

    generate.assert_not_awaited()


@pytest.mark.parametrize(
    ("overrides", "api_key", "host"),
    [
        (
            {"base_url": "https://img.example/v1", "api_key": "k2"},
            "k2",
            "img.example",
        ),
        ({"api_key": "k2"}, "k2", "main.example"),
    ],
    ids=("endpoint", "key-only"),
)
async def test_generate_image_override_uses_dedicated_client(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
    api_key: str,
    host: str,
) -> None:
    main_generate = AsyncMock()
    api = _images_api(main_generate, base_url="https://main.example/v1")
    generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=None, url="https://example.com")]
        )
    )
    created: list[Any] = []

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)
            self.images = SimpleNamespace(generate=generate)

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr("twipsybot.clients.openai.api.openai.AsyncOpenAI", _Client)

    assert await api.generate_image("一只猫", model="m", **overrides) == (
        "https://example.com"
    )

    main_generate.assert_not_awaited()
    [kwargs] = created
    assert kwargs["api_key"] == api_key
    assert kwargs["base_url"].split("/")[2] == host


def test_token_encoding_loads_lazily_into_data_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    encoding = SimpleNamespace(encode_ordinary=lambda text: list(text))
    load_encoding = Mock(return_value=encoding)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    monkeypatch.setattr(
        "twipsybot.clients.openai.api.tiktoken.encoding_for_model", load_encoding
    )

    api = OpenAIAPI("test", "deepseek-flash")

    load_encoding.assert_not_called()
    assert api.trim_chat_history([{"role": "user", "content": "hello"}], 9)
    load_encoding.assert_called_once_with("deepseek-flash")
    assert os.environ["TIKTOKEN_CACHE_DIR"] == str(
        (tmp_path / "data" / "tiktoken").resolve()
    )


def test_token_encoding_failure_uses_character_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_encoding = Mock(side_effect=OSError("offline"))
    monkeypatch.setattr(
        "twipsybot.clients.openai.api.tiktoken.encoding_for_model", load_encoding
    )
    api = OpenAIAPI("test", "deepseek-flash")
    history = [
        {"role": "user", "content": "123456"},
        {"role": "assistant", "content": "ok"},
    ]

    assert api.trim_chat_history(history, 10) == [history[-1]]
    assert api.trim_chat_history(history, 10) == [history[-1]]
    load_encoding.assert_called_once_with("deepseek-flash")


async def test_chat_mode_converts_responses_content_parts() -> None:
    api, chat, _ = _api(chat=[_chat_reply("ok")], api_mode="chat")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "hi"},
                {"type": "input_image", "image_url": "https://example.com/a.png"},
                {
                    "type": "input_file",
                    "filename": "a.pdf",
                    "file_data": "data:application/pdf;base64,AA==",
                },
                {
                    "type": "input_audio",
                    "input_audio": {"data": "AA==", "format": "mp3"},
                },
            ],
        }
    ]

    await api.generate_chat(messages)

    assert chat.await_args_list[-1].kwargs["messages"][0]["content"] == [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        {
            "type": "file",
            "file": {
                "filename": "a.pdf",
                "file_data": "data:application/pdf;base64,AA==",
            },
        },
        {"type": "input_audio", "input_audio": {"data": "AA==", "format": "mp3"}},
    ]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (_responses_reply("complete"), "complete"),
        (
            _responses_reply(
                output=[
                    SimpleNamespace(type="reasoning", content=[]),
                    SimpleNamespace(
                        type="message",
                        content=[
                            SimpleNamespace(type="output_text", text="first"),
                            SimpleNamespace(type="refusal", text="ignored"),
                            SimpleNamespace(type="output_text", text=" second"),
                        ],
                    ),
                    SimpleNamespace(type="message", content=None),
                ]
            ),
            "first second",
        ),
    ],
    ids=("aggregated", "message-parts"),
)
async def test_responses_text_is_extracted(reply: Any, expected: str) -> None:
    api, _, _ = _api(responses=[reply])

    assert await api.generate_text("hi") == expected


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (
            SimpleNamespace(
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            ),
            "max_output_tokens",
        ),
        (_responses_reply(output=None), "Invalid output type"),
        (_responses_reply(output=[]), "Empty output"),
    ],
    ids=("truncated", "invalid-output", "empty-output"),
)
async def test_responses_text_rejects_unusable_output(reply: Any, message: str) -> None:
    api, _, _ = _api(responses=[reply])

    with pytest.raises(APIConnectionError, match=message):
        await api.generate_text("hi")


@pytest.mark.parametrize(
    ("choices", "message"),
    [
        ([], "Empty choices"),
        ([_choice("truncated", "length")], "max_tokens"),
        ([_choice("")], None),
    ],
    ids=("no-choices", "truncated", "empty-content"),
)
async def test_chat_completions_rejects_incomplete_output(
    choices: list[Any], message: str | None
) -> None:
    api, _, _ = _api(chat=[SimpleNamespace(choices=choices)], api_mode="chat")

    with pytest.raises(APIConnectionError, match=message):
        await api.generate_text("hi")


@pytest.mark.parametrize(
    ("api_mode", "field", "expected"),
    [
        ("auto", "text", {"format": {"type": "json_object"}}),
        ("chat", "response_format", {"type": "json_object"}),
    ],
)
async def test_json_output_sets_mode_specific_request_field(
    api_mode: str, field: str, expected: dict[str, Any]
) -> None:
    api, chat, responses = _api(
        chat=[_chat_reply("{}")], responses=[_responses_reply("{}")], api_mode=api_mode
    )

    await api.generate_text("hi", json_output=True)

    create = chat if api_mode == "chat" else responses
    assert create.await_args_list[-1].kwargs[field] == expected


@pytest.mark.parametrize(
    ("base_url", "param"),
    [
        (None, "max_completion_tokens"),
        ("https://eu.api.openai.com/v1", "max_completion_tokens"),
        ("https://api.deepseek.com", "max_tokens"),
    ],
)
async def test_token_limit_parameter_depends_on_endpoint(
    monkeypatch: pytest.MonkeyPatch, base_url: str | None, param: str
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    api, chat, _ = _api(chat=[_chat_reply("ok")], api_mode="chat", base_url=base_url)

    await api.generate_text("hi", max_tokens=5)

    assert chat.await_args_list[-1].kwargs[param] == 5
    other = {"max_tokens", "max_completion_tokens"} - {param}
    assert chat.await_args_list[-1].kwargs.keys().isdisjoint(other)


@pytest.mark.parametrize("status", [404, 405, 501])
async def test_unsupported_responses_endpoint_falls_back_to_chat_completions(
    status: int,
) -> None:
    api, chat, responses = _api(
        chat=[_chat_reply("first"), _chat_reply("second")],
        responses=[_status_error(APIStatusError, status)],
    )

    assert await api.generate_text("hi") == "first"
    assert await api.generate_text("hi") == "second"

    assert not api.uses_responses_api
    responses.assert_awaited_once()
    assert chat.await_count == 2


async def test_other_responses_errors_are_not_treated_as_unsupported() -> None:
    api, chat, _ = _api(responses=[_status_error(APIStatusError, 500)])

    with pytest.raises(APIStatusError):
        await api.generate_text("hi")

    chat.assert_not_awaited()
    assert api.uses_responses_api


async def test_chat_tool_calls_run_in_parallel_and_feed_back_results() -> None:
    api, create, _ = _api(
        chat=[
            _chat_reply(None, ("c1", "t_x", "1"), ("c2", "t_x", "2")),
            _chat_reply("done"),
        ],
        api_mode="chat",
    )
    tools = _Tools()

    assert (
        await api.generate_chat([{"role": "user", "content": "hi"}], tools=tools)
        == "done"
    )

    assert tools.calls == [("t_x", "1"), ("t_x", "2")]
    first, second = (call.kwargs for call in create.await_args_list)
    assert first["tools"] == [{"type": "function", "function": tools.specs[0]}]
    assert second["tools"] == first["tools"]
    assert second["messages"][1:] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "t_x", "arguments": "1"},
                },
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "t_x", "arguments": "2"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "out:1"},
        {"role": "tool", "tool_call_id": "c2", "content": "out:2"},
    ]


async def test_responses_tool_calls_feed_back_function_outputs() -> None:
    item = {"type": "function_call", "call_id": "c1", "name": "t_x", "arguments": "1"}
    call = SimpleNamespace(model_dump=lambda **_: item, **item)
    api, _, create = _api(
        responses=[_responses_reply(output=[call]), _responses_reply("done", [])]
    )

    assert (
        await api.generate_chat([{"role": "user", "content": "hi"}], tools=_Tools())
        == "done"
    )

    first, second = (call.kwargs for call in create.await_args_list)
    assert first["tools"] == [{"type": "function", **_Tools.specs[0]}]
    assert second["input"][1:] == [
        item,
        {"type": "function_call_output", "call_id": "c1", "output": "out:1"},
    ]


async def test_tool_rounds_are_capped_then_answer_without_tools() -> None:
    looping = [_chat_reply(None, ("c", "t_x", "{}"))] * 3
    api, create, _ = _api(chat=[*looping, _chat_reply("final")], api_mode="chat")

    assert await api.generate_chat([], tools=_Tools()) == "final"

    assert ["tools" in call.kwargs for call in create.await_args_list] == [
        True,
        True,
        True,
        False,
    ]


async def test_tool_support_rejection_retries_without_tools() -> None:
    api, create, _ = _api(
        chat=[_status_error(BadRequestError, 400), _chat_reply("plain")],
        api_mode="chat",
    )

    assert await api.generate_chat([], tools=_Tools()) == "plain"

    assert "tools" not in create.await_args_list[1].kwargs


async def test_moderation_splits_large_batches_and_lists_flagged_categories() -> None:
    categories = SimpleNamespace(
        to_dict=lambda: {"harassment": True, "hate": False, "illicit": True}
    )

    async def moderate(*, model: str, input: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            results=[SimpleNamespace(categories=categories) for _ in input]
        )

    create = AsyncMock(side_effect=moderate)
    api = OpenAIAPI("test", "m")
    api.client = cast(Any, SimpleNamespace(moderations=SimpleNamespace(create=create)))

    result = await api.moderate_texts([str(i) for i in range(250)])

    assert result == [frozenset({"harassment", "illicit"})] * 250
    assert [len(call.kwargs["input"]) for call in create.await_args_list] == [
        100,
        100,
        50,
    ]
    assert create.await_args_list[-1].kwargs["model"] == "omni-moderation-latest"
