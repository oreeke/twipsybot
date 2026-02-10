import asyncio
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

from ..shared.config import Config
from ..shared.config_keys import ConfigKeys

__all__ = ("ConnectionPool", "DBManager")

Row = Sequence[Any]


class ConnectionPool:
    def __init__(self, db_path: str, max_connections: int = 10):
        self.db_path = db_path
        self.max_connections = max_connections
        self._pool = asyncio.Queue(maxsize=max_connections)
        self._created_connections = 0
        self._lock = asyncio.Lock()

    async def get_connection(self) -> aiosqlite.Connection:
        try:
            return self._pool.get_nowait()
        except asyncio.QueueEmpty:
            async with self._lock:
                if self._created_connections < self.max_connections:
                    conn = await aiosqlite.connect(
                        self.db_path, timeout=30.0, isolation_level=None
                    )
                    await conn.execute("PRAGMA journal_mode=WAL")
                    await conn.execute("PRAGMA synchronous=NORMAL")
                    await conn.execute("PRAGMA cache_size=10000")
                    await conn.execute("PRAGMA busy_timeout=30000")
                    self._created_connections += 1
                    return conn
            return await self._pool.get()

    async def return_connection(self, conn: aiosqlite.Connection) -> None:
        try:
            self._pool.put_nowait(conn)
        except asyncio.QueueFull:
            await conn.close()
            async with self._lock:
                self._created_connections -= 1

    async def close_all(self) -> None:
        connections = []
        while not self._pool.empty():
            try:
                connections.append(self._pool.get_nowait())
            except asyncio.QueueEmpty:
                break
        for conn in connections:
            await conn.close()
        self._created_connections = 0


