from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from twipsybot.clients.fetch.public import PublicFetcher
from twipsybot.clients.misskey.api import MisskeyAPI
from twipsybot.shared.exceptions import APINotFoundError, BlockedURLError

_LOOPBACK = ("127.0.0.0/8", "::1/128")


async def _status(
    fetcher: PublicFetcher, url: Any, allow: Callable[[Any], bool] | None = None
) -> int:
    async with fetcher.open(url, allow=allow) as response:
        return response.status


@pytest.fixture
async def server() -> AsyncIterator[TestServer]:
    async def body(request: web.Request) -> web.Response:
        return web.Response(body=b"x" * 100)

    async def loop(request: web.Request) -> web.Response:
        raise web.HTTPFound("/loop")

    async def to_metadata(request: web.Request) -> web.Response:
        raise web.HTTPFound("http://169.254.169.254/latest")

    app = web.Application()
    app.router.add_get("/body", body)
    app.router.add_get("/loop", loop)
    app.router.add_get("/meta", to_metadata)
    srv = TestServer(app)
    await srv.start_server()
    yield srv
    await srv.close()


@pytest.fixture
async def fetcher(server: TestServer) -> AsyncIterator[PublicFetcher]:
    fetcher = PublicFetcher(origin=str(server.make_url("/")))
    yield fetcher
    await fetcher.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest",
        "http://[::ffff:127.0.0.1]/",
        "http://[64:ff9b::7f00:1]/",
        "http://localhost/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "http:///nohost",
    ],
)
async def test_fetcher_blocks_non_public_targets(url: str) -> None:
    public = PublicFetcher()
    try:
        with pytest.raises(BlockedURLError):
            await _status(public, url)
    finally:
        await public.close()


async def test_fetcher_origin_by_hostname_skips_resolved_ip_check(
    server: TestServer,
) -> None:
    url = f"http://localhost:{server.port}/body"
    named = PublicFetcher(origin=f"http://localhost:{server.port}")
    try:
        assert await _status(named, url) == 200
    finally:
        await named.close()


async def test_fetcher_allow_nets_are_updatable(server: TestServer) -> None:
    url = str(server.make_url("/body"))
    public = PublicFetcher()
    try:
        with pytest.raises(BlockedURLError):
            await _status(public, url)
        public.set_nets(_LOOPBACK)
        assert await _status(public, url) == 200
        public.set_nets(())
        with pytest.raises(BlockedURLError):
            await _status(public, url)
    finally:
        await public.close()


async def test_fetcher_resolved_hosts_follow_allow_nets(
    server: TestServer,
) -> None:
    url = f"http://localhost:{server.port}/body"
    blocked, allowed = PublicFetcher(), PublicFetcher(_LOOPBACK)
    try:
        with pytest.raises(BlockedURLError):
            await _status(blocked, url)
        assert await _status(allowed, url) == 200
    finally:
        await blocked.close()
        await allowed.close()


async def test_fetcher_origin_exempts_only_its_host_and_port(
    server: TestServer, fetcher: PublicFetcher
) -> None:
    assert server.port is not None
    other_host = f"http://localhost:{server.port}/"
    other_port = f"http://127.0.0.1:{server.port + 1}/"

    assert await _status(fetcher, server.make_url("/body")) == 200
    with pytest.raises(BlockedURLError):
        await _status(fetcher, other_host)
    with pytest.raises(BlockedURLError):
        await _status(fetcher, other_port)


async def test_fetcher_validates_every_redirect_hop(
    server: TestServer, fetcher: PublicFetcher
) -> None:
    to_metadata, to_loop = server.make_url("/meta"), server.make_url("/loop")

    with pytest.raises(BlockedURLError, match="non-public"):
        await _status(fetcher, to_metadata)
    with pytest.raises(BlockedURLError, match="redirects"):
        await _status(fetcher, to_loop)


async def test_fetcher_allow_runs_on_every_hop(
    server: TestServer, fetcher: PublicFetcher
) -> None:
    seen: list[str] = []

    def allow(url: object) -> bool:
        seen.append(str(url))
        return len(seen) < 2

    redirecting = server.make_url("/loop")
    with pytest.raises(BlockedURLError, match="not allowed"):
        await _status(fetcher, redirecting, allow)
    assert len(seen) == 2


async def test_fetcher_read_limits(server: TestServer, fetcher: PublicFetcher) -> None:
    url = server.make_url("/body")

    async with fetcher.open(url) as response:
        assert len(await fetcher.read(response, 100)) == 100
    async with fetcher.open(url) as response:
        with pytest.raises(ValueError, match="exceeds"):
            await fetcher.read(response, 99)
    async with fetcher.open(url) as response:
        assert len(await fetcher.read(response, 10, truncate=True)) == 10


async def test_drive_fetch_bytes_uses_guarded_fetcher(server: TestServer) -> None:
    body, missing = str(server.make_url("/body")), str(server.make_url("/missing"))
    api = MisskeyAPI(str(server.make_url("/")).rstrip("/"), "token")
    try:
        assert len(await api.drive.fetch_bytes(body)) == 100
        with pytest.raises(ValueError, match="exceeds"):
            await api.drive.fetch_bytes(body, max_bytes=10)
        with pytest.raises(APINotFoundError):
            await api.drive.fetch_bytes(missing)
        with pytest.raises(BlockedURLError):
            await api.drive.fetch_bytes("http://169.254.169.254/latest")
    finally:
        await api.close()
