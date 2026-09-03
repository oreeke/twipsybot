import asyncio
from typing import Any
from urllib.parse import urlparse

import openai

from ...shared.constants import REQUEST_TIMEOUT


async def make_responses_request(
    *,
    client: openai.AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int | None,
    temperature: float | None,
    json_output: bool = False,
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
        return await asyncio.wait_for(
            client.responses.create(**kwargs),
            timeout=REQUEST_TIMEOUT,
        )


async def make_chat_completions_request(
    *,
    client: openai.AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    model: str,
    api_base: str,
    messages: list[dict[str, Any]],
    max_tokens: int | None,
    temperature: float | None,
    json_output: bool = False,
):
    async with semaphore:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            api_base_host = urlparse(api_base).hostname
            if api_base_host and (
                api_base_host == "openai.com" or api_base_host.endswith(".openai.com")
            ):
                kwargs["max_completion_tokens"] = max_tokens
            else:
                kwargs["max_tokens"] = max_tokens
        if json_output:
            kwargs["response_format"] = {"type": "json_object"}
        return await asyncio.wait_for(
            client.chat.completions.create(**kwargs),
            timeout=REQUEST_TIMEOUT,
        )
