import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from ...clients.misskey.antenna import (
    build_antenna_index,
    dedupe_non_empty,
    resolve_antenna_selector,
)
from ...clients.misskey.api import MisskeyAPI
from ...clients.misskey.channels import ChannelSpec, ChannelType
from ...clients.misskey.streaming import StreamingClient
from ...shared.config import Config
from ...shared.config_keys import ConfigKeys
from ...shared.utils import normalize_tokens
from .runtime import BotRuntime


class StreamingConnector:
    def __init__(
        self,
        *,
        config: Config,
        misskey: MisskeyAPI,
        streaming: StreamingClient,
        runtime: BotRuntime,
        on_mention: Callable[[dict[str, Any]], Awaitable[None]],
        on_message: Callable[[dict[str, Any]], Awaitable[None]],
        on_notification: Callable[[dict[str, Any]], Awaitable[None]],
        on_timeline_note: Callable[[dict[str, Any]], Awaitable[Any]],
    ):
        self._config = config
        self._misskey = misskey
        self._streaming = streaming
        self._runtime = runtime
        self._on_mention = on_mention
        self._on_message = on_message
        self._on_notification = on_notification
        self._on_timeline_note = on_timeline_note
        self._timeline_channels = self._load_timeline_channels()

    def _load_timeline_channels(self) -> set[str]:
        mapping = {
            ConfigKeys.BOT_TIMELINE_HOME: ChannelType.HOME_TIMELINE.value,
            ConfigKeys.BOT_TIMELINE_LOCAL: ChannelType.LOCAL_TIMELINE.value,
            ConfigKeys.BOT_TIMELINE_HYBRID: ChannelType.HYBRID_TIMELINE.value,
            ConfigKeys.BOT_TIMELINE_GLOBAL: ChannelType.GLOBAL_TIMELINE.value,
        }
        return {channel for key, channel in mapping.items() if self._config.get(key)}

    def load_antenna_selectors(self) -> list[str]:
        return normalize_tokens(self._config.get(ConfigKeys.BOT_TIMELINE_ANTENNA_IDS))

    async def resolve_antenna_ids(self, selectors: list[str]) -> list[str]:
        normalized = [s.strip() for s in selectors if isinstance(s, str) and s.strip()]
        if not normalized:
            return []
        antennas = await self._misskey.list_antennas()
        antenna_ids, name_to_ids, _ = build_antenna_index(antennas)
        resolved: list[str] = []
        for selector in normalized:
            antenna_id, err = resolve_antenna_selector(
                selector, antenna_ids, name_to_ids
            )
            if err == "not_found":
                logger.warning(f"Antenna not found: {selector}")
            elif err == "ambiguous":
                logger.warning(f"Antenna name is ambiguous: {selector}")
            resolved.append(antenna_id)
        return dedupe_non_empty(resolved)

    async def get_streaming_channels(self) -> list[ChannelSpec]:
        active = {ChannelType.MAIN.value, *self._timeline_channels}
        ordered = [
            ChannelType.MAIN.value,
            ChannelType.HOME_TIMELINE.value,
            ChannelType.LOCAL_TIMELINE.value,
            ChannelType.HYBRID_TIMELINE.value,
            ChannelType.GLOBAL_TIMELINE.value,
        ]
        result: list[ChannelSpec] = [c for c in ordered if c in active]
        selectors = self.load_antenna_selectors()
        for antenna_id in await self.resolve_antenna_ids(selectors):
            result.append((ChannelType.ANTENNA.value, {"antennaId": antenna_id}))
        return result

    async def setup_streaming(self) -> None:
        try:
            self._streaming.on_mention(self._on_mention)
            self._streaming.on_message(self._on_message)
            self._streaming.on_notification(self._on_notification)
            self._streaming.on_note(self._on_timeline_note)
            channels = await self.get_streaming_channels()
            await self._streaming.connect_once(channels)
            self._runtime.add_task("streaming", self._streaming.connect(channels))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Failed to set up Streaming connection: {e}")
            raise
