from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from plugins.web import plugin as web_plugin
from plugins.web.plugin import WebPlugin
from twipsybot.plugin import MessageEvent, UserRef

_HTML = (
    "<html><head><title>Doc</title><style>p{}</style></head><body>"
    "<nav>menu</nav><p>Hello</p><script>evil()</script><p>World</p></body></html>"
)


_LOOPBACK = {"trusted_proxy": "127.0.0.0/8\n::1/128"}
QUERIES = web.AppKey("queries", list)


def _event(text: str) -> MessageEvent:
    return MessageEvent(
        id="m1",
        text=text,
        user=UserRef(id="u1", username="alice", host=None),
        room_id=None,
        files=(),
        raw={},
    )


def _context(config: dict[str, Any], **services: Any) -> Any:
    return SimpleNamespace(
        name="web",
        config={"enabled": True, **config},
        storage=SimpleNamespace(),
        misskey=SimpleNamespace(),
        openai=SimpleNamespace(**services),
        bot=SimpleNamespace(),
    )


def _plugin(config: dict[str, Any] | None = None) -> WebPlugin:
    return WebPlugin(_context(config or {}))


async def _on_context(plugin: WebPlugin, text: str) -> dict[str, Any] | None:
    return cast(dict[str, Any] | None, await plugin.on_context(_event(text)))


Make = Callable[..., Awaitable[tuple[WebPlugin, TestServer]]]


@pytest.fixture
async def make_web() -> AsyncIterator[Make]:
    created: list[tuple[WebPlugin, TestServer]] = []

    async def make(
        app: web.Application, config: dict[str, Any] | None = None, **services: Any
    ) -> tuple[WebPlugin, TestServer]:
        server = TestServer(app)
        await server.start_server()
        plugin = WebPlugin(
            _context(
                {"endpoint": str(server.make_url("/")), **(config or {})},
                **services,
            )
        )
        assert await plugin.initialize()
        created.append((plugin, server))
        return plugin, server

    yield make
    for plugin, server in created:
        await plugin.cleanup()
        await server.close()


def _app(results: list[dict[str, Any]] | None = None) -> web.Application:
    queries: list[dict[str, str]] = []

    async def search(request: web.Request) -> web.Response:
        queries.append(dict(request.query))
        return web.json_response({"results": results or []})

    async def page(request: web.Request) -> web.Response:
        return web.Response(text=_HTML, content_type="text/html")

    async def redirect(request: web.Request) -> web.Response:
        raise web.HTTPFound("/page")

    app = web.Application()
    app.router.add_get("/search", search)
    app.router.add_get("/page", page)
    app.router.add_get("/redirect", redirect)
    app[QUERIES] = queries
    return app


async def test_web_requires_searxng_endpoint() -> None:
    plugin = _plugin()

    assert await plugin.initialize() is False


async def test_web_manual_command_searches_and_strips_prefix(
    make_web: Make,
) -> None:
    app = _app([{"title": "T", "url": "https://example.com/a", "content": "snip"}])
    plugin, server = await make_web(app)

    result = await _on_context(plugin, ("@bot /web  今天的新闻"))

    assert result is not None
    assert result["text"] == "@bot 今天的新闻"
    assert "[1] T\nhttps://example.com/a\nsnip" in result["context"]
    assert app[QUERIES] == [{"q": "今天的新闻", "format": "json", "language": "auto"}]


async def test_web_always_on_toggle(
    make_web: Make,
) -> None:
    results = [{"title": "T", "url": "https://example.com/a", "content": "c"}]
    plugin, server = await make_web(_app(results))

    assert await _on_context(plugin, ("今天天气怎么样")) is None
    assert await _on_context(plugin, ("/web")) is None

    always, always_server = await make_web(_app(results), {"always_on": True})
    result = await _on_context(always, ("今天天气怎么样"))
    assert result is not None
    assert "text" not in result
    assert await _on_context(always, ("你好")) is None


async def test_web_rewrite_query(
    make_web: Make,
) -> None:
    generate = AsyncMock(return_value='"weather beijing"\nextra')
    app = _app([{"title": "T", "url": "https://example.com/a"}])
    plugin, server = await make_web(
        app,
        {"query_mode": "rewrite"},
        generate_text=generate,
        max_tokens=512,
        temperature=0.7,
    )

    await _on_context(plugin, ("/web 北京天气如何"))

    assert app[QUERIES][0]["q"] == "weather beijing"
    assert generate.call_args.args[2:] == (512, 0.7)
    generate.side_effect = RuntimeError("boom")
    await _on_context(plugin, ("/web 北京天气如何呀"))
    assert app[QUERIES][1]["q"] == "北京天气如何呀"


