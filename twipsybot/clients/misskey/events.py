import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple

from cachetools import TTLCache
from loguru import logger

from ...shared.logs import maybe_log_event_dump
from .models import (
    CHAT_CHANNELS,
    NOTE_CHANNELS,
    ChannelType,
    MessageEvent,
    parse_mention,
    parse_message,
    parse_notification,
    parse_timeline_note,
)

if TYPE_CHECKING:
    from .stream import StreamingClient

EventHandler = Callable[[Any], Awaitable[Any] | Any]

_CHAT_IDLE_SECONDS = 120
_WORKERS = 24
_QUEUE_MAX = 1000
_QUEUE_PUT_TIMEOUT = 1.0
_DEDUP_CACHE_MAX = 2000
_DEDUP_CACHE_TTL = 600


class Incoming(NamedTuple):
    key: str
    event: Any
    channel_id: str


_ROUTES: dict[tuple[str, str], tuple[str, Callable[[Any], Any]]] = {
    ("main", "mention"): ("mention", parse_mention),
    ("main", "reply"): ("mention", partial(parse_mention, kind="reply")),
    ("main", "notification"): ("notification", parse_notification),
    ("main", "unreadNotification"): ("notification", parse_notification),
    ("main", "newChatMessage"): ("message", parse_message),
    ("chat", "message"): ("message", parse_message),
    ("note", "note"): ("note", parse_timeline_note),
}


def _category(channel_name: str) -> str:
    if channel_name in CHAT_CHANNELS:
        return "chat"
    return "note" if channel_name in NOTE_CHANNELS else channel_name


def _dedup_key(channel_name: str, event_type: str, event_id: str | None) -> str | None:
    if not event_id:
        return None
    match event_type:
        case "note":
            return f"note:{channel_name}:{event_id}"
        case "newChatMessage" | "message":
            return f"chatMessage:{event_id}"
        case "unreadNotification":
            return f"notification:{event_id}"
        case _:
            return f"{event_type}:{event_id}"


class EventRouter:
    def __init__(self, client: "StreamingClient"):
        self._client = client
        self.handlers: dict[str, list[EventHandler]] = {}
        self.processed: TTLCache[str, bool] = TTLCache(
            maxsize=_DEDUP_CACHE_MAX,
            ttl=_DEDUP_CACHE_TTL,
            timer=time.monotonic,
        )
        self.queue: asyncio.Queue[tuple[str, Incoming] | None] = asyncio.Queue(
            maxsize=_QUEUE_MAX
        )
        self.worker_count = _WORKERS
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

    def _chat_body(self, body: Any) -> Any:
        if not isinstance(body, dict) or not isinstance(
            user_id := body.get("fromUserId"), str
        ):
            return body
        if isinstance(user := body.get("fromUser"), dict):
            if not body.get("toRoomId"):
                self._chat_users[user_id] = user
        elif user_id in self._chat_users:
            return {**body, "fromUser": self._chat_users[user_id]}
        return body

    def _decode(
        self, channel_id: str, channel_name: str, route: tuple[str, Callable], body: Any
    ) -> Incoming | None:
        key, parse = route
        if key == "message":
            body = self._chat_body(body)
        elif key == "note" and isinstance(body, dict):
            body = {**body, "streamingChannel": channel_name}
        event = parse(body)
        return Incoming(key, event, channel_id) if event else None

    async def handle_channel_message(self, body: dict[str, Any]) -> None:
        channel_id = body.get("id")
        event_type = body.get("type")
        if channel_id not in self._client.channels:
            self._dump(f"unknown/{event_type}", body)
            logger.debug(f"Message received for unknown channel: {channel_id}")
            return
        channel_name = self._client.channels[channel_id].get("name", "unknown")
        self._dump(f"{channel_name}/{event_type}", body)
        if not isinstance(event_type, str) or not event_type:
            logger.debug(
                f"Received {channel_name} data without standard event type; "
                f"skipping (channel_id={channel_id})"
            )
            return
        route = _ROUTES.get((_category(channel_name), event_type))
        if route is None:
            logger.debug(f"Skipping unsupported {channel_name} event: {event_type}")
            return
        incoming = self._decode(channel_id, channel_name, route, body.get("body"))
        if incoming is None:
            logger.warning(f"Invalid {channel_name} {event_type} event; skipping")
            return
        key = _dedup_key(channel_name, event_type, incoming.event.id)
        if key and key in self.processed:
            logger.debug(f"Duplicate event detected; skipping - {key}")
            return
        logger.debug(
            f"Received {channel_name} event: {event_type} (channel_id={channel_id})"
        )
        if await self.enqueue(channel_name, incoming) and key:
            self.processed[key] = True

    async def enqueue(self, channel_name: str, incoming: Incoming) -> bool:
        try:
            async with asyncio.timeout(_QUEUE_PUT_TIMEOUT):
                await self.queue.put((channel_name, incoming))
            return True
        except TimeoutError:
            logger.warning(
                f"Event queue congested; dropping event: {incoming.key} "
                f"(id={incoming.event.id or 'unknown'})"
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

    async def dispatch(self, channel_name: str, incoming: Incoming) -> None:
        key, event, channel_id = incoming
        if key != "message":
            await self.call_handlers(key, event)
        elif channel_name == ChannelType.MAIN:
            await self.handle_main_chat_message(event)
        else:
            await self._handle_chat_message(event, channel_id)

    async def handle_main_chat_message(self, message: MessageEvent) -> None:
        channel_id = None
        if room_id := message.room_id:
            channel_id = await self.open_chat_channel(
                ChannelType.CHAT_ROOM, {"roomId": room_id}
            )
        elif other_id := message.user.id:
            channel_id = await self.open_chat_channel(
                ChannelType.CHAT_USER, {"otherId": other_id}
            )
        await self._handle_chat_message(message, channel_id)

    async def _handle_chat_message(
        self, message: MessageEvent, channel_id: str | None
    ) -> None:
        if channel_id:
            await self._client.send_channel_message(
                channel_id, "read", {"id": message.id}
            )
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

    async def call_handlers(self, event_type: str, data: Any) -> None:
        for handler in self.handlers.get(event_type, []):
            try:
                result = handler(data)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.exception(f"Event handler failed ({event_type}): {e}")

    def _dump(self, kind: str, payload: Any) -> None:
        maybe_log_event_dump(self._client.log_dump_events, kind=kind, payload=payload)