class DBManager:
    def __init__(
        self,
        db_path: str | None = None,
        max_connections: int = 10,
        config: Config | None = None,
    ):
        self.config = config or Config()
        resolved_db_path = (
            db_path if db_path is not None else self.config.get(ConfigKeys.DB_PATH)
        )
        if not isinstance(resolved_db_path, str) or not resolved_db_path.strip():
            resolved_db_path = "data/twipsybot.db"
        self.db_path = Path(resolved_db_path)
        self._pool = ConnectionPool(str(self.db_path), max_connections)
        self._initialized = False

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._create_tables()
        self._initialized = True
        logger.info(f"DB manager initialized: {self.db_path}")

    async def close(self) -> None:
        await self._pool.close_all()
        logger.debug("DB manager closed")

    async def _create_tables(self) -> None:
        conn = await self._pool.get_connection()
        try:
            await self._execute_schema(conn)
        finally:
            await self._pool.return_connection(conn)

    @staticmethod
    async def _execute_schema(conn: aiosqlite.Connection) -> None:
        schema_statements = [
            """
            CREATE TABLE IF NOT EXISTS plugin_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_name TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(plugin_name, key)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS response_limit_state (
                user_id TEXT PRIMARY KEY,
                last_reply_ts REAL,
                turns INTEGER NOT NULL DEFAULT 0,
                blocked_until_ts REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ]
        index_statements = [
            "CREATE INDEX IF NOT EXISTS idx_plugin_data_name_key ON plugin_data(plugin_name, key)",
            "CREATE INDEX IF NOT EXISTS idx_response_limit_state_updated ON response_limit_state(updated_at)",
        ]
        async with conn.execute("BEGIN TRANSACTION"):
            for statement in schema_statements:
                await conn.execute(statement)
            for index_sql in index_statements:
                await conn.execute(index_sql)
            await conn.commit()

    async def _fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> Row | None:
        conn = await self._pool.get_connection()
        try:
            async with conn.execute(query, params) as cursor:
                return await cursor.fetchone()
        finally:
            await self._pool.return_connection(conn)

    async def _fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[Row]:
        conn = await self._pool.get_connection()
        try:
            async with conn.execute(query, params) as cursor:
                return list(await cursor.fetchall())
        finally:
            await self._pool.return_connection(conn)

    async def _execute_write(self, query: str, params: tuple[Any, ...] = ()) -> int:
        conn = await self._pool.get_connection()
        try:
            async with conn.execute(query, params) as cursor:
                await conn.commit()
                return cursor.rowcount
        except aiosqlite.Error as e:
            await conn.rollback()
            logger.error(f"Database write operation failed: {e}")
            raise
        finally:
            await self._pool.return_connection(conn)

    async def get_plugin_data(self, plugin_name: str, key: str) -> str | None:
        result = await self._fetch_one(
            "SELECT value FROM plugin_data WHERE plugin_name = ? AND key = ?",
            (plugin_name, key),
        )
        return result[0] if result else None

    async def set_plugin_data(self, plugin_name: str, key: str, value: str) -> None:
        await self._execute_write(
            "INSERT OR REPLACE INTO plugin_data (plugin_name, key, value, updated_at) VALUES (?, ?, ?, ?)",
            (plugin_name, key, value, datetime.now()),
        )

    async def get_response_limit_state(
        self, user_id: str
    ) -> tuple[float | None, int, float | None] | None:
        result = await self._fetch_one(
            "SELECT last_reply_ts, turns, blocked_until_ts FROM response_limit_state WHERE user_id = ?",
            (user_id,),
        )
        if not result:
            return None
        last_reply_ts, turns, blocked_until_ts = result
        turns_value = int(turns) if turns is not None else 0
        return last_reply_ts, turns_value, blocked_until_ts

    async def set_response_limit_state(
        self,
        *,
        user_id: str,
        last_reply_ts: float | None,
        turns: int,
        blocked_until_ts: float | None,
    ) -> None:
        await self._execute_write(
            """
            INSERT OR REPLACE INTO response_limit_state
                (user_id, last_reply_ts, turns, blocked_until_ts, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, last_reply_ts, int(turns), blocked_until_ts, datetime.now()),
        )

    async def cleanup_response_limit_state(
        self, *, max_age_days: int | None = None
    ) -> int:
        if max_age_days is None:
            max_age_days = self.config.get(ConfigKeys.DB_CLEAR)
        if not isinstance(max_age_days, int):
            max_age_days = 30
        if max_age_days < 0:
            return 0
        if max_age_days == 0:
            return await self._execute_write("DELETE FROM response_limit_state")
        cutoff = int(datetime.now().timestamp() - (max_age_days * 86400))
        return await self._execute_write(
            "DELETE FROM response_limit_state WHERE CAST(strftime('%s', updated_at) AS INTEGER) < ?",
            (cutoff,),
        )

    async def delete_plugin_data(self, plugin_name: str, key: str | None = None) -> int:
        if key:
            query = "DELETE FROM plugin_data WHERE plugin_name = ? AND key = ?"
            params = (plugin_name, key)
        else:
            query = "DELETE FROM plugin_data WHERE plugin_name = ?"
            params = (plugin_name,)
        return await self._execute_write(query, params)

    async def get_table_stats(self) -> dict[str, Any]:
        tables_query = "SELECT name FROM sqlite_master WHERE type='table'"
        tables_result = await self._fetch_all(tables_query)
        table_stats: dict[str, Any] = {}
        for table_row in tables_result:
            table_name = table_row[0] if table_row else None
            if not isinstance(table_name, str):
                continue
            if not table_name.replace("_", "").isalnum():
                continue
            count_query = f'SELECT COUNT(*) FROM "{table_name}"'
            count_result = await self._fetch_one(count_query)
            size_query = "SELECT SUM(pgsize) FROM dbstat WHERE name = ?"
            size_result = await self._fetch_one(size_query, (table_name,))
            size_bytes = 0
            if size_result and size_result[0]:
                size_bytes = int(size_result[0])
            row_count = 0
            if count_result and count_result[0] is not None:
                row_count = int(count_result[0])
            table_stats[table_name] = {
                "row_count": row_count,
                "size_bytes": size_bytes,
                "size_kb": round(size_bytes / 1024, 2),
                "size_mb": round(size_bytes / 1024 / 1024, 2),
            }
        return table_stats

    async def vacuum(self) -> None:
        conn = await self._pool.get_connection()
        try:
            await conn.execute("VACUUM")
            logger.debug("Database vacuum completed")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Database vacuum failed: {e}")
        finally:
            await self._pool.return_connection(conn)
