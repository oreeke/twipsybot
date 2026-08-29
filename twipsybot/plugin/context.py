from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import BotControl, MisskeyService, OpenAIService, PluginStorage

__all__ = ("PluginContext",)


@dataclass(frozen=True, slots=True)
class PluginContext:
    name: str
    config: Mapping[str, Any]
    storage: PluginStorage
    misskey: MisskeyService
    openai: OpenAIService
    bot: BotControl
