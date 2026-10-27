from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from support.events import message
from support.fakes import FakeMisskeyServer
from support.types import MakeBot, MakePluginDir, WriteConfig

import twipsybot.bot.engine.pipeline as pipeline_module
from twipsybot.plugin import tool

_SOURCE = """
import asyncio
from typing import Annotated

from pydantic import Field
from twipsybot.plugin import PLUGIN_API_VERSION, PluginBase, tool


class ToolPlugin(PluginBase):
    api_version = PLUGIN_API_VERSION

    @tool("Repeat text")
    async def echo(
        self,
        event,
        text: Annotated[str, Field(description="what to repeat")],
        times: int = 1,
    ):
        async def later():
            return self.handled("later", "file-2")

        return self.handled(text * times, "file-1", later)

    @tool("Admin only", access="admin")
    async def secret(self, event):
        return "secret"

    @tool("Reads the web", external=True)
    async def page(self, event):
        return "page"

    @tool("Writes outward", effect=True)
    async def send(self, event):
        return "sent"

    @tool("Followers only", access="followers")
    async def members(self, event):
        return "members"

    @tool("Fails")
    async def boom(self, event):
        raise RuntimeError("boom")

    @tool("Hangs")
    async def hang(self, event):
        await asyncio.sleep(10)

    @tool("Returns junk")
    async def junk(self, event):
        return {"unexpected": True}


plugin = ToolPlugin
"""


def _event(user_id: str = "user-1") -> Any:
    return message(
        {"id": "m1", "text": "hi", "user": {"id": user_id, "username": "alice"}}
    )


