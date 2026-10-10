import ipaddress
import socket
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import asynccontextmanager

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from yarl import URL

from ...shared.constants import API_TIMEOUT
from ...shared.exceptions import BlockedURLError

__all__ = ("PublicFetcher",)

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_USER_AGENT = "Mozilla/5.0 (compatible; TwipsyBot)"
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 3
_CHUNK_SIZE = 65536
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class _Resolver(AbstractResolver):
    def __init__(self, fetcher: "PublicFetcher") -> None:
        self._inner = aiohttp.DefaultResolver()
        self._fetcher = fetcher

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        hosts = await self._inner.resolve(host, port, family)
        if self._fetcher.is_origin(host, port):
            return hosts
        if public := [
            h for h in hosts if self._fetcher.is_public(ipaddress.ip_address(h["host"]))
        ]:
            return public
        raise BlockedURLError(f"non-public address: {host}")

    async def close(self) -> None:
        await self._inner.close()


class PublicFetcher:
    def __init__(self, nets: Iterable[str] = (), *, origin: str = "") -> None:
        self._nets: tuple[_IPNetwork, ...] = ()
        self._origin = URL(origin) if origin else None
        self._session: aiohttp.ClientSession | None = None
        self._resolver: _Resolver
        self.set_nets(nets)

    def set_nets(self, nets: Iterable[str]) -> None:
        self._nets = tuple(ipaddress.ip_network(n, strict=False) for n in nets if n)

    def is_origin(self, host: str, port: int | None) -> bool:
        origin = self._origin
        return bool(origin and origin.host == host and origin.port == port)

    def is_public(self, ip: _IPAddress) -> bool:
        if ip.version == 6 and ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        return ip.is_global or any(ip in network for network in self._nets)

    def _check(self, url: URL) -> None:
        if url.scheme not in {"http", "https"} or not url.host:
            raise BlockedURLError(f"unsupported URL: {url}")
        if self.is_origin(url.host, url.port):
            return
        try:
            ip = ipaddress.ip_address(url.host)
        except ValueError:
            return
        if not self.is_public(ip):
            raise BlockedURLError(f"non-public address: {url.host}")

    @property
    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._resolver = _Resolver(self)
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": _USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                connector=aiohttp.TCPConnector(resolver=self._resolver),
                cookie_jar=aiohttp.DummyCookieJar(),
            )
        return self._session

    @asynccontextmanager
    async def open(
        self, url: str | URL, *, allow: Callable[[URL], bool] | None = None
    ) -> AsyncGenerator[aiohttp.ClientResponse]:
        target = URL(url)
        for _ in range(_MAX_REDIRECTS + 1):
            self._check(target)
            if allow and not allow(target):
                raise BlockedURLError(f"URL not allowed: {target.host}")
            async with self._client.get(target, allow_redirects=False) as response:
                location = response.headers.get("Location")
                if response.status in _REDIRECTS and location:
                    target = target.join(URL(location))
                    continue
                yield response
                return
        raise BlockedURLError("too many redirects")

    @staticmethod
    async def read(
        response: aiohttp.ClientResponse, max_bytes: int, *, truncate: bool = False
    ) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
            if size + len(chunk) > max_bytes:
                if not truncate:
                    raise ValueError("file size exceeds limit")
                chunks.append(chunk[: max_bytes - size])
                break
            chunks.append(chunk)
            size += len(chunk)
        return b"".join(chunks)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            await self._resolver.close()
        self._session = None
