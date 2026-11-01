from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from support.fakes import FakeMisskeyServer

from twipsybot.plugin.services import BotControlAdapter, OpenAIServiceAdapter


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"bot.system_prompt": None}, ("", None, None)),
        (
            {
                "bot.system_prompt": "be brief",
                "bot.max_tokens": 64,
                "bot.temperature": 0.5,
            },
            ("be brief", 64, 0.5),
        ),
    ],
)
def test_openai_adapter_reads_generation_settings_from_config(
    values: dict[str, Any], expected: tuple[str, int | None, float | None]
) -> None:
    config = SimpleNamespace(get=lambda key, default=None: values.get(key, default))
    adapter = OpenAIServiceAdapter(SimpleNamespace(), config)

    actual = (adapter.system_prompt, adapter.max_tokens, adapter.temperature)

    assert actual == expected


async def _hold(lock: Any) -> None:
    async with lock:
        pass


async def test_actor_lock_serializes_only_the_same_actor() -> None:
    adapter = BotControlAdapter(SimpleNamespace())

    async with adapter.actor_lock("u1", "alice"):
        async with asyncio.timeout(0.5), adapter.actor_lock("u2", "bob"):
            pass
        blocked = asyncio.wait_for(_hold(adapter.actor_lock("u1", "alice")), 0.05)
        with pytest.raises(TimeoutError):
            await blocked


async def test_plugin_storage_is_namespaced_per_plugin(boot) -> None:
    bot = await boot(one="pass", two="pass")
    one = bot.plugin_manager.plugins["one"].context.storage
    two = bot.plugin_manager.plugins["two"].context.storage
    await one.set("key", "1")
    await one.set("other", "1")
    await two.set("key", "2")

    assert (await one.get("key"), await two.get("key")) == ("1", "2")
    assert await bot.db.get_plugin_data("one", "key") == "1"
    assert await one.delete("other") == 1
    assert await one.delete() == 1
    assert await one.get("key") is None
    assert await two.get("key") == "2"


async def test_plugin_context_hides_internals_and_isolates_antennas(
    boot, misskey_server: FakeMisskeyServer
) -> None:
    bot = await boot(context="pass")
    context = bot.plugin_manager.plugins["context"].context
    antenna = {"id": "antenna-1", "name": "original"}
    misskey_server.set_response("antennas/list", lambda payload: [antenna])

    assert not hasattr(context.misskey, "access_token")
    assert not hasattr(context.openai, "client")
    with pytest.raises(TypeError):
        context.config["enabled"] = False

    antennas = await context.misskey.list_antennas()
    antennas[0]["name"] = "changed"

    assert (await context.misskey.list_antennas())[0]["name"] == "original"
    assert antenna["name"] == "original"
