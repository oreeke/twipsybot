import asyncio
import json
import random
import time
from typing import Any

import aiohttp
from loguru import logger

from ...shared.constants import (
    API_MAX_RETRIES,
    MISSKEY_MAX_CONCURRENCY,
)
from ...shared.exceptions import (
    APIBadRequestError,
    APIConnectionError,
    APIFileTooLargeError,
    APINotFoundError,
    APIPermissionError,
    APIRateLimitError,
    APIResponseError,
    AuthenticationError,
)
from .drive import MisskeyDrive
from .transport import TCPClient

__all__ = ("MisskeyAPI",)

NOTE_TEXT_MAX_LENGTH = 3000
CHAT_TEXT_MAX_LENGTH = 2000
MAX_RETRY_AFTER = 60.0


class MisskeyAPI:
    def __init__(
        self,
        instance_url: str,
        access_token: str,
        *,
        transport: TCPClient | None = None,
    ):
        self.instance_url = instance_url.rstrip("/")
        self.access_token = access_token
        self.transport: TCPClient = transport or TCPClient()
        self.drive: MisskeyDrive = MisskeyDrive(self)
        self._semaphore = asyncio.Semaphore(MISSKEY_MAX_CONCURRENCY)
        self._antennas_cache: list[dict[str, Any]] = []
        self._antennas_cache_expires_at = 0.0
        self._antennas_cache_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.transport.close_session(silent=True)
        logger.debug("Misskey API client closed")

    @property
    def session(self) -> aiohttp.ClientSession:
        return self.transport.session

    @property
    def semaphore(self) -> asyncio.Semaphore:
        return self._semaphore

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    @staticmethod
    def _format_error_text(error_text: str) -> str:
        s = error_text.strip()
        if not s:
            return ""
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return s
        if not isinstance(obj, dict):
            return s
        err = obj.get("error")
        if not isinstance(err, dict):
            return s
        code = err.get("code")
        msg = err.get("message")
        if isinstance(code, str) and isinstance(msg, str):
            return f"{code}: {msg}"
        if isinstance(msg, str):
            return msg
        return s

    @staticmethod
    def _error_details(error_text: str) -> dict[str, Any]:
        try:
            payload = json.loads(error_text)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
            return {}
        return payload["error"]

    @classmethod
    def _response_error(
        cls, response: Any, endpoint: str, raw_error_text: str
    ) -> APIResponseError:
        status = response.status
        error_text = cls._format_error_text(raw_error_text)
        details = cls._error_details(raw_error_text)
        error_type: type[APIResponseError] = {
            400: APIBadRequestError,
            401: AuthenticationError,
            403: APIPermissionError,
            404: APINotFoundError,
            413: APIFileTooLargeError,
            429: APIRateLimitError,
        }.get(
            status,
            APIBadRequestError if 400 <= status < 500 else APIConnectionError,
        )
        retry_after: float | None = None
        try:
            retry_after = max(0.0, float(response.headers.get("Retry-After", "")))
        except (TypeError, ValueError):
            pass
        log = logger.warning if status == 429 else logger.error
        log(f"Misskey API failed: {status} - {endpoint} - {error_text}")
        return error_type(
            error_text,
            status=status,
            code=details.get("code") if isinstance(details.get("code"), str) else None,
            error_id=details.get("id") if isinstance(details.get("id"), str) else None,
            kind=details.get("kind") if isinstance(details.get("kind"), str) else None,
            info=details.get("info"),
            retry_after=retry_after,
        )

    async def _process_response(self, response, endpoint: str) -> Any:
        if response.status in (200, 204):
            if response.status == 204:
                logger.debug(f"Misskey API request succeeded: {endpoint}")
                return {}
            try:
                result = await response.json()
                logger.debug(f"Misskey API request succeeded: {endpoint}")
                return result
            except (json.JSONDecodeError, aiohttp.ContentTypeError):
                if not await response.read():
                    logger.debug(f"Misskey API request succeeded: {endpoint}")
                    return {}
                raise APIConnectionError() from None
        raw_error_text = await response.text()
        raise self._response_error(response, endpoint, raw_error_text)

    async def make_read_request(
        self, endpoint: str, data: dict[str, Any] | None = None
    ) -> Any:
        for attempt in range(1, API_MAX_RETRIES + 1):
            try:
                return await self._make_request_once(endpoint, data)
            except APIRateLimitError as e:
                logger.info(f"Retry attempt #{attempt}...")
                delay = (
                    min(e.retry_after, MAX_RETRY_AFTER)
                    if e.retry_after is not None
                    else random.uniform(0, min(2 ** (attempt - 1), 30))
                )
                await asyncio.sleep(delay)
            except APIConnectionError:
                logger.info(f"Retry attempt #{attempt}...")
                await asyncio.sleep(random.uniform(0, min(2 ** (attempt - 1), 30)))
        return await self._make_request_once(endpoint, data)

    async def make_request(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
    ) -> Any:
        return await self._make_request_once(endpoint, data)

    async def _make_request_once(
        self, endpoint: str, data: dict[str, Any] | None = None
    ) -> Any:
        url = f"{self.instance_url}/api/{endpoint}"
        payload = dict(data) if data else {}
        try:
            session: aiohttp.ClientSession = self.session
            async with (
                self._semaphore,
                session.post(url, json=payload, headers=self.auth_headers) as response,
            ):
                return await self._process_response(response, endpoint)
        except (
            aiohttp.ClientError,
            json.JSONDecodeError,
        ) as e:
            logger.error(f"HTTP request error: {e}")
            raise APIConnectionError() from e

    @staticmethod
    def _limit_text(text: str, max_length: int, target: str) -> str:
        if len(text) <= max_length:
            return text
        logger.warning(
            f"Truncated {target} text from {len(text)} to {max_length} characters"
        )
        return f"{text[: max_length - 1]}…"

    async def create_note(
        self,
        text: str,
        visibility: str | None = None,
        reply_id: str | None = None,
        local_only: bool | None = None,
        file_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if visibility is None:
            visibility = "public"
        data: dict[str, Any] = {
            "text": self._limit_text(text, NOTE_TEXT_MAX_LENGTH, "note"),
            "visibility": visibility,
        }
        if file_ids:
            data["fileIds"] = file_ids
        if reply_id:
            data["replyId"] = reply_id
        if local_only:
            data["localOnly"] = True
        result = await self.make_request("notes/create", data)
        logger.debug(
            f"Misskey note created: note_id={result.get('createdNote', {}).get('id', 'unknown')}"
        )
        return result

    async def get_note(self, note_id: str) -> dict[str, Any]:
        return await self.make_read_request("notes/show", {"noteId": note_id})

    async def get_user_notes(
        self,
        user_id: str,
        *,
        until_date: int,
        until_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        data: dict[str, Any] = {
            "userId": user_id,
            "withReplies": False,
            "withRenotes": False,
            "withChannelNotes": False,
            "untilDate": until_date,
            "limit": limit,
            "allowPartial": False,
            "withFiles": False,
        }
        if until_id:
            data["untilId"] = until_id
        result = await self.make_read_request("users/notes", data)
        return result if isinstance(result, list) else []

    async def delete_note(self, note_id: str) -> dict[str, Any]:
        return await self.make_request("notes/delete", {"noteId": note_id})

    async def get_current_user(self) -> dict[str, Any]:
        return await self.make_read_request("i", {})

    async def list_antennas(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if now < self._antennas_cache_expires_at and self._antennas_cache:
            return list(self._antennas_cache)
        async with self._antennas_cache_lock:
            now = time.monotonic()
            if now < self._antennas_cache_expires_at and self._antennas_cache:
                return list(self._antennas_cache)
            result = await self.make_read_request("antennas/list", {})
            antennas = result if isinstance(result, list) else []
            self._antennas_cache = antennas
            self._antennas_cache_expires_at = time.monotonic() + 30.0
            return list(antennas)

    async def send_message(
        self, user_id: str, text: str, file_id: str | None = None
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "toUserId": user_id,
            "text": self._limit_text(text, CHAT_TEXT_MAX_LENGTH, "chat message"),
        }
        if file_id:
            data["fileId"] = file_id
        result = await self.make_request("chat/messages/create-to-user", data)
        logger.debug(
            f"Misskey chat message sent: message_id={result.get('id', 'unknown')}"
        )
        return result

    async def send_room_message(
        self, room_id: str, text: str, file_id: str | None = None
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "toRoomId": room_id,
            "text": self._limit_text(text, CHAT_TEXT_MAX_LENGTH, "chat message"),
        }
        if file_id:
            data["fileId"] = file_id
        result = await self.make_request("chat/messages/create-to-room", data)
        logger.debug(
            f"Misskey room message sent: message_id={result.get('id', 'unknown')}"
        )
        return result

    async def create_reaction(self, note_id: str, reaction: str) -> dict[str, Any]:
        if not note_id:
            raise ValueError("note_id cannot be empty")
        if not reaction:
            raise ValueError("reaction cannot be empty")
        return await self.make_request(
            "notes/reactions/create", {"noteId": note_id, "reaction": reaction}
        )

    async def create_renote(
        self,
        note_id: str,
        visibility: str | None = None,
        text: str | None = None,
        local_only: bool | None = None,
    ) -> dict[str, Any]:
        if not note_id:
            raise ValueError("note_id cannot be empty")
        data: dict[str, Any] = {"renoteId": note_id}
        if visibility:
            data["visibility"] = visibility
        if text:
            data["text"] = self._limit_text(text, NOTE_TEXT_MAX_LENGTH, "note")
        if local_only is not None:
            data["localOnly"] = bool(local_only)
        result = await self.make_request("notes/create", data)
        logger.debug(
            f"Misskey renote created: note_id={result.get('createdNote', {}).get('id', 'unknown')}"
        )
        return result

    async def get_messages(
        self, user_id: str, limit: int = 10, since_id: str | None = None
    ) -> list[dict[str, Any]]:
        data = {"userId": user_id, "limit": limit}
        if since_id:
            data["sinceId"] = since_id
        return await self.make_read_request("chat/messages/user-timeline", data)

    async def get_room_messages(
        self, room_id: str, limit: int = 10, since_id: str | None = None
    ) -> list[dict[str, Any]]:
        data = {"roomId": room_id, "limit": limit}
        if since_id:
            data["sinceId"] = since_id
        return await self.make_read_request("chat/messages/room-timeline", data)