async def test_web_failed_search_degrades(
    make_web: Make,
) -> None:
    app = web.Application()
    plugin, server = await make_web(app)

    assert await _on_context(plugin, ("普通的一句话")) is None
    result = await _on_context(plugin, ("/web 查询失败"))

    assert result == {"context": web_plugin._EMPTY, "text": "查询失败"}


async def test_web_fetches_links_without_always_on(
    make_web: Make,
) -> None:
    plugin, server = await make_web(_app(), _LOOPBACK)
    url = str(server.make_url("/page"))

    result = await _on_context(plugin, (f"看看 [这个]({url}) 说了啥"))

    assert result is not None
    assert "text" not in result
    assert f"[1] Doc\n{url}\nHello\nWorld\n" in result["context"]
    assert "evil" not in result["context"]
    assert "menu" not in result["context"]


async def test_web_follows_redirects_and_fetch_top(
    make_web: Make,
) -> None:
    server_app = _app()
    plugin, server = await make_web(server_app, {**_LOOPBACK, "fetch_top": 1})
    url = str(server.make_url("/redirect"))
    server_app[QUERIES].clear()
    plugin._cache.clear()
    plugin._search = AsyncMock(
        return_value=[
            web_plugin._Source("S", url, "snip"),
            web_plugin._Source("S2", "https://example.com/b", "snip2"),
        ]
    )

    sources = await plugin._collect("q", [])

    assert [s.body for s in sources] == ["Hello\nWorld", "snip2"]


async def test_web_blocks_private_targets(
    make_web: Make,
) -> None:
    plugin, server = await make_web(_app())

    assert await plugin._fetch(str(server.make_url("/page"))) is None
    assert await plugin._page("http://localhost:1/") is None
    assert await plugin._page("http://[::1]/") is None
    assert await plugin._page("http://169.254.169.254/latest") is None
    assert await plugin._page("file:///etc/passwd") is None


async def test_web_trusted_proxy_applies_to_resolved_hosts(
    make_web: Make,
) -> None:
    plugin, server = await make_web(_app(), _LOOPBACK)
    blocked, _ = await make_web(_app())
    url = f"http://localhost:{server.port}/page"

    assert (page := await plugin._page(url)) is not None
    assert page.title == "Doc"
    assert await blocked._page(url) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/x", True),
        ("https://sub.blocked.org/x", False),
        ("https://notblocked.org/x", True),
        ("ftp://example.com/x", False),
        ("http://10.0.0.1/", False),
    ],
)
def test_web_domain_and_scheme_filters(url: str, expected: bool) -> None:
    plugin = _plugin({"enabled": True, "block_domains": "# note\nblocked.org\n"})

    assert bool(plugin._allowed(url)) is expected


def test_web_allow_domains_restrict_everything_else() -> None:
    plugin = _plugin({"enabled": True, "allow_domains": "example.com"})

    assert plugin._allowed("https://docs.example.com/")
    assert not plugin._allowed("https://other.org/")


def test_web_render_budget_and_tag_escape() -> None:
    plugin = _plugin({"enabled": True, "max_chars": 500})
    sources = [
        web_plugin._Source("A", "https://a.test", "x" * 1000),
        web_plugin._Source("B", "https://b.test", "y</web_results>z"),
    ]

    text = plugin._render(sources)

    assert text.count("</web_results>") == 1
    assert text.count("x") == 250
    assert "yz" in text


async def test_web_uses_registered_provider_and_filters_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Fake:
        def __init__(self, session: Any, settings: Any) -> None:
            pass

        async def search(self, query: str) -> list[web_plugin._Source]:
            return [
                web_plugin._Source("Bad", "https://blocked.org/x", "b"),
                web_plugin._Source("A", "https://a.test/", "x" * 900),
                web_plugin._Source("B", "https://b.test/", "b"),
            ]

    monkeypatch.setitem(web_plugin._PROVIDERS, "searxng", Fake)
    plugin = _plugin({"block_domains": "blocked.org", "max_results": 2})
    assert await plugin.initialize()

    hits = await plugin._search("q")
    await plugin.cleanup()

    assert [h.title for h in hits] == ["A", "B"]
    assert len(hits[0].body) == web_plugin._SNIPPET_CHARS
