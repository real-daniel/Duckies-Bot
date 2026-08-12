"""SQLite persistence for Discord-to-Steam account links."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
import sqlite3
from typing import TypeVar

from ..features.accounts import SteamAccount


T = TypeVar("T")


class AccountAlreadyLinkedError(RuntimeError):
    """Raised when a Steam account belongs to another Discord user."""


class SteamLinkRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    async def initialize(self) -> None:
        await self._run(self._initialize_sync)

    async def link(self, discord_user_id: int, account: SteamAccount) -> None:
        try:
            await self._run(self._link_sync, discord_user_id, account)
        except sqlite3.IntegrityError as exc:
            raise AccountAlreadyLinkedError(
                "That Steam account is already linked to another Discord user."
            ) from exc

    async def get(self, discord_user_id: int) -> SteamAccount | None:
        return await self._run(self._get_sync, discord_user_id)

    async def unlink(self, discord_user_id: int) -> bool:
        return await self._run(self._unlink_sync, discord_user_id)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        return await asyncio.to_thread(operation, *args)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_sync(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS steam_links (
                    discord_user_id INTEGER PRIMARY KEY,
                    account_id INTEGER NOT NULL UNIQUE,
                    steam_id64 INTEGER NOT NULL UNIQUE,
                    linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()

    def _link_sync(self, discord_user_id: int, account: SteamAccount) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO steam_links (discord_user_id, account_id, steam_id64)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    steam_id64 = excluded.steam_id64,
                    linked_at = CURRENT_TIMESTAMP
                """,
                (discord_user_id, account.account_id, account.steam_id64),
            )
            connection.commit()

    def _get_sync(self, discord_user_id: int) -> SteamAccount | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT account_id, steam_id64 FROM steam_links WHERE discord_user_id = ?",
                (discord_user_id,),
            ).fetchone()
        return SteamAccount(row[0], row[1]) if row else None

    def _unlink_sync(self, discord_user_id: int) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM steam_links WHERE discord_user_id = ?",
                (discord_user_id,),
            )
            connection.commit()
        return cursor.rowcount > 0
