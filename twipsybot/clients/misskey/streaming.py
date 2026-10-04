import asyncio
import random
import uuid
from typing import Any

from loguru import logger

from ...shared.exceptions import WebSocketConnectionError, WebSocketReconnectError
from .channels import ChannelSpec, ChannelType
from .events import EventRouter
from .socket import StreamingSocket
from .transport import TCPClient

__all__ = ("StreamingClient",)

MAX_CHANNELS_PER_CONNECTION = 32
CHANNEL_CONNECT_TIMEOUT = 5.0


def _channel_name(spec: ChannelSpec) -> str:
    return spec[0] if isinstance(spec, tuple) else str(spec)


def _normalize_specs(channels: list[ChannelSpec] | None) -> list[ChannelSpec]:
    return [c for c in channels or [] if c and _channel_name(c)]


def _connect_message(channel_id: str, name: str, params: Any) -> dict[str, Any]:
    return {
        "type": "connect",
        "body": {"channel": name, "id": channel_id, "params": params, "pong": True},
    }


class StreamingClient:
    def __init__(
        self,
        instance_url: str,
        access_token: str,
        *,
        log_dump_events: bool = False,
        transport: TCPClient | None = None,
    ):
        self.socket = StreamingSocket(
            instance_url.rstrip("/"), access_token, transport or TCPClient()
        )
        self.events = EventRouter(self)
        self.log_dump_events = log_dump_events
        self.state = "initializing"
        self.running = False
        self.channels: dict[str, dict[str, Any]] = {}
        self._confirmed_channel_ids: set[str] = set()
        self._channel_confirmation_events: dict[str, asyncio.Event] = {}
        self._first_connection = True
        self._lifecycle_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.disconnect()
        await self.events.stop()
        await self.socket.close()
        await self.socket.transport.close_session(silent=True)
        logger.debug("Streaming client closed")

    async def connect(
        self, channels: list[ChannelSpec] | None = None, *, reconnect: bool = True
    ) -> None:
        await self.connect_once(
            _normalize_specs(channels), raise_on_error=not reconnect
        )
        retry_delay = 1.0
        while self.running:
            try:
                await self._listen_messages()
                return
            except WebSocketConnectionError:
                if not self.running:
                    return
                if not reconnect:
                    raise
                self.state = "reconnecting"
                delay = random.uniform(retry_delay / 2, retry_delay)
                logger.debug(f"WebSocket disconnected; reconnecting in {delay:.1f}s")
                try:
                    await self._reconnect_with_backoff(delay)
                    retry_delay = 1.0
                except WebSocketConnectionError:
                    retry_delay = min(retry_delay * 2, 30.0)

    async def connect_once(
        self,
        channels: list[ChannelSpec] | None = None,
        *,
        raise_on_error: bool = False,
    ) -> None:
        async with self._lifecycle_lock:
            if self.running:
                return
            self.running = True
            self.events.start()
            requested = _normalize_specs(channels)
            if ChannelType.MAIN not in map(_channel_name, requested):
                requested.insert(0, ChannelType.MAIN.value)
            for spec in requested:
                params = spec[1] if isinstance(spec, tuple) else None
                await self.connect_channel(_channel_name(spec), params)
            try:
                await self._connect_and_resubscribe()
            except WebSocketConnectionError:
                self.state = "reconnecting"
                if raise_on_error:
                    self.running = False
                    self.state = "disconnected"
                    await self.socket.close()
                    await self.events.stop()
                    raise
            if self._first_connection:
                logger.info("Streaming client started")
                self._first_connection = False

    async def disconnect(self) -> None:
        async with self._lifecycle_lock:
            self.running = False
            self.events.cancel_chat_tasks()
            for channel_id in self.channels:
                if self.socket.available:
                    try:
                        await self.socket.send_control(
                            {"type": "disconnect", "body": {"id": channel_id}}
                        )
                    except Exception as e:
                        logger.warning(f"Error disconnecting channel {channel_id}: {e}")
            self.channels.clear()
            self._reset_channel_confirmations()
            await self.socket.close()
            self.socket.clear_buffer()
            self.events.processed.clear()
            self.state = "disconnected"

    async def connect_channel(
        self, channel: ChannelType | str, params: dict[str, Any] | None = None
    ) -> str:
        name = channel.value if isinstance(channel, ChannelType) else str(channel)
        if not name:
            raise ValueError("channel name must not be empty")
        params = params or {}
        for channel_id, info in self.channels.items():
            if info.get("name") == name and info.get("params") == params:
                logger.debug(f"Channel {name} already connected: {channel_id}")
                if (
                    self.socket.available
                    and channel_id not in self._confirmed_channel_ids
                ):
                    await self._wait_for_channel_confirmation(channel_id, name)
                return channel_id
        if len(self.channels) >= MAX_CHANNELS_PER_CONNECTION:
            raise WebSocketConnectionError(
                f"Misskey allows at most {MAX_CHANNELS_PER_CONNECTION} "
                "channels per connection"
            )
        channel_id = str(uuid.uuid4())
        self.channels[channel_id] = {"name": name, "params": params}
        if self.socket.available:
            self._channel_confirmation_events[channel_id] = asyncio.Event()
            await self.socket.send_control(_connect_message(channel_id, name, params))
            try:
                await self._wait_for_channel_confirmation(channel_id, name)
            except BaseException:
                self.channels.pop(channel_id, None)
                self._channel_confirmation_events.pop(channel_id, None)
                raise
        logger.debug(f"Connected channel: {name} (ID: {channel_id})")
        return channel_id

    async def disconnect_channel_id(self, channel_id: str) -> None:
        if not channel_id:
            return
        if channel_id in self.channels and self.socket.available:
            try:
                await self.socket.send_control(
                    {"type": "disconnect", "body": {"id": channel_id}}
                )
            except WebSocketConnectionError:
                pass
        self.channels.pop(channel_id, None)
        self._confirmed_channel_ids.discard(channel_id)
        if event := self._channel_confirmation_events.pop(channel_id, None):
            event.set()

    async def send_channel_message(
        self, channel_id: str, event_type: str, body: dict[str, Any]
    ) -> None:
        await self.socket.send(
            {"type": "ch", "body": {"id": channel_id, "type": event_type, "body": body}}
        )

    async def _wait_for_channel_confirmation(
        self, channel_id: str, channel_name: str
    ) -> None:
        event = self._channel_confirmation_events.setdefault(
            channel_id, asyncio.Event()
        )
        try:
            async with asyncio.timeout(CHANNEL_CONNECT_TIMEOUT):
                await event.wait()
        except TimeoutError:
            raise WebSocketConnectionError(
                f"Misskey did not confirm channel: {channel_name}"
            ) from None
        if channel_id not in self._confirmed_channel_ids:
            raise WebSocketReconnectError()

    def _confirm_channel(self, channel_id: str) -> None:
        if channel_id not in self.channels:
            return
        self._confirmed_channel_ids.add(channel_id)
        if event := self._channel_confirmation_events.pop(channel_id, None):
            event.set()

    def _reset_channel_confirmations(self) -> None:
        self._confirmed_channel_ids.clear()
        events = tuple(self._channel_confirmation_events.values())
        self._channel_confirmation_events.clear()
        for event in events:
            event.set()

    async def _connect_and_resubscribe(self) -> None:
        await self.socket.connect()
        self._reset_channel_confirmations()
        await self._resubscribe_channels()
        await self.socket.flush()
        self.state = "connected"

    async def _resubscribe_channels(self) -> None:
        for channel_id, info in self.channels.items():
            if isinstance(name := info.get("name"), str) and name:
                await self.socket.send_control(
                    _connect_message(channel_id, name, info.get("params") or {})
                )

    async def _reconnect_with_backoff(self, delay_seconds: float) -> None:
        await self.socket.close()
        await asyncio.sleep(delay_seconds)
        await self._connect_and_resubscribe()

    async def _listen_messages(self) -> None:
        while self.running:
            try:
                if (data := await self.socket.receive()) is not None:
                    await self._process_message(data)
            except (ValueError, TypeError, AttributeError, KeyError) as e:
                logger.error(f"Failed to parse message: {e}")

    async def _process_message(self, data: Any) -> None:
        if not data or not isinstance(data, dict):
            logger.debug(f"Invalid message format; skipping: {data}")
            return
        message_type = data.get("type")
        body = data.get("body", {})
        if message_type == "channel":
            await self.events.handle_channel_message(body)
        elif message_type == "connected" and isinstance(body, dict):
            if isinstance(channel_id := body.get("id"), str):
                self._confirm_channel(channel_id)
        else:
            logger.debug(f"Unknown message type received: {message_type}")
