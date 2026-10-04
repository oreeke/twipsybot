import asyncio
import base64
import os
from pathlib import Path
from typing import Any

import openai
import tiktoken
from loguru import logger
from openai import (
    APIStatusError,
    BadRequestError,
)
from openai import (
    AuthenticationError as OpenAIAuthenticationError,
)

from ...shared.constants import (
    API_MAX_RETRIES,
    API_TIMEOUT,
    OPENAI_MAX_CONCURRENCY,
    REQUEST_TIMEOUT,
)
from ...shared.exceptions import APIConnectionError, AuthenticationError
from .extract import (
    extract_responses_text,
    process_chat_completions_response,
)
from .requests import (
    TokenParam,
    make_chat_completions_request,
    make_responses_request,
)

__all__ = ("OpenAIAPI",)


class OpenAIAPI:
    @staticmethod
    def _safe_error_message(e: Exception, *, limit: int = 300) -> str:
        name = type(e).__name__
        msg = str(e).strip()
        if not msg:
            return name
        msg = " ".join(msg.split())
        if len(msg) > limit:
            msg = f"{msg[: max(0, limit - 3)]}..."
        return f"{name}: {msg}"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        api_mode: str = "auto",
        image_model: str | None = None,
        image_size: str | None = None,
        image_quality: str | None = None,
    ):
        self.model = model
        self.api_mode = api_mode
        self.image_model = image_model
        self.image_size = image_size
        self.image_quality = image_quality
        self._responses_disabled = False
        self._semaphore = asyncio.Semaphore(OPENAI_MAX_CONCURRENCY)
        self._token_encoding: tiktoken.Encoding | None = None
        self._token_encoding_unavailable = False
        try:
            self.client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=base_url or None,
                timeout=API_TIMEOUT,
                max_retries=API_MAX_RETRIES,
            )
        except Exception as e:
            logger.error(f"Failed to create OpenAI API client: {e}")
            raise APIConnectionError(self._safe_error_message(e)) from e
        host = self.client.base_url.host
        self._token_param: TokenParam = (
            "max_completion_tokens"
            if host == "openai.com" or host.endswith(".openai.com")
            else "max_tokens"
        )

    def update(
        self,
        *,
        model: str,
        api_mode: str,
        image_model: str | None,
        image_size: str | None,
        image_quality: str | None,
    ) -> None:
        if api_mode != self.api_mode:
            self.api_mode = api_mode
            self._responses_disabled = False
        self.model = model
        self.image_model = image_model
        self.image_size = image_size
        self.image_quality = image_quality

    async def _chat_completions(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int | None,
        temperature: float | None,
        call_type: str,
        json_output: bool = False,
    ) -> str:
        response = await make_chat_completions_request(
            client=self.client,
            semaphore=self._semaphore,
            model=self.model,
            token_param=self._token_param,
            messages=self._to_chat_completions_messages(messages),
            max_tokens=max_tokens,
            temperature=temperature,
            json_output=json_output,
        )
        return process_chat_completions_response(response, call_type)

    @property
    def uses_responses_api(self) -> bool:
        return not self._responses_disabled and self.api_mode != "chat"

    @staticmethod
    def _to_chat_completions_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                converted.append(message)
                continue
            parts: list[Any] = []
            for part in content:
                if not isinstance(part, dict):
                    parts.append(part)
                elif part.get("type") == "input_text":
                    parts.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "input_image":
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": part.get("image_url", "")},
                        }
                    )
                else:
                    parts.append(part)
            converted.append({**message, "content": parts})
        return converted

    def trim_chat_history(
        self, history: list[dict[str, str]], token_budget: int | None
    ) -> list[dict[str, str]]:
        if not isinstance(token_budget, int) or token_budget <= 0:
            return []
        encoding = self._get_token_encoding()
        selected: list[dict[str, str]] = []
        used_tokens = 0
        for message in reversed(history):
            content = message.get("content", "")
            message_tokens = (
                len(encoding.encode_ordinary(content)) if encoding else len(content)
            ) + 4
            if used_tokens + message_tokens > token_budget:
                break
            selected.append(message)
            used_tokens += message_tokens
        selected.reverse()
        return selected

    def _get_token_encoding(self) -> tiktoken.Encoding | None:
        if self._token_encoding is not None or self._token_encoding_unavailable:
            return self._token_encoding
        os.environ.setdefault(
            "TIKTOKEN_CACHE_DIR", str(Path("data/tiktoken").resolve())
        )
        try:
            try:
                self._token_encoding = tiktoken.encoding_for_model(self.model)
            except KeyError:
                self._token_encoding = tiktoken.get_encoding("o200k_base")
        except Exception as e:
            self._token_encoding_unavailable = True
            logger.warning(
                f"Failed to load tiktoken encoding; using character estimate: "
                f"{self._safe_error_message(e)}"
            )
        return self._token_encoding

    @staticmethod
    def _is_responses_unavailable(error: Any) -> bool:
        if getattr(error, "status_code", None) in {404, 405, 501}:
            return True
        code = getattr(error, "code", None)
        if code in {"unsupported_api", "unsupported_endpoint", "not_implemented"}:
            return True
        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "responses api is not supported",
                "responses api not supported",
                "does not support the responses api",
                "doesn't support the responses api",
                "does not support responses api",
                "unsupported endpoint: /responses",
                "unknown endpoint: /responses",
                "unrecognized request url: /v1/responses",
            )
        )

    async def _call_api(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int | None,
        temperature: float | None,
        call_type: str,
        json_output: bool = False,
    ) -> str:
        args = (messages, max_tokens, temperature, call_type, json_output)
        try:
            if not self.uses_responses_api:
                text = await self._chat_completions(*args)
            else:
                try:
                    response = await make_responses_request(
                        client=self.client,
                        semaphore=self._semaphore,
                        model=self.model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        json_output=json_output,
                    )
                    text = extract_responses_text(response)
                except APIStatusError as e:
                    if not self._is_responses_unavailable(e):
                        raise
                    self._responses_disabled = True
                    logger.warning(
                        f"Responses API unavailable; falling back to Chat Completions: {e}"
                    )
                    text = await self._chat_completions(*args)
        except OpenAIAuthenticationError as e:
            logger.error(f"API authentication failed: {e}")
            raise AuthenticationError(self._safe_error_message(e)) from e
        except BadRequestError as e:
            logger.error(f"API request parameter error: {e}")
            raise ValueError(self._safe_error_message(e)) from e
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Invalid API response format: {e}")
            raise ValueError(self._safe_error_message(e)) from e
        logger.debug(
            f"OpenAI API {call_type} call succeeded; output length: {len(text)}"
        )
        return text

    async def close(self):
        if getattr(self, "client", None):
            await self.client.close()
            logger.debug("OpenAI API client closed")

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_output: bool = False,
    ) -> str:
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt.strip()}]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt.strip()})
        return await self._call_api(
            messages, max_tokens, temperature, "single-turn text", json_output
        )

    async def generate_chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        return await self._call_api(
            messages, max_tokens, temperature, "multi-turn chat"
        )

    async def moderate_texts(self, texts: list[str]) -> list[frozenset[str]]:
        async with self._semaphore, asyncio.timeout(REQUEST_TIMEOUT):
            response = await self.client.moderations.create(
                model="omni-moderation-latest", input=texts
            )
        return [
            frozenset(
                name for name, flagged in result.categories.to_dict().items() if flagged
            )
            for result in response.results
        ]

    async def generate_image(self, prompt: str) -> bytes | str:
        if not self.image_model:
            raise ValueError("image generation is not configured")
        options: dict[str, Any] = {
            "model": self.image_model,
            "prompt": prompt.strip(),
        }
        if self.image_size:
            options["size"] = self.image_size
        if self.image_quality:
            options["quality"] = self.image_quality
        async with self._semaphore:
            response = await self.client.images.generate(**options)
        image = response.data[0] if response.data else None
        if image is None:
            raise ValueError("image API returned no image data")
        if image.b64_json:
            try:
                return base64.b64decode(image.b64_json, validate=True)
            except ValueError as e:
                raise ValueError("image API returned invalid image data") from e
        if image.url:
            return image.url
        raise ValueError("image API returned no image data")
