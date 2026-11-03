import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

Row = Sequence[Any]

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;
CREATE TABLE IF NOT EXISTS plugin_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_name TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plugin_name, key)
);
CREATE TABLE IF NOT EXISTS response_limit_state (
    user_id TEXT PRIMARY KEY,
    last_reply_ts REAL,
    turns INTEGER NOT NULL DEFAULT 0,
    blocked_until_ts REAL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_response_limit_state_updated
    ON response_limit_state(updated_at);
CREATE TABLE IF NOT EXISTS auto_post_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    post_date TEXT NOT NULL,
    posts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scheduled_notes (
    id TEXT PRIMARY KEY,
    run_at TEXT NOT NULL,
    text TEXT NOT NULL,
    visibility TEXT,
    local_only INTEGER
);
"""


class DBManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            if self._connection is not None:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(
                self.db_path, timeout=30.0, isolation_level=None
            )
            try:
                await connection.executescript(_SCHEMA)
            except BaseException:
                await asyncio.shield(connection.close())
                raise
            self._connection = connection
        logger.info(f"DB manager initialized: {self.db_path}")

    async def close(self) -> None:
        async with self._lock:
            connection, self._connection = self._connection, None
            if connection is not None:
                await connection.close()
        logger.debug("DB manager closed")

    def _get_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("database is not initialized")
        return self._connection

    async def _fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[Row]:
        async with (
            self._lock,
            self._get_connection().execute(query, params) as cursor,
        ):
            return list(await cursor.fetchall())

    async def _fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> Row | None:
        return next(iter(await self._fetch_all(query, params)), None)

    async def _execute_write(self, query: str, params: tuple[Any, ...] = ()) -> int:
        async with (
            self._lock,
            self._get_connection().execute(query, params) as cursor,
        ):
            return cursor.rowcount

    async def get_plugin_data(self, plugin_name: str, key: str) -> str | None:
        row = await self._fetch_one(
            "SELECT value FROM plugin_data WHERE plugin_name = ? AND key = ?",
            (plugin_name, key),
        )
        return row[0] if row else None

    async def set_plugin_data(self, plugin_name: str, key: str, value: str) -> None:
        await self._execute_write(
            "INSERT OR REPLACE INTO plugin_data (plugin_name, key, value) VALUES (?, ?, ?)",
            (plugin_name, key, value),
        )

    async def delete_plugin_data(self, plugin_name: str, key: str | None = None) -> int:
        if key:
            query = "DELETE FROM plugin_data WHERE plugin_name = ? AND key = ?"
            params = (plugin_name, key)
        else:
            query = "DELETE FROM plugin_data WHERE plugin_name = ?"
            params = (plugin_name,)
        return await self._execute_write(query, params)

    async def get_response_limit_state(
        self, user_id: str
    ) -> tuple[float | None, int, float | None] | None:
        row = await self._fetch_one(
            "SELECT last_reply_ts, turns, blocked_until_ts FROM response_limit_state "
            "WHERE user_id = ?",
            (user_id,),
        )
        return (row[0], row[1], row[2]) if row else None

    async def set_response_limit_state(
        self,
        *,
        user_id: str,
        last_reply_ts: float | None,
        turns: int,
        blocked_until_ts: float | None,
    ) -> None:
        await self._execute_write(
            "INSERT OR REPLACE INTO response_limit_state "
            "(user_id, last_reply_ts, turns, blocked_until_ts) VALUES (?, ?, ?, ?)",
            (user_id, last_reply_ts, turns, blocked_until_ts),
        )

    async def get_auto_post_state(self) -> tuple[str, int] | None:
        row = await self._fetch_one("SELECT post_date, posts FROM auto_post_state")
        return (row[0], row[1]) if row else None

    async def set_auto_post_state(self, post_date: str, posts: int) -> None:
        await self._execute_write(
            "INSERT OR REPLACE INTO auto_post_state VALUES (1, ?, ?)",
            (post_date, posts),
        )

    async def maintain(self, max_age_days: int) -> None:
        if max_age_days >= 0:
            await self._execute_write(
                "DELETE FROM response_limit_state WHERE updated_at < datetime('now', ?)",
                (f"-{max_age_days} days",),
            )
        await self._execute_write("PRAGMA optimize")

    async def add_scheduled_note(
        self,
        note_id: str,
        run_at: str,
        text: str,
        visibility: str | None,
        local_only: bool | None,
    ) -> None:
        await self._execute_write(
            "INSERT INTO scheduled_notes VALUES (?, ?, ?, ?, ?)",
            (note_id, run_at, text, visibility, local_only),
        )

    async def delete_scheduled_note(self, note_id: str) -> None:
        await self._execute_write(
            "DELETE FROM scheduled_notes WHERE id = ?", (note_id,)
        )

    async def get_scheduled_notes(self) -> list[Row]:
        return await self._fetch_all("SELECT * FROM scheduled_notes")
