from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from support.events import message
from support.fakes import FakeMisskeyServer
from support.types import MakeBot, MakePluginDir, WriteConfig

import twipsybot.bot.engine.pipeline as pipeline_module
from twipsybot import Neuro
from twipsybot.bot.engine.pipeline import Toolbox
from twipsybot.plugin import tool

TOOL_PLUGIN = """
import asyncio
from typing import Annotated

from pydantic import Field
from twipsybot.plugin import PluginBase, tool


class ToolPlugin(PluginBase):
    @tool("Repeat text")
    async def echo(
        self,
        event,
        text: Annotated[str, Field(description="what to repeat")],
        times: int = 1,
    ):
        async def later():
            return self.handled("later", file_ids=["file-2"])

        return self.handled(
            text * times,
            file_ids=["file-1", f"x-{text}"],
            followup=later,
            links=[f"https://{text}.test/x(1)"],
        )

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


def event(user_id: str = "user-1") -> Any:
    return message(
        {"id": "m1", "text": "hi", "user": {"id": user_id, "username": "alice"}}
    )


def names(toolbox: Toolbox) -> list[str]:
    return [s["name"] for s in toolbox.specs]


@pytest.fixture
async def kit_bot(
    make_bot: MakeBot, make_plugin_dir: MakePluginDir, write_config: WriteConfig
) -> Neuro:
    return await make_bot(
        write_config(bot={"admins": ["user-1"]}),
        plugins_dir=make_plugin_dir("kit", TOOL_PLUGIN),
    )


@pytest.fixture
async def toolbox(kit_bot: Neuro) -> Toolbox:
    return await kit_bot.pipeline.toolbox(event())


async def test_toolbox_filters_tools_by_access(
    kit_bot: Neuro, misskey_server: FakeMisskeyServer
) -> None:
    misskey_server.set_response(
        "users/show", lambda payload: {"isFollowed": payload["userId"] == "user-3"}
    )
    common = ["time_now", "kit_boom", "kit_echo", "kit_hang", "kit_junk"]

    admin = await kit_bot.pipeline.toolbox(event("user-1"))
    follower = await kit_bot.pipeline.toolbox(event("user-3"))
    guest = await kit_bot.pipeline.toolbox(event("user-2"))

    assert names(admin) == [
        "note_cancel",
        "note_pending",
        "note_publish",
        *common,
        "kit_members",
        "kit_page",
        "kit_secret",
        "kit_send",
    ]
    assert names(follower) == [*common, "kit_members", "kit_page", "kit_send"]
    assert names(guest) == [*common, "kit_page", "kit_send"]
    assert {c["userId"] for c in misskey_server.calls["users/show"]} == {
        "user-2",
        "user-3",
    }


async def test_toolbox_exposes_tool_schema_from_signature(toolbox: Toolbox) -> None:
    echo = next(s for s in toolbox.specs if s["name"] == "kit_echo")

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


async def test_follower_lookup_is_cached(
    kit_bot: Neuro, misskey_server: FakeMisskeyServer
) -> None:
    for _ in range(2):
        assert "kit_members" in names(await kit_bot.pipeline.toolbox(event("user-3")))

    assert misskey_server.calls["users/show"] == [{"userId": "user-3"}]


async def test_follower_lookup_failure_hides_follower_tools(kit_bot: Neuro) -> None:
    kit_bot.misskey.show_user = AsyncMock(side_effect=RuntimeError("down"))

    assert "kit_members" not in names(await kit_bot.pipeline.toolbox(event("user-3")))


async def test_reading_external_content_disables_effect_tools(
    toolbox: Toolbox,
) -> None:
    assert "kit_send" in names(toolbox)

    assert await toolbox.run("kit_page", "{}") == "page"

    assert "kit_send" not in names(toolbox)
    assert "kit_page" in names(toolbox)
    assert "untrusted" in await toolbox.run("kit_send", "{}")


async def test_effect_tool_chosen_before_content_arrives_still_runs(
    toolbox: Toolbox,
) -> None:
    sent, page = await asyncio.gather(
        toolbox.run("kit_send", "{}"), toolbox.run("kit_page", "{}")
    )

    assert (sent, page) == ("sent", "page")
    assert "kit_send" not in names(toolbox)


async def test_explicit_taint_disables_effect_tools(toolbox: Toolbox) -> None:
    toolbox.taint()

    assert "kit_send" not in names(toolbox)
    assert "note_publish" not in names(toolbox)
    assert "kit_echo" in names(toolbox)


async def test_toolbox_collects_unique_files_and_followups(toolbox: Toolbox) -> None:
    assert await toolbox.run("kit_echo", '{"text": "ab", "times": 2}') == "abab"
    await toolbox.run("kit_echo", '{"text": "a"}')
    await toolbox.run("kit_echo", '{"text": "a"}')

    assert toolbox.file_ids == ["file-1", "x-ab", "x-a"]
    assert toolbox.links == ["https://ab.test/x(1)", "https://a.test/x(1)"]
    followup = toolbox.followups[0]
    assert await followup() == {
        "handled": True,
        "response": "later",
        "file_ids": ["file-2"],
    }


async def test_respond_appends_tool_links_missing_from_reply(kit_bot: Neuro) -> None:
    sent: list[str] = []

    async def generate(augment: Any) -> str:
        await augment.tools.run("kit_echo", '{"text": "a"}')
        await augment.tools.run("kit_echo", '{"text": "b"}')
        return "see https://a.test/x(1)"

    async def deliver(reply: Any, source: str) -> None:
        sent.append(reply.text)

    await kit_bot.pipeline.respond(
        user_id=None,
        handle=None,
        hook="on_message",
        event=event(),
        generate=generate,
        deliver=deliver,
    )

    assert sent == ["see https://a.test/x(1)\n\n?[b.test](https://b.test/x(1%29)"]


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
    name: str, arguments: str, expected: str, toolbox: Toolbox
) -> None:
    assert (await toolbox.run(name, arguments)).startswith(expected)


async def test_toolbox_times_out_slow_tools(
    monkeypatch: pytest.MonkeyPatch, toolbox: Toolbox
) -> None:
    monkeypatch.setattr(pipeline_module, "_TOOL_TIMEOUT_SECONDS", 0.01)

    assert await toolbox.run("kit_hang", "{}") == "Error: tool timed out"


async def test_toolbox_refuses_calls_while_plugins_are_not_running(
    kit_bot: Neuro, toolbox: Toolbox
) -> None:
    await kit_bot.plugin_manager.shutdown_plugins()

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
    text: str, expected: tuple[str, str] | None, toolbox: Toolbox
) -> None:
    assert toolbox.command(text) == expected


def test_tool_requires_async_method_with_event() -> None:
    with pytest.raises(TypeError, match="must be"):

        @tool("sync")
        def sync(self, event): ...

    with pytest.raises(TypeError, match="must be"):

        @tool("no event")
        async def no_event(self, text: str): ...
