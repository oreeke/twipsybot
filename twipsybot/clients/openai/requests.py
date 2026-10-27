import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import openai

from ...shared.consts import REQUEST_TIMEOUT

TokenParam = Literal["max_tokens", "max_completion_tokens"]
ToolSpecs = Sequence[Mapping[str, Any]]


async def make_responses_request(
    *,
    client: openai.AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int | None,
    temperature: float | None,
    json_output: bool = False,
    tools: ToolSpecs | None = None,
):
    async with semaphore:
        kwargs: dict[str, Any] = {
            "model": model,
            "input": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
        if json_output:
            kwargs["text"] = {"format": {"type": "json_object"}}
        if tools:
            kwargs["tools"] = [{"type": "function", **tool} for tool in tools]
        async with asyncio.timeout(REQUEST_TIMEOUT):
            return await client.responses.create(**kwargs)


async def make_chat_completions_request(
    *,
    client: openai.AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    model: str,
    token_param: TokenParam = "max_tokens",
    messages: list[dict[str, Any]],
    max_tokens: int | None,
    temperature: float | None,
    json_output: bool = False,
    tools: ToolSpecs | None = None,
):
    async with semaphore:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs[token_param] = max_tokens
        if json_output:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": tool} for tool in tools]
        async with asyncio.timeout(REQUEST_TIMEOUT):
            return await client.chat.completions.create(**kwargs)
