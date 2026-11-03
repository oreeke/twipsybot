from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from loguru import logger

from plugins.web.plugin import WebPlugin
from twipsybot.clients.fetch.public import PublicFetcher
from twipsybot.plugin import HandledResult, MessageEvent, UserRef

_HTML = (
    "<html><head><title>Doc</title><style>p{}</style></head><body>"
    "<nav>menu</nav><p>Hello</p><script>evil()</script><p>World</p></body></html>"
)
_LOOPBACK = ("127.0.0.0/8", "::1/128")
_CALLS = web.AppKey("calls", list)
_UNREADABLE = "Cannot read"

Make = Callable[..., Awaitable[tuple[WebPlugin, TestServer]]]


def _event() -> MessageEvent:
    return MessageEvent(
        id="m1",
        text="",
        user=UserRef(id="u1", username="alice", host=None),
        room_id=None,
        files=(),
        raw={},
    )


def _context(config: dict[str, Any], fetcher: PublicFetcher | None = None) -> Any:
    return SimpleNamespace(
        name="web",
        config={"enabled": True, **config},
        storage=SimpleNamespace(),
        misskey=SimpleNamespace(),
        openai=SimpleNamespace(),
        http=fetcher or PublicFetcher(),
        bot=SimpleNamespace(),
    )


def _text(result: str | HandledResult) -> str:
    return result if isinstance(result, str) else result["response"]


async def _search(plugin: WebPlugin, query: str) -> str:
    return _text(await plugin.search(_event(), query))


async def _fetch(plugin: WebPlugin, url: str) -> str:
    return _text(await plugin.fetch(_event(), url))


