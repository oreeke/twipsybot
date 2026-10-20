from __future__ import annotations

import asyncio
import base64
import os
from types import SimpleNamespace
from typing import Any, Self, cast
from unittest.mock import AsyncMock, Mock

import pytest
from httpx2 import Request, Response
from openai import APIStatusError

from twipsybot.clients.openai.api import OpenAIAPI
from twipsybot.clients.openai.extract import (
    extract_responses_text,
    process_chat_completions_response,
)
from twipsybot.clients.openai.requests import (
    make_chat_completions_request,
    make_responses_request,
)
from twipsybot.shared.errors import (
    APIConnectionError,
)


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


async def test_openai_generates_png_bytes() -> None:
    api = OpenAIAPI("test", "m")
    generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode())]
        )
    )
    api.client = cast(Any, SimpleNamespace(images=SimpleNamespace(generate=generate)))

    assert await api.generate_image("一只猫", model="gpt-image-2") == b"png"
    generate.assert_awaited_once_with(
        model="gpt-image-2",
        prompt="一只猫",
    )


async def test_openai_forwards_explicit_image_options() -> None:
    api = OpenAIAPI("test", "m")
    generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=None, url="https://example.com")]
        )
    )
    api.client = cast(Any, SimpleNamespace(images=SimpleNamespace(generate=generate)))

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


async def test_openai_image_rejects_endpoint_without_key() -> None:
    api = OpenAIAPI("main", "m")
    generate = AsyncMock()
    api.client = cast(Any, SimpleNamespace(images=SimpleNamespace(generate=generate)))

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
async def test_openai_image_override_uses_dedicated_client(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
    api_key: str,
    host: str,
) -> None:
    api = OpenAIAPI("main", "m", base_url="https://main.example/v1")
    main_generate = AsyncMock()
    api.client = cast(
        Any, SimpleNamespace(images=SimpleNamespace(generate=main_generate))
    )
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

    api = OpenAIAPI("test", "deepseek-flash")

    load_encoding.assert_not_called()
    assert api.trim_chat_history([{"role": "user", "content": "hello"}], 9)
    load_encoding.assert_called_once_with("deepseek-flash")
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
    api = OpenAIAPI("test", "deepseek-flash")
    history = [
        {"role": "user", "content": "123456"},
        {"role": "assistant", "content": "ok"},
    ]

    assert api.trim_chat_history(history, 10) == [history[-1]]
    assert api.trim_chat_history(history, 10) == [history[-1]]
    load_encoding.assert_called_once_with("deepseek-flash")


def test_responses_file_part_converts_for_chat_completions() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "hi"},
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

    converted = OpenAIAPI._to_chat_completions_messages(messages)

    assert converted[0]["content"] == [
        {"type": "text", "text": "hi"},
        {
            "type": "file",
            "file": {
                "filename": "a.pdf",
                "file_data": "data:application/pdf;base64,AA==",
            },
        },
        {"type": "input_audio", "input_audio": {"data": "AA==", "format": "mp3"}},
    ]


def test_responses_text_prefers_aggregated_output() -> None:
    response = SimpleNamespace(output_text="complete", output=None)

    assert extract_responses_text(response) == "complete"


def test_responses_text_collects_message_output_parts() -> None:
    response = SimpleNamespace(
        output_text="",
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
        ],
    )

    assert extract_responses_text(response) == "first second"


def test_responses_text_rejects_truncated_output() -> None:
    response = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
    )

    with pytest.raises(APIConnectionError, match="max_output_tokens"):
        extract_responses_text(response)


@pytest.mark.parametrize(
    ("output", "message"),
    ((None, "Invalid output type"), ([], "Empty output")),
)
def test_responses_text_rejects_invalid_or_empty_output(
    output: Any, message: str
) -> None:
    response = SimpleNamespace(output_text="", output=output)

    with pytest.raises(APIConnectionError, match=message):
        extract_responses_text(response)


@pytest.mark.parametrize(
    ("content", "finish_reason", "message"),
    (("truncated", "length", "max_tokens"), ("", "stop", None)),
)
def test_chat_completions_rejects_incomplete_output(
    content: str, finish_reason: str, message: str | None
) -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason=finish_reason
            )
        ]
    )

    with pytest.raises(APIConnectionError, match=message):
        process_chat_completions_response(response, "test")


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
        messages=[],
        max_tokens=None,
        temperature=None,
        json_output=True,
    )

    call = create.await_args
    assert call is not None
    assert call.kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    ("message", "status_code", "expected"),
    (
        ("unsupported", 404, True),
        ("unsupported", 405, True),
        ("unsupported", 501, True),
        ("Invalid max_output_tokens", None, False),
    ),
)
def test_responses_unavailable_only_for_unsupported_status(
    message: str, status_code: int | None, expected: bool
) -> None:
    error = _BadRequest(message, status_code=status_code)

    assert OpenAIAPI._is_responses_unavailable(error) is expected


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
    api = OpenAIAPI("test", "m")
    monkeypatch.setattr(api, "_chat_completions", fallback)

    result = await api.generate_text("hello")

    assert result == "fallback"
    fallback.assert_awaited_once()
    await api.close()


async def test_openai_client_uses_sdk_retries() -> None:
    api = OpenAIAPI("test", "m")

    assert api.client.max_retries == 2
    await api.close()


@pytest.mark.parametrize(
    ("base_url", "host", "param"),
    [
        (None, "api.openai.com", "max_completion_tokens"),
        ("https://eu.api.openai.com/v1", "eu.api.openai.com", "max_completion_tokens"),
        ("https://api.deepseek.com", "api.deepseek.com", "max_tokens"),
    ],
)
async def test_openai_base_url_defaults_to_sdk_and_picks_token_param(
    monkeypatch: pytest.MonkeyPatch, base_url: str | None, host: str, param: str
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    api = OpenAIAPI("test", "m", base_url=base_url)

    assert api.client.base_url.host == host
    assert api._token_param == param
    await api.close()


async def test_openai_moderates_texts_in_batch() -> None:
    categories = SimpleNamespace(
        to_dict=lambda: {"harassment": True, "hate": False, "illicit": True}
    )
    create = AsyncMock(
        return_value=SimpleNamespace(results=[SimpleNamespace(categories=categories)])
    )
    api = OpenAIAPI("test", "m")
    api.client = cast(Any, SimpleNamespace(moderations=SimpleNamespace(create=create)))

    result = await api.moderate_texts(["text"])

    assert result == [frozenset({"harassment", "illicit"})]
    create.assert_awaited_once_with(model="omni-moderation-latest", input=["text"])


async def test_openai_moderation_splits_large_batches() -> None:
    categories = SimpleNamespace(to_dict=lambda: {"hate": True})

    async def create(*, model: str, input: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            results=[SimpleNamespace(categories=categories) for _ in input]
        )

    mock = AsyncMock(side_effect=create)
    api = OpenAIAPI("test", "m")
    api.client = cast(Any, SimpleNamespace(moderations=SimpleNamespace(create=mock)))

    result = await api.moderate_texts([str(i) for i in range(250)])

    assert len(result) == 250
    assert [len(call.kwargs["input"]) for call in mock.await_args_list] == [
        100,
        100,
        50,
    ]
