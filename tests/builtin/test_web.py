from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from plugins.web import plugin as web_plugin
from plugins.web.plugin import WebPlugin
from twipsybot.clients.fetch.public import PublicFetcher
from twipsybot.plugin import (
    APIResponseError,
    MessageEvent,
    UserRef,
)
from twipsybot.plugin.services import HttpServiceAdapter
from twipsybot.shared.errors import BlockedURLError

_HTML = (
    "<html><head><title>Doc</title><style>p{}</style></head><body>"
    "<nav>menu</nav><p>Hello</p><script>evil()</script><p>World</p></body></html>"
)


_LOOPBACK = ("127.0.0.0/8", "::1/128")
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


def _context(
    config: dict[str, Any], fetcher: PublicFetcher | None = None, **services: Any
) -> Any:
    return SimpleNamespace(
        name="web",
        config={"enabled": True, **config},
        storage=SimpleNamespace(),
        misskey=SimpleNamespace(),
        openai=SimpleNamespace(**services),
        http=HttpServiceAdapter(fetcher or PublicFetcher()),
        bot=SimpleNamespace(),
    )


def _plugin(config: dict[str, Any] | None = None) -> WebPlugin:
    return WebPlugin(_context(config or {}))


async def _search(plugin: WebPlugin, query: str) -> str:
    return await plugin.search(_event(""), query)


async def _fetch(plugin: WebPlugin, url: str) -> str:
    return await plugin.fetch(_event(""), url)


Make = Callable[..., Awaitable[tuple[WebPlugin, TestServer]]]


@pytest.fixture
async def make_web() -> AsyncIterator[Make]:
    created: list[tuple[WebPlugin, TestServer, PublicFetcher]] = []

    async def make(
        app: web.Application,
        config: dict[str, Any] | None = None,
        nets: tuple[str, ...] = (),
        **services: Any,
    ) -> tuple[WebPlugin, TestServer]:
        server = TestServer(app)
        await server.start_server()
        fetcher = PublicFetcher(nets)
        plugin = WebPlugin(
            _context(
                {"endpoint": str(server.make_url("/")), **(config or {})},
                fetcher,
                **services,
            )
        )
        assert await plugin.initialize()
        created.append((plugin, server, fetcher))
        return plugin, server

    yield make
    for plugin, server, fetcher in created:
        await plugin.cleanup()
        await fetcher.close()
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


def test_web_tools_require_followers() -> None:
    plugin = _plugin()

    assert {(s.name, s.access) for s in plugin._tools} == {
        ("search", "followers"),
        ("fetch", "followers"),
    }


async def test_web_search_tool_renders_sources(make_web: Make) -> None:
    app = _app([{"title": "T", "url": "https://example.com/a", "content": "snip"}])
    plugin, _ = await make_web(app)

    result = await _search(plugin, "  今天的新闻 ")

    assert "[1] T\ncite: ?[*](https://example.com/a)\nsnip" in result
    assert app[QUERIES] == [{"q": "今天的新闻", "format": "json", "language": "auto"}]


async def test_web_failed_search_degrades(make_web: Make) -> None:
    plugin, _ = await make_web(web.Application())

    assert await _search(plugin, "查询失败") == (
        f"<web_results>{web_plugin._EMPTY_PROMPT}</web_results>"
    )

    custom, _ = await make_web(web.Application(), {"empty_prompt": "  没找到  "})
    assert await _search(custom, "查询失败") == "<web_results>没找到</web_results>"


async def test_web_fetch_tool_extracts_page_text(make_web: Make) -> None:
    plugin, server = await make_web(_app(), nets=_LOOPBACK)
    url = str(server.make_url("/page"))

    result = await _fetch(plugin, f" {url} ")

    assert f"[1] Doc\ncite: ?[*]({url})\nHello\nWorld\n" in result
    assert "evil" not in result
    assert "menu" not in result


async def test_web_fetch_tool_reports_unreadable_pages(make_web: Make) -> None:
    plugin, server = await make_web(_app())

    assert "Cannot read" in await _fetch(plugin, str(server.make_url("/page")))
    assert "Cannot read" in await _fetch(plugin, "file:///etc/passwd")


async def test_web_fetch_results_fetches_every_hit(
    make_web: Make,
) -> None:
    server_app = _app()
    plugin, server = await make_web(server_app, {"fetch_results": True}, nets=_LOOPBACK)
    url = str(server.make_url("/redirect"))
    plugin._search = AsyncMock(
        return_value=[
            web_plugin._Source("S", url, "snip"),
            web_plugin._Source("S2", "http://localhost:1/", "snip2"),
        ]
    )

    sources = await plugin._collect("q")

    assert [s.body for s in sources] == ["Hello\nWorld", "snip2"]


