import asyncio
import calendar
import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple

import aiohttp
import feedparser
from bs4 import BeautifulSoup
from loguru import logger
from pydantic import Field, field_validator

from twipsybot.plugin import (
    USER_AGENT,
    AutoPostEvent,
    AutoPostResult,
    LineText,
    PluginBase,
    PluginConfig,
    PromptModificationResult,
    When,
)

_TOPIC_SUFFIXES = {".md", ".txt"}
_RSS_TIMEOUT = aiohttp.ClientTimeout(total=60)
_RSS_RECENT_KEYS_LIMIT = 2000
_LIST_AI_PREFIX = "Write about the topic: {topic}\n\n"
_RSS_AI_PREFIX = (
    "Use the following article as reference only; do not include links.\n"
    "<article>\nTitle: {title}\nSummary: {summary}\n</article>\n\n"
)


class _Config(PluginConfig):
    source: Literal["list", "rss"] = "list"
    topics: Annotated[tuple[str, ...], When("source", "list")] = Field(
        (), validation_alias="list", description="topic per line, or prompts/*.md"
    )
    list_ai_prefix: Annotated[str, When("source", "list")] = Field(
        "", description=_LIST_AI_PREFIX
    )
    list_start_line: Annotated[int, When("source", "list")] = Field(1, ge=1)
    rss_list: Annotated[tuple[str, ...], LineText, When("source", "rss")] = Field(
        default=(), description="https://example.com/feed.xml"
    )
    rss_ai: Annotated[bool, When("source", "rss")] = False
    rss_post_mode: Annotated[Literal["batch", "rotate"], When("source", "rss")] = (
        "rotate"
    )
    rss_ai_prefix: Annotated[str, When("source", "rss")] = Field(
        "", description=_RSS_AI_PREFIX
    )

    @field_validator("topics", mode="before")
    @classmethod
    def _split_topics(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [s for line in value.splitlines() if (s := line.strip())]
        return value


class _Entry(NamedTuple):
    ts: int
    key: str
    title: str
    link: str
    summary: str
    feed_idx: int
    entry_idx: int


class TopicsPlugin(PluginBase):
    config_class = _Config
    settings: _Config

    def __init__(self, context):
        super().__init__(context)
        self.topics = []
        self._pending_rss: dict[str, list[tuple[str, int | None]]] = {}

    async def initialize(self) -> bool:
        try:
            if self.settings.source == "rss":
                details = f"RSS feeds: {len(self.settings.rss_list)}"
            else:
                self.topics = self._configured_topics()
                if not self.topics:
                    raise ValueError("list is empty")
                if await self.context.storage.get("last_used_line") is None:
                    initial = (self.settings.list_start_line - 1) % len(self.topics)
                    await self.context.storage.set("last_used_line", str(initial))
                details = f"Custom topics: {len(self.topics)}"
            self._log_plugin_action("initialized", details)
            return True
        except Exception as e:
            logger.error(f"Topics plugin initialization failed: {e}")
            return False

    async def on_auto_post(
        self, _event: AutoPostEvent
    ) -> AutoPostResult | PromptModificationResult | None:
        try:
            if self.settings.source == "rss":
                if contents := await self._get_next_rss_posts():
                    self._log_plugin_action("direct post", f"count={len(contents)}")
                    return {"contents": contents}
                return None
            topic = await self._get_next_topic()
            return {
                "prompt": (
                    self.settings.list_ai_prefix.strip() or _LIST_AI_PREFIX
                ).format(topic=topic),
            }
        except Exception as e:
            logger.error(f"Topics plugin auto-post hook failed: {e}")
            return None

    def _configured_topics(self) -> list[str]:
        topics: list[str] = []
        for entry in self.settings.topics:
            path = Path(entry)
            if (
                path.parts[:1] == ("prompts",)
                and path.suffix.lower() in _TOPIC_SUFFIXES
                and ".." not in path.parts
            ):
                try:
                    topics += self._read_lines(path)
                except OSError as e:
                    logger.warning(f"Failed to read topics file {entry}: {e}")
            else:
                topics.append(entry)
        return topics

    @staticmethod
    def _read_lines(path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        return [s for line in text.splitlines() if (s := line.strip())]

    async def _get_next_rss_posts(self) -> list[str]:
        self._pending_rss.clear()
        urls = list(self.settings.rss_list)
        if not urls:
            logger.warning("RSS source enabled but rss_list is empty")
            return []

        if self.settings.rss_post_mode == "rotate":
            return await self._get_next_rss_posts_rotate(urls)

        recent_keys = await self._get_recent_rss_keys()
        recent_set = set(recent_keys)
        candidates = await self._fetch_all_rss_candidates(urls)
        selected = self._select_latest_per_feed(urls, candidates, recent_set)
        if not selected:
            return []

        contents = await self._render_selected_rss_entries(selected)
        for content, entry in zip(contents, selected, strict=True):
            self._pending_rss.setdefault(content, []).append((entry.key, None))
        return contents

    async def _get_next_rss_posts_rotate(self, urls: list[str]) -> list[str]:
        recent_keys = await self._get_recent_rss_keys()
        recent_set = set(recent_keys)
        start_idx = await self._get_stored_int("rss_last_feed_idx")
        if start_idx >= len(urls):
            start_idx = 0

        async with self._rss_session() as session:
            for step in range(len(urls)):
                feed_idx = (start_idx + step) % len(urls)
                url = urls[feed_idx]
                try:
                    candidates = await self._fetch_rss_candidates(
                        session, url, feed_idx=feed_idx
                    )
                except Exception as e:
                    logger.warning(f"RSS fetch failed: {e}")
                    candidates = []

                filtered = [c for c in candidates if c.key not in recent_set]
                best = self._pick_latest_entry(filtered)
                if not best:
                    continue

                contents = await self._render_selected_rss_entries([best])
                if contents:
                    self._pending_rss.setdefault(contents[0], []).append(
                        (best.key, (feed_idx + 1) % len(urls))
                    )
                return contents

        await self._set_storage_value(
            "rss_last_feed_idx", str((start_idx + 1) % len(urls))
        )
        return []

    @staticmethod
    def _rss_session() -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            timeout=_RSS_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )

    async def _fetch_all_rss_candidates(self, urls: list[str]) -> list[_Entry]:
        async with self._rss_session() as session:
            tasks = [
                self._fetch_rss_candidates(session, url, feed_idx=i)
                for i, url in enumerate(urls)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        candidates: list[_Entry] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(f"RSS fetch failed: {result}")
            else:
                candidates.extend(result)
        return candidates

    @staticmethod
    def _select_latest_per_feed(
        urls: list[str], candidates: list[_Entry], recent_set: set[str]
    ) -> list[_Entry]:
        by_feed_idx: dict[int, list[_Entry]] = {}
        for c in candidates:
            if c.key in recent_set:
                continue
            by_feed_idx.setdefault(c.feed_idx, []).append(c)

        selected: list[_Entry] = []
        for feed_idx in range(len(urls)):
            best = TopicsPlugin._pick_latest_entry(by_feed_idx.get(feed_idx))
            if best:
                selected.append(best)
        return selected

    @staticmethod
    def _pick_latest_entry(entries: list[_Entry] | None) -> _Entry | None:
        if not entries:
            return None
        return max(entries, key=lambda c: (c.ts, -c.entry_idx))

    async def _render_selected_rss_entries(self, selected: list[_Entry]) -> list[str]:
        contents: list[str] = []
        for entry in selected:
            primary = await self._render_rss_primary_text(entry)
            contents.append(f"📡 {primary}\n\n📎 {entry.link}")
        return contents

    async def on_auto_post_published(self, content: str) -> None:
        pending = self._pending_rss.get(content)
        if not pending:
            return
        key, next_feed_idx = pending.pop(0)
        if not pending:
            self._pending_rss.pop(content, None)
        recent_keys = await self._get_recent_rss_keys()
        updated = self._append_recent_key(
            recent_keys, key, limit=_RSS_RECENT_KEYS_LIMIT
        )
        if not await self._set_storage_value("rss_recent_keys", json.dumps(updated)):
            raise RuntimeError("failed to persist published RSS entry")
        if next_feed_idx is not None and not await self._set_storage_value(
            "rss_last_feed_idx", str(next_feed_idx)
        ):
            raise RuntimeError("failed to persist RSS feed position")

    async def _render_rss_primary_text(self, entry: _Entry) -> str:
        if self.settings.rss_ai:
            return await self._rewrite_entry(entry)
        return entry.summary or entry.title

    async def _fetch_rss_candidates(
        self, session: aiohttp.ClientSession, url: str, *, feed_idx: int
    ) -> list[_Entry]:
        try:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    raise ValueError(f"{url} HTTP {resp.status}")
                raw = await resp.read()
        except Exception as e:
            raise ValueError(f"{url} fetch error: {e}") from e

        parsed = feedparser.parse(raw)
        entries = getattr(parsed, "entries", None) or []
        out: list[_Entry] = []
        for entry_idx, entry in enumerate(entries[:20]):
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            link = str(entry.get("link") or "").strip()
            if not title or not link:
                continue
            out.append(
                _Entry(
                    ts=self._get_entry_timestamp(entry),
                    key=self._make_entry_key(url, entry),
                    title=title,
                    link=link,
                    summary=self._entry_summary(entry),
                    feed_idx=feed_idx,
                    entry_idx=entry_idx,
                )
            )
        return out

    @staticmethod
    def _entry_summary(entry: dict[str, Any]) -> str:
        text = str(entry.get("summary") or "")
        if "<" in text or "&" in text:
            text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
        return " ".join(text.split())[:1200]

    @staticmethod
    def _get_entry_timestamp(entry: dict[str, Any]) -> int:
        for k in ("published_parsed", "updated_parsed"):
            t = entry.get(k)
            if t:
                try:
                    return int(calendar.timegm(t))
                except (TypeError, ValueError, OverflowError):
                    continue
        return 0

    @staticmethod
    def _make_entry_key(feed_url: str, entry: dict[str, Any]) -> str:
        raw = entry.get("id") or entry.get("guid") or entry.get("link")
        base = f"{feed_url}\n{raw}".encode(errors="ignore")
        return hashlib.sha256(base).hexdigest()

    async def _rewrite_entry(self, entry: _Entry) -> str:
        try:
            prompt = (self.settings.rss_ai_prefix.strip() or _RSS_AI_PREFIX).format(
                title=entry.title, link=entry.link, summary=entry.summary
            )
        except Exception as e:
            logger.warning(f"Invalid rss_ai_prefix format: {e}")
            return entry.title
        try:
            text = await self.context.bot.generate_auto_post(prompt)
        except Exception as e:
            logger.warning(f"RSS title rewrite failed: {e}")
            return entry.title
        return (text or "").strip() or entry.title

    async def _get_recent_rss_keys(self) -> list[str]:
        try:
            raw = await self.context.storage.get("rss_recent_keys")
            if not raw:
                return []
            obj = json.loads(raw)
            if not isinstance(obj, list):
                return []
            return [x for x in obj if isinstance(x, str) and x]
        except Exception as e:
            logger.warning(f"Failed to load rss_recent_keys: {e}")
            return []

    async def _get_stored_int(self, key: str) -> int:
        try:
            raw = await self.context.storage.get(key)
            return max(0, int(raw)) if raw else 0
        except Exception as e:
            logger.warning(f"Failed to load {key}: {e}")
            return 0

    async def _set_storage_value(self, key: str, value: str) -> bool:
        try:
            await self.context.storage.set(key, value)
            return True
        except Exception as e:
            logger.warning(f"Failed to save {key}: {e}")
            return False

    @staticmethod
    def _append_recent_key(keys: list[str], key: str, *, limit: int) -> list[str]:
        if key in keys:
            keys = [k for k in keys if k != key]
        keys.append(key)
        if limit > 0 and len(keys) > limit:
            keys = keys[-limit:]
        return keys

    async def _get_next_topic(self) -> str:
        try:
            last_used_line = await self._get_stored_int("last_used_line")
            index = last_used_line % len(self.topics)
            topic = self.topics[index]
            await self._set_storage_value(
                "last_used_line", str((index + 1) % len(self.topics))
            )
            self._log_plugin_action("selected topic", f"{topic} (line: {index + 1})")
            return topic
        except Exception as e:
            logger.warning(f"Failed to get next topic: {e}")
            return self.topics[0]


plugin = TopicsPlugin
