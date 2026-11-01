from __future__ import annotations

import asyncio

import pytest
from support.events import message

import twipsybot.plugin.manager as manager_module

_EVENT = message({"id": "message-1", "user": {"id": "user-1", "username": "alice"}})


async def test_handled_result_short_circuits_later_plugins(boot) -> None:
    bot = await boot(
        first="async def on_message(self, event):\n    return self.handled('first')\n",
        second="async def on_message(self, event):\n"
        "    await self.context.storage.set('called', 'yes')\n"
        "    return self.handled('second')\n",
    )

    results = await bot.plugin_manager.call_plugin_hook("on_message", _EVENT)

    assert results == [("first", {"handled": True, "response": "first"})]
    assert await bot.db.get_plugin_data("second", "called") is None


async def test_hook_timeout_and_exception_are_isolated(
    monkeypatch: pytest.MonkeyPatch, boot
) -> None:
    monkeypatch.setattr(manager_module, "_PLUGIN_HOOK_TIMEOUT_SECONDS", 0.01)
    bot = await boot(
        broken="async def on_message(self, event):\n"
        "    import asyncio\n"
        "    await asyncio.sleep(1)\n",
        error="async def on_message(self, event):\n    raise RuntimeError('boom')\n",
        healthy="async def on_message(self, event):\n    return self.handled('ok')\n",
    )

    results = await bot.plugin_manager.call_plugin_hook("on_message", _EVENT)

    assert results == [("healthy", {"handled": True, "response": "ok"})]


async def test_auto_post_plugins_share_trigger_time(boot) -> None:
    body = "async def on_auto_post(self, event):\n    self.triggered = event.triggered_at\n"
    bot = await boot(first=body, second=body)
    plugins = bot.plugin_manager.plugins

    await bot.plugin_manager.call_plugin_hook("on_auto_post")

    assert plugins["first"].triggered == plugins["second"].triggered


async def test_actor_lock_does_not_reenter_response_pipeline(boot) -> None:
    bot = await boot(
        actor="async def on_message(self, event):\n"
        "    async with self.context.bot.actor_lock(event.user.id, event.user.username):\n"
        "        return self.handled('ok')\n"
    )

    results = await asyncio.wait_for(
        bot.plugin_manager.call_plugin_hook("on_message", _EVENT), timeout=0.2
    )

    assert results == [("actor", {"handled": True, "response": "ok"})]