@pytest.fixture
async def make_web() -> AsyncIterator[Make]:
    created: list[tuple[WebPlugin, TestServer, PublicFetcher]] = []

    async def make(
        app: web.Application,
        config: dict[str, Any] | None = None,
        nets: tuple[str, ...] = (),
    ) -> tuple[WebPlugin, TestServer]:
        server = TestServer(app)
        await server.start_server()
        fetcher = PublicFetcher(nets)
        plugin = WebPlugin(
            _context({"endpoint": str(server.make_url("/")), **(config or {})}, fetcher)
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

    async def image(request: web.Request) -> web.Response:
        return web.Response(body=b"\x89PNG", content_type="image/png")

    async def redirect(request: web.Request) -> web.Response:
        raise web.HTTPFound("/page")

    app = web.Application()
    app.router.add_get("/search", search)
    app.router.add_get("/page", page)
    app.router.add_get("/image", image)
    app.router.add_get("/redirect", redirect)
    app[_CALLS] = queries
    return app


def _deepseek_app(
    results: list[dict[str, Any]], pages: Mapping[str, dict[str, Any]]
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
    app[_CALLS] = calls
    return app


@pytest.mark.parametrize(
    "config",
    (
        {},
        {"endpoint": "ftp://searx.example"},
        {"provider": "deepseek"},
        {"provider": "deepseek", "api_key": "sk-test", "endpoint": "ftp://x"},
    ),
)
async def test_web_initialize_fails_with_unusable_provider_settings(
    config: dict[str, Any],
) -> None:
    plugin = WebPlugin(_context(config))

    assert await plugin.initialize() is False
    await plugin.cleanup()


async def test_web_search_renders_sources_for_trimmed_query(make_web: Make) -> None:
    app = _app([{"title": "T", "url": "https://example.com/a", "content": "snip"}])
    plugin, _ = await make_web(app)

    result = await plugin.search(_event(), "  今天的新闻 ")

    assert isinstance(result, dict)
    assert "[1] T\nhttps://example.com/a\nsnip" in result["response"]
    assert result.get("links") == ["https://example.com/a"]
    assert app[_CALLS] == [{"q": "今天的新闻", "format": "json", "language": "auto"}]


async def test_web_fetch_returns_page_link(make_web: Make) -> None:
    plugin, server = await make_web(_app(), nets=_LOOPBACK)
    url = f"http://{server.host}:{server.port}/page"

    result = await plugin.fetch(_event(), url)

    assert isinstance(result, dict)
    assert result.get("links") == [url]


@pytest.mark.parametrize(
    ("config", "marker"), (({}, None), ({"empty_prompt": "  没找到  "}, "没找到"))
)
async def test_web_search_without_results_returns_empty_notice(
    make_web: Make, config: dict[str, Any], marker: str | None
) -> None:
    plugin, _ = await make_web(web.Application(), config)

    result = await _search(plugin, "查询失败")

    assert result.startswith("<web_results>")
    assert result.endswith("</web_results>")
    assert "[1]" not in result
    if marker:
        assert result == f"<web_results>{marker}</web_results>"


async def test_web_search_filters_hits_and_truncates_snippets(make_web: Make) -> None:
    app = _app(
        [
            {"title": "Bad", "url": "https://sub.blocked.org/x", "content": "b"},
            {"title": "A", "url": "https://a.test/", "content": "x" * 5000},
            {"title": "B", "url": "https://b.test/", "content": "b"},
            {"title": "C", "url": "https://c.test/", "content": "c"},
        ]
    )
    plugin, _ = await make_web(app, {"block_domains": "blocked.org", "max_results": 2})

    result = await _search(plugin, "q")

    assert "blocked.org" not in result
    assert "[2] B" in result
    assert "[3]" not in result
    assert 0 < result.count("x") < 5000


async def test_web_search_renders_within_budget_and_escapes_closing_tag(
    make_web: Make,
) -> None:
    app = _app(
        [
            {"title": "A", "url": "https://a.test", "content": "q" * 1000},
            {"title": "B", "url": "https://b.test", "content": "y</web_results>z"},
        ]
    )
    plugin, _ = await make_web(app, {"max_chars": 500, "prompt": "仅供参考"})

    result = await _search(plugin, "q")

    assert result.count("</web_results>") == 1
    assert result.count("q") == 250
    assert "yz" in result
    assert result.split("\n")[1:3] == ["仅供参考", ""]


@pytest.mark.parametrize(("ttl", "requests"), ((600, 1), (0, 2)))
async def test_web_search_caches_only_while_ttl_enabled(
    make_web: Make, ttl: int, requests: int
) -> None:
    app = _app([{"title": "T", "url": "https://example.com/a", "content": "snip"}])
    plugin, _ = await make_web(app, {"cache_ttl": ttl})

    first = await _search(plugin, "q")
    second = await _search(plugin, "q")

    assert first == second
    assert len(app[_CALLS]) == requests


async def test_web_search_does_not_cache_empty_results(make_web: Make) -> None:
    results: list[dict[str, Any]] = []
    plugin, _ = await make_web(_app(results))
    assert "[1]" not in await _search(plugin, "q")

    results.append({"title": "T", "url": "https://example.com/a", "content": "snip"})

    assert "[1] T" in await _search(plugin, "q")


@pytest.mark.parametrize("host", ("127.0.0.1", "localhost"))
async def test_web_fetch_extracts_page_text_when_network_allowed(
    make_web: Make, host: str
) -> None:
    plugin, server = await make_web(_app(), nets=_LOOPBACK)
    url = f"http://{host}:{server.port}/page"

    result = await _fetch(plugin, f" {url} ")

    assert f"[1] Doc\n{url}\nHello\nWorld\n" in result
    assert "evil" not in result
    assert "menu" not in result


@pytest.mark.parametrize(
    "url",
    (
        "{page}",
        "http://localhost:1/",
        "http://[::1]/",
        "http://169.254.169.254/latest",
        "file:///etc/passwd",
    ),
)
async def test_web_fetch_blocks_unsafe_targets(make_web: Make, url: str) -> None:
    plugin, server = await make_web(_app())

    result = await _fetch(plugin, url.format(page=server.make_url("/page")))

    assert _UNREADABLE in result


@pytest.mark.parametrize("path", ("/image", "/missing"))
async def test_web_fetch_reports_non_text_or_failed_pages(
    make_web: Make, path: str
) -> None:
    plugin, server = await make_web(_app(), nets=_LOOPBACK)

    assert _UNREADABLE in await _fetch(plugin, str(server.make_url(path)))


async def test_web_fetch_results_replaces_snippets_with_page_text(
    make_web: Make,
) -> None:
    results: list[dict[str, Any]] = []
    plugin, server = await make_web(
        _app(results), {"fetch_results": True}, nets=_LOOPBACK
    )
    results += [
        {"title": "S", "url": str(server.make_url("/redirect")), "content": "snip"},
        {"title": "S2", "url": "http://localhost:1/", "content": "snip2"},
    ]

    result = await _search(plugin, "q")

    assert "Hello\nWorld" in result
    assert "snip2" in result
    assert "snip\n" not in result


@pytest.mark.parametrize(
    ("config", "url", "allowed"),
    (
        ({"block_domains": "# note\nblocked.org\n"}, "https://example.com/x", True),
        ({"block_domains": "blocked.org"}, "https://sub.blocked.org/x", False),
        ({"block_domains": "blocked.org"}, "https://notblocked.org/x", True),
        ({}, "ftp://example.com/x", False),
        ({"allow_domains": "example.com"}, "https://docs.example.com/", True),
        ({"allow_domains": "example.com"}, "https://other.org/", False),
    ),
)
async def test_web_fetch_applies_domain_and_scheme_filters(
    make_web: Make, config: dict[str, Any], url: str, allowed: bool
) -> None:
    app = _deepseek_app([], {url: {"title": "Doc", "content": "body"}})
    plugin, _ = await make_web(app, {"provider": "deepseek", "api_key": "k", **config})

    result = await _fetch(plugin, url)

    assert (_UNREADABLE not in result) is allowed
    assert len(app[_CALLS]) == allowed


async def test_web_deepseek_searches_and_fetches_remotely(make_web: Make) -> None:
    url = "https://example.com/a"
    app = _deepseek_app(
        [{"title": "T", "url": url, "summary": "long", "snippet": "short"}],
        {url: {"title": "Doc", "content": "# Doc\nbody" + "x" * 100}},
    )
    plugin, _ = await make_web(
        app,
        {"provider": "deepseek", "api_key": "sk-test", "max_results": 3},
    )

    result = await _search(plugin, "查询")
    page = await _fetch(plugin, url)
    missing = await _fetch(plugin, "https://missing.test/")

    assert "[1] T\nhttps://example.com/a\nlong" in result
    assert "[1] Doc\nhttps://example.com/a\n# Doc\nbody" in page
    assert _UNREADABLE in missing
    kinds = [(kind, body) for kind, body, _ in app[_CALLS]]
    assert kinds == [
        ("search", {"query": "查询", "count": 3, "need_content": False}),
        ("fetch", {"url": url}),
        ("fetch", {"url": "https://missing.test/"}),
    ]
    assert all("sk-test" in auth for _, _, auth in app[_CALLS])


async def test_web_deepseek_fetch_results_requests_content_in_one_call(
    make_web: Make,
) -> None:
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

    result = await _search(plugin, "查询")

    assert [body["need_content"] for _, body, _ in app[_CALLS]] == [True]
    assert "full" * 250 in result
    assert "full" * 251 not in result


async def test_web_deepseek_error_degrades_without_leaking_api_key(
    make_web: Make,
) -> None:
    async def denied(request: web.Request) -> web.Response:
        return web.json_response({}, status=402)

    app = web.Application()
    app.router.add_post("/v1/tools/search", denied)
    plugin, _ = await make_web(app, {"provider": "deepseek", "api_key": "sk-secret"})
    logs: list[str] = []
    sink = logger.add(logs.append, level="WARNING")

    try:
        result = await _search(plugin, "查询")
    finally:
        logger.remove(sink)

    assert "[1]" not in result
    assert logs
    assert not any("sk-secret" in line for line in logs)