async def _bot(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> Any:
    plugins_dir = make_plugin_dir("kit", _SOURCE)
    return await make_bot(
        write_config(bot={"admins": ["user-1"]}), plugins_dir=plugins_dir
    )


async def _toolbox(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    user_id: str = "user-1",
) -> Any:
    bot = await _bot(make_bot, make_plugin_dir, write_config)
    return await bot.pipeline.toolbox(_event(user_id))


def _names(toolbox: Any) -> list[str]:
    return [s["name"] for s in toolbox.specs]


async def test_toolbox_filters_tools_by_access(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    misskey_server.set_response(
        "users/show", lambda payload: {"isFollowed": payload["userId"] == "user-3"}
    )
    bot = await _bot(make_bot, make_plugin_dir, write_config)

    admin = await bot.pipeline.toolbox(_event("user-1"))
    follower = await bot.pipeline.toolbox(_event("user-3"))
    guest = await bot.pipeline.toolbox(_event("user-2"))

    assert _names(admin) == [
        "note_publish",
        "kit_boom",
        "kit_echo",
        "kit_hang",
        "kit_junk",
        "kit_members",
        "kit_page",
        "kit_secret",
        "kit_send",
    ]
    assert _names(follower) == [
        "kit_boom",
        "kit_echo",
        "kit_hang",
        "kit_junk",
        "kit_members",
        "kit_page",
        "kit_send",
    ]
    assert _names(guest) == [
        "kit_boom",
        "kit_echo",
        "kit_hang",
        "kit_junk",
        "kit_page",
        "kit_send",
    ]
    assert {c["userId"] for c in misskey_server.calls["users/show"]} == {
        "user-2",
        "user-3",
    }
    echo = next(s for s in admin.specs if s["name"] == "kit_echo")
    assert echo["description"] == "Repeat text"
    assert echo["parameters"] == {
        "type": "object",
        "properties": {
            "text": {"description": "what to repeat", "type": "string"},
            "times": {"default": 1, "type": "integer"},
        },
        "required": ["text"],
        "additionalProperties": False,
    }


async def test_follower_lookup_is_cached_and_fails_closed(
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
    misskey_server: FakeMisskeyServer,
) -> None:
    misskey_server.set_response("users/show", lambda payload: {"isFollowed": True})
    bot = await _bot(make_bot, make_plugin_dir, write_config)

    for _ in range(2):
        assert "kit_members" in _names(await bot.pipeline.toolbox(_event("user-3")))
    assert misskey_server.calls["users/show"] == [{"userId": "user-3"}]

    bot.admin._followers.clear()
    bot.misskey.show_user = AsyncMock(side_effect=RuntimeError("down"))

    assert "kit_members" not in _names(await bot.pipeline.toolbox(_event("user-3")))


async def test_reading_external_content_disables_effect_tools(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    toolbox = await _toolbox(make_bot, make_plugin_dir, write_config)
    assert "kit_send" in _names(toolbox)

    assert await toolbox.run("kit_page", "{}") == "page"

    assert "kit_send" not in _names(toolbox)
    assert "kit_page" in _names(toolbox)
    assert "untrusted" in await toolbox.run("kit_send", "{}")


async def test_effect_tool_chosen_before_content_arrives_still_runs(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    toolbox = await _toolbox(make_bot, make_plugin_dir, write_config)

    sent, page = await asyncio.gather(
        toolbox.run("kit_send", "{}"), toolbox.run("kit_page", "{}")
    )

    assert (sent, page) == ("sent", "page")
    assert "kit_send" not in _names(toolbox)


async def test_explicit_taint_disables_effect_tools(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    toolbox = await _toolbox(make_bot, make_plugin_dir, write_config)

    toolbox.taint()

    assert "kit_send" not in _names(toolbox)
    assert "note_publish" not in _names(toolbox)
    assert "kit_echo" in _names(toolbox)


async def test_toolbox_is_none_without_tools(
    make_bot: MakeBot, write_config: WriteConfig
) -> None:
    bot = await make_bot(write_config())

    assert await bot.pipeline.toolbox(_event()) is None


async def test_toolbox_runs_tool_and_collects_files_and_followups(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    toolbox = await _toolbox(make_bot, make_plugin_dir, write_config)

    assert await toolbox.run("kit_echo", '{"text": "ab", "times": 2}') == "abab"

    assert toolbox.file_id == "file-1"
    [followup] = toolbox.followups
    assert await followup() == {
        "handled": True,
        "response": "later",
        "file_id": "file-2",
    }


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("kit_missing", "{}", "Error: unknown tool kit_missing"),
        ("kit_echo", "{}", "Error: invalid arguments: text Field required"),
        ("kit_echo", "not json", "Error: invalid arguments: "),
        (
            "kit_echo",
            '{"text": "a", "extra": 1}',
            "Error: invalid arguments: extra Extra inputs are not permitted",
        ),
        ("kit_boom", "", "Error: tool execution failed"),
        ("kit_junk", "{}", "Error: tool returned an invalid result"),
    ],
)
async def test_toolbox_reports_failures_to_the_model(
    name: str,
    arguments: str,
    expected: str,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    toolbox = await _toolbox(make_bot, make_plugin_dir, write_config)

    assert (await toolbox.run(name, arguments)).startswith(expected)


async def test_toolbox_times_out_slow_tools(
    monkeypatch: pytest.MonkeyPatch,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    monkeypatch.setattr(pipeline_module, "_TOOL_TIMEOUT_SECONDS", 0.01)
    toolbox = await _toolbox(make_bot, make_plugin_dir, write_config)

    assert await toolbox.run("kit_hang", "{}") == "Error: tool timed out"


async def test_toolbox_refuses_calls_while_plugin_reloads(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> None:
    plugins_dir = make_plugin_dir("kit", _SOURCE)
    bot = await make_bot(write_config(), plugins_dir=plugins_dir)
    toolbox = await bot.pipeline.toolbox(_event())
    bot.plugin_manager._reloading.add("kit")

    assert (
        await toolbox.run("kit_echo", '{"text": "x"}')
        == "Error: tool is temporarily unavailable"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/echo 你好", ("kit_echo", "你好")),
        ("@bot  /kit_echo\n多行\n文本", ("kit_echo", "@bot  多行\n文本")),
        ("/echo", ("kit_echo", "")),
        ("/missing 你好", None),
        ("/echo/path 你好", None),
        ("请 /echo 你好", None),
    ],
)
async def test_toolbox_parses_slash_command(
    text: str,
    expected: tuple[str, str] | None,
    make_bot: MakeBot,
    make_plugin_dir: MakePluginDir,
    write_config: WriteConfig,
) -> None:
    toolbox = await _toolbox(make_bot, make_plugin_dir, write_config)

    assert toolbox.command(text) == expected


def test_tool_requires_async_method_with_event() -> None:
    with pytest.raises(TypeError, match="must be"):

        @tool("sync")
        def sync(self, event): ...

    with pytest.raises(TypeError, match="must be"):

        @tool("no event")
        async def no_event(self, text: str): ...
