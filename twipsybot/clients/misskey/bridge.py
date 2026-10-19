import asyncio
import json
import re
from collections import deque
from contextlib import suppress
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import aiohttp
from loguru import logger

from ...shared.consts import API_TIMEOUT, USER_AGENT
from ...shared.errors import WebSocketConnectionError, WebSocketReconnectError

_I_PARAM_RE = re.compile(r"([?&]i=)[^&#\s]+")
_I_JSON_RE = re.compile(r'("i"\s*:\s*")[^"]+(")')
_RECEIVE_TIMEOUT = 10
_SEND_BUFFER_MAX = 200
_CLOSED_TYPES = (
    aiohttp.WSMsgType.CLOSED,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.ERROR,
)


def _redact_access_token(text: str) -> str:
    return _I_JSON_RE.sub(r"\1***\2", _I_PARAM_RE.sub(r"\1***", text))


class TCPClient:
    def __init__(self) -> None:
        self.__session: aiohttp.ClientSession | None = None
        self.__connector: aiohttp.TCPConnector | None = None

    @property
    def _connector(self) -> aiohttp.TCPConnector:
        if self.__connector is None or self.__connector.closed:
            self.__connector = aiohttp.TCPConnector()
        return self.__connector

    @property
    def session(self) -> aiohttp.ClientSession:
        if self.__session is None or self.__session.closed:
            self.__session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                connector=self._connector,
                connector_owner=True,
            )
        return self.__session

    async def close_session(self, *, silent: bool = False) -> None:
        if self.__session and not self.__session.closed:
            try:
                await self.__session.close()
            except Exception as e:
                logger.warning(f"Error closing session: {e}")
        if self.__connector and not self.__connector.closed:
            try:
                await self.__connector.close()
            except Exception as e:
                logger.warning(f"Error closing connector: {e}")
        self.__session = self.__connector = None
        if not silent:
            logger.debug("TCP session closed")

    async def ws_connect(self, url: str) -> aiohttp.ClientWebSocketResponse:
        try:
            return await self.session.ws_connect(url)
        except aiohttp.ClientConnectorError as e:
            logger.error(f"TCP client connection failed: {e}")
            raise WebSocketConnectionError() from e


class StreamingSocket:
    def __init__(self, instance_url: str, access_token: str, transport: TCPClient):
        self.instance_url = instance_url
        self.access_token = access_token
        self.transport = transport
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.buffer: deque[dict[str, Any]] = deque(maxlen=_SEND_BUFFER_MAX)
        self._overflow_warned = False
        self._ws_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._connect_task: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        return self.ws is not None and not self.ws.closed

    def buffer_outgoing(self, message: dict[str, Any]) -> None:
        if len(self.buffer) == self.buffer.maxlen and not self._overflow_warned:
            logger.warning("WebSocket send buffer full; dropping oldest messages")
            self._overflow_warned = True
        self.buffer.append(message)

    def clear_buffer(self) -> None:
        self.buffer.clear()
        self._overflow_warned = False

    async def send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            if (ws := self.ws) is None or ws.closed:
                self.buffer_outgoing(message)
                return
            try:
                await ws.send_json(message)
            except (aiohttp.ClientError, OSError) as e:
                self.buffer_outgoing(message)
                await self._drop(e)

    async def send_control(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            if (ws := self.ws) is None or ws.closed:
                raise WebSocketReconnectError()
            try:
                await ws.send_json(message)
            except (aiohttp.ClientError, OSError) as e:
                await self._drop(e)
                raise WebSocketReconnectError() from e

    async def _drop(self, error: Exception) -> None:
        await self.close()
        logger.debug(
            f"WebSocket send failed; reconnecting: {_redact_access_token(str(error))}"
        )

    async def flush(self) -> None:
        while self.buffer and self.available:
            await self.send_control(self.buffer.popleft())
        if not self.buffer:
            self._overflow_warned = False

    async def connect(self) -> None:
        async with self._ws_lock:
            if self.available:
                return
            task = self._connect_task
            if task is None or task.done():
                task = asyncio.create_task(self._open(), name="stream-ws-connect")
                self._connect_task = task
        try:
            await task
        finally:
            if task.done():
                async with self._ws_lock:
                    if self._connect_task is task:
                        self._connect_task = None

    async def _open(self) -> None:
        raw = self.instance_url.strip().rstrip("/")
        parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
        scheme = parsed.scheme.lower()
        if scheme not in {"https", "http"}:
            raise ValueError("Unsupported instance URL scheme")
        base_url = urlunsplit(
            (
                "wss" if scheme == "https" else "ws",
                parsed.netloc,
                parsed.path.rstrip("/"),
                "",
                "",
            )
        ).rstrip("/")
        safe_url = f"{base_url}/streaming"
        try:
            ws = await self.transport.ws_connect(
                f"{safe_url}?{urlencode({'i': self.access_token})}"
            )
            async with self._ws_lock:
                if self.available:
                    with suppress(Exception):
                        await ws.close()
                    return
                self.ws = ws
            logger.debug(f"WebSocket connected: {safe_url}")
        except WebSocketConnectionError:
            await self.close()
            raise
        except (aiohttp.ClientError, OSError) as e:
            await self.close()
            logger.error(f"WebSocket connection failed: {_redact_access_token(str(e))}")
            raise WebSocketConnectionError() from e

    async def receive(self) -> Any:
        if (ws := self.ws) is None or ws.closed:
            raise WebSocketReconnectError()
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=_RECEIVE_TIMEOUT)
        except TimeoutError:
            return None
        except (aiohttp.ClientError, OSError) as e:
            raise WebSocketReconnectError() from e
        if msg.type in _CLOSED_TYPES:
            raise WebSocketReconnectError()
        if msg.type != aiohttp.WSMsgType.TEXT:
            return None
        return json.loads(msg.data)

    async def close(self) -> None:
        async with self._ws_lock:
            ws, self.ws = self.ws, None
            if ws is not None and not ws.closed:
                with suppress(Exception):
                    await ws.close()