async def test_web_blocks_private_targets(
    make_web: Make,
) -> None:
    plugin, server = await make_web(_app())

    page_url = str(server.make_url("/page"))
    with pytest.raises(BlockedURLError):
        await plugin._fetch(page_url)
    assert await plugin._page("http://localhost:1/") is None
    assert await plugin._page("http://[::1]/") is None
    assert await plugin._page("http://169.254.169.254/latest") is None
    assert await plugin._page("file:///etc/passwd") is None


async def test_web_allow_nets_apply_to_resolved_hosts(
    make_web: Make,
) -> None:
    plugin, server = await make_web(_app(), nets=_LOOPBACK)
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
        web_plugin._Source("A", "https://a.test", "q" * 1000),
        web_plugin._Source("B", "https://b.test", "y</web_results>z"),
    ]

    text = plugin._render(sources)

    assert text.count("</web_results>") == 1
    assert text.count("q") == 250
    assert "yz" in text
    assert web_plugin._PROMPT in text

    custom = _plugin({"prompt": "仅供参考"})._render(sources[1:])
    assert custom.split("\n")[1:3] == ["仅供参考", ""]
    assert web_plugin._PROMPT not in custom


def _deepseek_app(
    results: list[dict[str, Any]], pages: dict[str, dict[str, Any]]
) -> web.Application:
    calls: list[tuple[str, dict[str, Any], str]] = []

    async def search(request: web.Request) -> web.Response:
        calls.append(("search", await request.json(), request.headers["Authorization"]))
        return web.json_response({"results": results})

    async def fetch(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append(("fetch", body, request.headers["Authorization"]))
        if page := pages.get(body["url"]):
            return web.json_response(page)
        return web.json_response({"error": {}}, status=404)

    app = web.Application()
    app.router.add_post("/v1/tools/search", search)
    app.router.add_post("/v1/tools/fetch", fetch)
    app[QUERIES] = calls
    return app


async def test_web_deepseek_requires_api_key() -> None:
    assert await _plugin({"provider": "deepseek"}).initialize() is False


async def test_web_deepseek_searches_and_fetches_remotely(make_web: Make) -> None:
    url = "https://example.com/a"
    app = _deepseek_app(
        [{"title": "T", "url": url, "summary": "long", "snippet": "short"}],
        {url: {"title": "Doc", "content": "# Doc\nbody" + "x" * 100}},
    )
    plugin, _ = await make_web(
        app,
        {
            "provider": "deepseek",
            "api_key": "sk-test",
            "max_results": 3,
            "max_chars": 500,
            "block_domains": "blocked.org",
        },
    )

    result = await _search(plugin, "查询")
    page = await _fetch(plugin, url)
    blocked = await _fetch(plugin, "https://blocked.org/x")

    assert "[1] T\ncite: ?[*](https://example.com/a)\nlong" in result
    assert "[1] Doc\ncite: ?[*](https://example.com/a)\n# Doc\nbody" in page
    assert "Cannot read" in blocked
    searches = [call for call in app[QUERIES] if call[0] == "search"]
    assert searches == [
        (
            "search",
            {"query": "查询", "count": 3, "need_content": False},
            "Bearer sk-test",
        )
    ]
    fetches = [call[1]["url"] for call in app[QUERIES] if call[0] == "fetch"]
    assert fetches == [url]
    assert await plugin._page("https://missing.test/") is None


async def test_web_deepseek_fetch_results_uses_need_content(make_web: Make) -> None:
    url = "https://example.com/a"
    app = _deepseek_app(
        [{"title": "T", "url": url, "summary": "long", "content": "full" * 400}], {}
    )
    plugin, _ = await make_web(
        app,
        {
            "provider": "deepseek",
            "api_key": "sk-test",
            "fetch_results": True,
            "max_chars": 1000,
        },
    )

    hits = await plugin._search("查询")

    assert app[QUERIES][0][1]["need_content"] is True
    assert [h.body for h in hits] == ["full" * 250]
    assert len(app[QUERIES]) == 1


async def test_web_deepseek_errors_do_not_leak_api_key(make_web: Make) -> None:
    async def denied(request: web.Request) -> web.Response:
        return web.json_response({}, status=402)

    app = web.Application()
    app.router.add_post("/v1/tools/search", denied)
    plugin, _ = await make_web(app, {"provider": "deepseek", "api_key": "sk-secret"})

    with pytest.raises(APIResponseError) as raised:
        await plugin._provider.search("查询")

    assert raised.value.status == 402
    assert "sk-secret" not in repr(raised.value)


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
