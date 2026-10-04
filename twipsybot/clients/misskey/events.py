import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from cachetools import TTLCache
from loguru import logger

from ...shared.constants import (
    STREAM_DEDUP_CACHE_MAX,
    STREAM_DEDUP_CACHE_TTL,
    STREAM_QUEUE_MAX,
    STREAM_QUEUE_PUT_TIMEOUT,
    STREAM_WORKERS,
)
from ...shared.utils import maybe_log_event_dump
from .channels import CHAT_CHANNELS, NOTE_CHANNELS, ChannelType

if TYPE_CHECKING:
    from .streaming import StreamingClient

__all__ = ("EventHandler", "EventRouter")

EventHandler = Callable[[dict[str, Any]], Awaitable[Any] | Any]

_CHAT_IDLE_SECONDS = 120


def _wrap(event_type: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    wrapped: dict[str, Any] = {"type": event_type, key: payload}
    if isinstance(event_id := payload.get("id"), str) and event_id:
        wrapped["id"] = event_id
    return wrapped


def _normalize(
    channel_name: str, event_type: str, body: Any
) -> tuple[str, dict[str, Any]]:
    if isinstance(body, dict):
        if channel_name == ChannelType.MAIN:
            match event_type:
                case "mention" | "reply":
                    return event_type, _wrap(event_type, "note", body)
                case "notification" | "unreadNotification":
                    return "notification", _wrap("notification", "notification", body)
                case "newChatMessage":
                    return event_type, {**body, "type": event_type}
        elif channel_name in CHAT_CHANNELS and event_type == "message":
            return event_type, {"type": event_type, **body}
    return event_type, {"type": event_type, "body": body}


def _dedup_key(event: dict[str, Any], event_type: str, channel_name: str) -> str | None:
    event_id = event.get("id")
    if not event_id and event_type == "note" and isinstance(event.get("body"), dict):
        event_id = event["body"].get("id")
    if not isinstance(event_id, str) or not event_id:
        return None
    if event_type == "note":
        return f"note:{channel_name}:{event_id}"
    if event_type in {"newChatMessage", "message"}:
        return f"chatMessage:{event_id}"
    return f"{event_type}:{event_id}"


class EventRouter:
    def __init__(self, client: "StreamingClient"):
        self._client = client
        self.handlers: dict[str, list[EventHandler]] = {}
        self.processed: TTLCache[str, bool] = TTLCache(
            maxsize=STREAM_DEDUP_CACHE_MAX,
            ttl=STREAM_DEDUP_CACHE_TTL,
            timer=time.monotonic,
        )
        self.queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue(
            maxsize=STREAM_QUEUE_MAX
        )
        self.worker_count = STREAM_WORKERS
        self.workers: list[asyncio.Task[None]] = []
        self.chat_tasks: dict[str, asyncio.Task[None]] = {}
        self._chat_users: dict[str, dict[str, Any]] = {}
        self._busy = 0

    def on(self, event_type: str, handler: EventHandler) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    def status(self) -> dict[str, int]:
        return {
            "queue_size": self.queue.qsize(),
            "queue_capacity": self.queue.maxsize,
            "busy_workers": self._busy,
            "workers_alive": sum(not worker.done() for worker in self.workers),
            "workers_total": self.worker_count,
        }

    def start(self) -> None:
        if not self.workers:
            self.workers = [
                asyncio.create_task(self.worker_loop(), name=f"stream-worker-{i}")
                for i in range(self.worker_count)
            ]

    async def stop(self) -> None:
        if not self.workers:
            return
        while not self.queue.empty():
            self.queue.get_nowait()
        for _ in self.workers:
            await self.queue.put(None)
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    def cancel_chat_tasks(self) -> None:
        tasks = list(self.chat_tasks.values())
        self.chat_tasks.clear()
        self._chat_users.clear()
        for task in tasks:
            task.cancel()

    async def handle_channel_message(self, body: dict[str, Any]) -> None:
        channel_id = body.get("id")
        if channel_id not in self._client.channels:
            logger.debug(f"Message received for unknown channel: {channel_id}")
            return
        channel_name = self._client.channels[channel_id].get("name", "unknown")
        event_type = body.get("type")
        if not isinstance(event_type, str) or not event_type:
            logger.debug(
                f"Received {channel_name} data without standard event type; "
                f"skipping (channel_id={channel_id})"
            )
            self._dump(channel_name, body)
            return
        payload = body.get("body")
        event_type, event = _normalize(
            channel_name, event_type, {} if payload is None else payload
        )
        event.setdefault("streamingChannelId", channel_id)
        key = _dedup_key(event, event_type, channel_name)
        if key and key in self.processed:
            logger.debug(f"Duplicate event detected; skipping - {key}")
            return
        logger.debug(
            f"Received {channel_name} event: {event_type} (channel_id={channel_id})"
        )
        if await self.enqueue(channel_name, event) and key:
            self.processed[key] = True

    async def enqueue(self, channel_name: str, event: dict[str, Any]) -> bool:
        try:
            async with asyncio.timeout(STREAM_QUEUE_PUT_TIMEOUT):
                await self.queue.put((channel_name, event))
            return True
        except TimeoutError:
            logger.warning(
                f"Event queue congested; dropping event: {event.get('type')} "
                f"(id={event.get('id', 'unknown')})"
            )
            return False

    async def worker_loop(self) -> None:
        while item := await self.queue.get():
            self._busy += 1
            try:
                await self.dispatch(*item)
            except Exception as e:
                logger.exception(f"Failed to process event: {e}")
            finally:
                self._busy -= 1

    async def dispatch(self, channel_name: str, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if channel_name == ChannelType.MAIN:
            match event_type:
                case "newChatMessage":
                    return await self.handle_main_chat_message(event)
                case "notification":
                    return await self.call_handlers("notification", event)
                case "mention" | "reply":
                    return await self.call_handlers("mention", event)
        elif channel_name in CHAT_CHANNELS and event_type == "message":
            return await self._handle_chat_message(event)
        elif channel_name in NOTE_CHANNELS and event_type == "note":
            return await self._handle_note(channel_name, event)
        logger.debug(f"Unknown {channel_name} channel event type: {event_type}")
        self._dump(event_type or channel_name, event)

    async def handle_main_chat_message(self, event: dict[str, Any]) -> None:
        channel_id = None
        if isinstance(room_id := event.get("toRoomId"), str) and room_id:
            channel_id = await self.open_chat_channel(
                ChannelType.CHAT_ROOM, {"roomId": room_id}
            )
        elif isinstance(other_id := event.get("fromUserId"), str) and other_id:
            if isinstance(from_user := event.get("fromUser"), dict):
                self._chat_users[other_id] = from_user
            channel_id = await self.open_chat_channel(
                ChannelType.CHAT_USER, {"otherId": other_id}
            )
        message = {**event, "type": "message"}
        if channel_id:
            message["streamingChannelId"] = channel_id
        await self._handle_chat_message(message)

    async def _handle_chat_message(self, event: dict[str, Any]) -> None:
        message = dict(event)
        if (
            "fromUser" not in message
            and isinstance(user_id := message.get("fromUserId"), str)
            and user_id in self._chat_users
        ):
            message["fromUser"] = self._chat_users[user_id]
        channel_id = message.get("streamingChannelId")
        msg_id = message.get("id")
        if (
            channel_id
            and isinstance(channel_id, str)
            and isinstance(msg_id, str)
            and msg_id
        ):
            await self._client.send_channel_message(channel_id, "read", {"id": msg_id})
            if channel_id in self.chat_tasks:
                self.schedule_chat_disconnect(channel_id)
        await self.call_handlers("message", message)

    async def open_chat_channel(
        self, channel: ChannelType, params: dict[str, str]
    ) -> str | None:
        try:
            channel_id = await self._client.connect_channel(channel, params)
        except Exception as e:
            logger.debug(f"Failed to connect {channel.value} channel {params}: {e}")
            return None
        self.schedule_chat_disconnect(channel_id)
        return channel_id

    def schedule_chat_disconnect(self, channel_id: str) -> None:
        if task := self.chat_tasks.get(channel_id):
            task.cancel()
        self.chat_tasks[channel_id] = asyncio.create_task(
            self._disconnect_chat_channel_later(channel_id),
            name=f"chat-disconnect-{channel_id}",
        )

    async def _disconnect_chat_channel_later(self, channel_id: str) -> None:
        try:
            await asyncio.sleep(_CHAT_IDLE_SECONDS)
            await self._client.disconnect_channel_id(channel_id)
        except Exception as e:
            logger.debug(f"Failed to disconnect chat channel {channel_id}: {e}")
        finally:
            if self.chat_tasks.get(channel_id) is asyncio.current_task():
                del self.chat_tasks[channel_id]

    async def _handle_note(self, channel_name: str, event: dict[str, Any]) -> None:
        body = event.get("body")
        note = dict(body) if isinstance(body, dict) else event
        note.setdefault("streamingChannel", channel_name)
        logger.debug(f"Received {channel_name} note: {note.get('id', 'unknown')}")
        self._dump(channel_name, note)
        await self.call_handlers("note", note)

    async def call_handlers(self, event_type: str, data: dict[str, Any]) -> None:
        for handler in self.handlers.get(event_type, []):
            try:
                result = handler(data)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.exception(f"Event handler failed ({event_type}): {e}")

    def _dump(self, kind: str, payload: Any) -> None:
        maybe_log_event_dump(self._client.log_dump_events, kind=kind, payload=payload)
