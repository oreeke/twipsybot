from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from ...clients.misskey.api import MisskeyAPI
from ...clients.misskey.channels import NOTE_CHANNELS, ChannelSpec, ChannelType
from ...clients.misskey.stream import StreamingClient
from ...shared.config import Config
from ...shared.keys import ConfigKeys
from ...shared.utils import normalize_tokens
from .runtime import BotRuntime


def _match_antenna(selector: str, antennas: list[tuple[str, str]]) -> list[str]:
    if any(antenna_id == selector for antenna_id, _ in antennas):
        return [selector]
    if exact := [antenna_id for antenna_id, name in antennas if name == selector]:
        return exact
    lowered = selector.lower()
    return list(
        dict.fromkeys(
            antenna_id
            for antenna_id, name in antennas
            if name and name.lower() == lowered
        )
    )


def _split(spec: ChannelSpec) -> tuple[str, dict[str, Any]]:
    return (spec, {}) if isinstance(spec, str) else spec


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

    def _load_timeline_channels(self) -> set[str]:
        mapping = {
            ConfigKeys.TIMELINE_HOME: ChannelType.HOME_TIMELINE.value,
            ConfigKeys.TIMELINE_LOCAL: ChannelType.LOCAL_TIMELINE.value,
            ConfigKeys.TIMELINE_HYBRID: ChannelType.HYBRID_TIMELINE.value,
            ConfigKeys.TIMELINE_GLOBAL: ChannelType.GLOBAL_TIMELINE.value,
        }
        return {channel for key, channel in mapping.items() if self._config.get(key)}

    def load_antenna_selectors(self) -> list[str]:
        return normalize_tokens(self._config.get(ConfigKeys.TIMELINE_ANTENNAS))

    async def resolve_antenna_ids(self, selectors: list[str]) -> list[str]:
        selectors = [s.strip() for s in selectors if isinstance(s, str) and s.strip()]
        if not selectors:
            return []
        antennas = [
            (a["id"], a["name"].strip() if isinstance(a.get("name"), str) else "")
            for a in await self._misskey.list_antennas()
            if isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"]
        ]
        resolved: list[str] = []
        for selector in selectors:
            match _match_antenna(selector, antennas):
                case [antenna_id]:
                    resolved.append(antenna_id)
                case []:
                    logger.warning(f"Antenna not found: {selector}")
                case _:
                    logger.warning(f"Antenna name is ambiguous: {selector}")
        return list(dict.fromkeys(resolved))

    async def get_streaming_channels(self) -> list[ChannelSpec]:
        active = {ChannelType.MAIN.value, *self._load_timeline_channels()}
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

    async def sync_timelines(self) -> None:
        if not self._streaming.running:
            return
        wanted = {
            (name, frozenset(params.items())): (name, params)
            for name, params in map(_split, await self.get_streaming_channels())
            if name in NOTE_CHANNELS
        }
        for channel_id, info in tuple(self._streaming.channels.items()):
            key = (info["name"], frozenset(info["params"].items()))
            if info["name"] in NOTE_CHANNELS and key not in wanted:
                await self._streaming.disconnect_channel_id(channel_id)
        for name, params in wanted.values():
            try:
                await self._streaming.connect_channel(name, params)
            except Exception as e:
                logger.warning(f"Failed to subscribe {name} {params}: {e}")

    async def setup_streaming(self) -> None:
        try:
            for event, handler in (
                ("mention", self._on_mention),
                ("message", self._on_message),
                ("notification", self._on_notification),
                ("note", self._on_timeline_note),
            ):
                self._streaming.events.on(event, handler)
            channels = await self.get_streaming_channels()
            await self._streaming.connect_once(channels)
            self._runtime.add_task("streaming", self._streaming.connect(channels))
        except Exception as e:
            logger.exception(f"Failed to set up Streaming connection: {e}")
            raise
