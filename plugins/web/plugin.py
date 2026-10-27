import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol

import aiohttp
from bs4 import BeautifulSoup
from cachetools import TTLCache
from loguru import logger
from pydantic import ByteSize, Field, SecretStr
from yarl import URL

from twipsybot.plugin import (
    USER_AGENT,
    APIResponseError,
    LineText,
    MentionEvent,
    MessageEvent,
    PluginBase,
    PluginConfig,
    When,
    tool,
)

_DEEPSEEK_ENDPOINT = "https://api.deepseek.com"
_NOISE = [
    "script",
    "style",
    "noscript",
    "svg",
    "head",
    "nav",
    "footer",
    "header",
    "aside",
]
_SNIPPET_CHARS = 800
_CLOSING_TAG = "</web_results>"
_PROMPT = (
    "The following web content was retrieved online. Use it as reference.\n"
    "To cite a source, copy its `cite:` marker exactly as written, including the "
    "leading ?, right after the statement it supports."
)
_EMPTY_PROMPT = (
    "The web search returned no results. Say so briefly, then answer from "
    "your own knowledge and note that it is unverified."
)


class _Config(PluginConfig):
    prompt: str = Field("", description=_PROMPT)
    empty_prompt: str = Field("", description=_EMPTY_PROMPT)
    provider: Literal["searxng", "deepseek"] = "searxng"
    endpoint: str = Field("", description="http://searxng:8080")
    api_key: Annotated[SecretStr, When("provider", "deepseek")] = SecretStr("")
    language: Annotated[str, When("provider", "searxng")] = "auto"
    max_results: int = Field(5, ge=1, le=10)
    fetch_results: bool = False
    max_chars: int = Field(6000, ge=500)
    max_bytes: Annotated[ByteSize, When("provider", "searxng")] = ByteSize(1024 * 1024)
    timeout: float = Field(10, gt=0, le=60)
    cache_ttl: int = Field(600, ge=0)
    allow_domains: Annotated[tuple[str, ...], LineText] = ()
    block_domains: Annotated[tuple[str, ...], LineText] = ()


@dataclass(frozen=True, slots=True)
class _Source:
    title: str
    url: str
    body: str


class _Provider(Protocol):
    async def search(self, query: str) -> list[_Source]: ...


