"""SQLite persistence for short-lived Valve broadcast URLs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time
from typing import TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StoredBroadcastURL:
    url: str
    expires_at: float


class BroadcastURLRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    async def initialize(self) -> None:
        await self._run(self._initialize_sync)

    async def get(self, match_id: int) -> StoredBroadcastURL | None:
        return await self._run(self._get_sync, match_id, time.time())

    async def put(self, match_id: int, url: str, expires_at: float) -> None:
        await self._run(self._put_sync, match_id, url, expires_at)

    async def delete(self, match_id: int) -> None:
        await self._run(self._delete_sync, match_id)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        return await asyncio.to_thread(operation, *args)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.database_path, timeout=10)

    def _initialize_sync(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deadlock_broadcast_urls (
                    match_id INTEGER PRIMARY KEY,
                    broadcast_url TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            connection.commit()

    def _get_sync(self, match_id: int, now: float) -> StoredBroadcastURL | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT broadcast_url, expires_at
                FROM deadlock_broadcast_urls
                WHERE match_id = ? AND expires_at > ?
                """,
                (match_id, now),
            ).fetchone()
            if row is None:
                connection.execute(
                    "DELETE FROM deadlock_broadcast_urls WHERE match_id = ?",
                    (match_id,),
                )
                connection.commit()
                return None
        return StoredBroadcastURL(url=row[0], expires_at=row[1])

    def _put_sync(self, match_id: int, url: str, expires_at: float) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO deadlock_broadcast_urls (match_id, broadcast_url, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    broadcast_url = excluded.broadcast_url,
                    expires_at = excluded.expires_at
                """,
                (match_id, url, expires_at),
            )
            connection.commit()

    def _delete_sync(self, match_id: int) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM deadlock_broadcast_urls WHERE match_id = ?",
                (match_id,),
            )
            connection.commit()