class _SearXNG:
    def __init__(self, session: aiohttp.ClientSession, settings: _Config):
        base = settings.endpoint.strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ValueError("searxng requires an http(s) endpoint")
        self._session = session
        self._url = f"{base}/search"
        self._language = settings.language

    async def search(self, query: str) -> list[_Source]:
        params = {"q": query, "format": "json"}
        if self._language:
            params["language"] = self._language
        async with self._session.get(self._url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        return [
            _Source(str(item.get("title") or url), url, str(item.get("content") or ""))
            for item in data.get("results") or []
            if isinstance(url := item.get("url"), str)
        ]


class _DeepSeek:
    def __init__(self, session: aiohttp.ClientSession, settings: _Config):
        base = settings.endpoint.strip().rstrip("/") or _DEEPSEEK_ENDPOINT
        if not base.startswith(("http://", "https://")):
            raise ValueError("deepseek requires an http(s) endpoint")
        key = settings.api_key.get_secret_value()
        if not key:
            raise ValueError("deepseek requires api_key")
        self._session = session
        self._base = f"{base}/v1/tools"
        self._headers = {"Authorization": f"Bearer {key}"}
        self._count = settings.max_results
        self._content = settings.fetch_results
        self._max_chars = settings.max_chars

    async def _post(self, tool: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        async with self._session.post(
            f"{self._base}/{tool}", json=payload, headers=self._headers
        ) as resp:
            if resp.status == 404:
                return None
            if resp.status >= 400:
                raise APIResponseError(f"deepseek {tool} failed", status=resp.status)
            return await resp.json(content_type=None)

    async def search(self, query: str) -> list[_Source]:
        data = await self._post(
            "search",
            {
                "query": query[:128],
                "count": self._count,
                "need_content": self._content,
            },
        )
        return [
            _Source(
                str(item.get("title") or url),
                url,
                str(
                    item.get("content")
                    or item.get("summary")
                    or item.get("snippet")
                    or ""
                )[: self._max_chars],
            )
            for item in (data or {}).get("results") or []
            if isinstance(url := item.get("url"), str)
        ]

    async def fetch(self, url: str) -> _Source | None:
        data = await self._post("fetch", {"url": url})
        if not data or not (content := data.get("content")):
            return None
        title = data.get("title") or URL(url).host or url
        return _Source(str(title), url, str(content)[: self._max_chars])


_PROVIDERS: dict[str, Callable[[aiohttp.ClientSession, _Config], _Provider]] = {
    "searxng": _SearXNG,
    "deepseek": _DeepSeek,
}


def _compact(text: str) -> str:
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _extract(body: bytes, *, html: bool) -> tuple[str, str]:
    if not html:
        return "", _compact(body.decode("utf-8", "replace"))
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(_NOISE):
        tag.decompose()
    return title, _compact(soup.get_text("\n"))


class WebPlugin(PluginBase):
    api_version = 4
    config_class = _Config
    settings: _Config

    def __init__(self, context):
        super().__init__(context)
        self._cache = TTLCache[tuple[str, str], Any](
            maxsize=256, ttl=max(self.settings.cache_ttl, 1)
        )

    async def initialize(self) -> bool:
        client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.settings.timeout),
            headers={"User-Agent": USER_AGENT},
        )
        self._register_resource(client)
        try:
            self._provider = _PROVIDERS[self.settings.provider](client, self.settings)
        except ValueError as e:
            logger.error(f"Web plugin provider unavailable: {e}")
            return False
        self._log_plugin_action("initialized", f"provider={self.settings.provider}")
        return True

    @tool(
        "Search the web for real-time information, news, or uncertain facts. "
        "Returns search results with source links.",
        access="followers",
        external=True,
    )
    async def search(
        self,
        event: MessageEvent | MentionEvent,
        query: Annotated[
            str,
            Field(
                description="Concise search keywords in a language suited to the search"
            ),
        ],
    ) -> str:
        sources = await self._collect(query.strip())
        if sources:
            return self._render(sources)
        empty = self.settings.empty_prompt.strip() or _EMPTY_PROMPT
        return f"<web_results>{empty}{_CLOSING_TAG}"

    @tool(
        "Read the main text content of a web page URL",
        access="followers",
        external=True,
    )
    async def fetch(
        self,
        event: MessageEvent | MentionEvent,
        url: Annotated[str, Field(description="http(s) page URL")],
    ) -> str:
        if page := await self._page(url.strip()):
            return self._render([page])
        return "Cannot read this page: the URL is unreachable, blocked, or not a text page."

    async def _collect(self, query: str) -> list[_Source]:
        hits = await self._search(query)
        if self.settings.fetch_results and not isinstance(self._provider, _DeepSeek):
            fetched = await asyncio.gather(*(self._page(h.url) for h in hits))
            hits = [
                replace(h, body=p.body) if p else h
                for h, p in zip(hits, fetched, strict=True)
            ]
        return hits

    async def _cached(self, key: tuple[str, str], factory) -> Any:
        if not self.settings.cache_ttl:
            return await factory()
        if (hit := self._cache.get(key)) is not None:
            return hit
        if value := await factory():
            self._cache[key] = value
        return value

    async def _search(self, query: str) -> list[_Source]:
        try:
            return await self._cached(("search", query), lambda: self._query(query))
        except Exception as e:
            logger.warning(f"Web search failed: {e!r}")
            return []

    async def _query(self, query: str) -> list[_Source]:
        hits: list[_Source] = []
        for hit in await self._provider.search(query):
            if not self._allowed(hit.url):
                continue
            if not self.settings.fetch_results:
                hit = replace(hit, body=hit.body[:_SNIPPET_CHARS])
            hits.append(hit)
            if len(hits) == self.settings.max_results:
                break
        return hits

    async def _page(self, url: str) -> _Source | None:
        try:
            return await self._cached(("page", url), lambda: self._fetch(url))
        except Exception as e:
            logger.warning(f"Web fetch failed: {url}: {e!r}")
            return None

    def _allowed(self, url: str | URL) -> bool:
        try:
            parsed = URL(url)
        except ValueError:
            return False
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            return False
        host = parsed.host.casefold()

        def listed(domains: tuple[str, ...]) -> bool:
            return any(
                host == (d := item.casefold()) or host.endswith(f".{d}")
                for item in domains
            )

        if listed(self.settings.block_domains):
            return False
        return not self.settings.allow_domains or listed(self.settings.allow_domains)

    async def _fetch(self, url: str) -> _Source | None:
        if isinstance(self._provider, _DeepSeek):
            return await self._provider.fetch(url) if self._allowed(url) else None
        http = self.context.http
        async with (
            asyncio.timeout(self.settings.timeout),
            http.open(url, allow=self._allowed) as resp,
        ):
            kind = resp.content_type
            html = "html" in kind
            if resp.status != 200 or not (html or kind.startswith("text/")):
                return None
            body = await http.read(resp, int(self.settings.max_bytes), truncate=True)
            host = resp.url.host
        title, text = await asyncio.to_thread(_extract, body, html=html)
        return _Source(title or host or url, url, text[: self.settings.max_chars])

    def _render(self, sources: list[_Source]) -> str:
        budget = self.settings.max_chars
        blocks: list[str] = []
        for index, source in enumerate(sources, 1):
            body = source.body[: budget // (len(sources) - index + 1)]
            budget -= len(body)
            blocks.append(
                f"[{index}] {source.title}\ncite: ?[*]({source.url})\n{body}".rstrip()
            )
        date = datetime.now().astimezone().date().isoformat()
        content = "\n\n".join(blocks).replace(_CLOSING_TAG, "")
        prompt = self.settings.prompt.strip() or _PROMPT
        return f'<web_results date="{date}">\n{prompt}\n\n{content}\n{_CLOSING_TAG}'


plugin = WebPlugin
