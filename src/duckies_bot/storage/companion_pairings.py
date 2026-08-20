"""SQLite persistence for authenticated companion installations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
import hashlib
from pathlib import Path
import secrets
import sqlite3
from typing import TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CompanionPairing:
    token_hash: str
    discord_user_id: int
    guild_id: int
    channel_id: int


class CompanionPairingRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    async def initialize(self) -> None:
        await self._run(self._initialize_sync)

    async def create(
        self,
        discord_user_id: int,
        guild_id: int,
        channel_id: int,
    ) -> tuple[CompanionPairing, str]:
        token = secrets.token_urlsafe(32)
        pairing = CompanionPairing(
            _token_hash(token),
            discord_user_id,
            guild_id,
            channel_id,
        )
        await self._run(self._create_sync, pairing)
        return pairing, token

    async def authenticate(self, token: str) -> CompanionPairing | None:
        if not token or len(token) > 256:
            return None
        return await self._run(self._authenticate_sync, _token_hash(token))

    async def disable(self, discord_user_id: int, guild_id: int) -> bool:
        return await self._run(self._disable_sync, discord_user_id, guild_id)

    async def record_match(self, pairing: CompanionPairing, match_id: int) -> bool:
        """Record a submission, returning false when it was already accepted."""

        return await self._run(
            self._record_match_sync,
            pairing.discord_user_id,
            pairing.guild_id,
            match_id,
        )

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        return await asyncio.to_thread(operation, *args)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_sync(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS companion_pairings (
                    token_hash TEXT PRIMARY KEY,
                    discord_user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TEXT,
                    UNIQUE(discord_user_id, guild_id)
                );

                CREATE TABLE IF NOT EXISTS companion_match_submissions (
                    discord_user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    match_id INTEGER NOT NULL,
                    accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(discord_user_id, guild_id, match_id)
                );
                """
            )
            connection.commit()

    def _create_sync(self, pairing: CompanionPairing) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO companion_pairings (
                    token_hash, discord_user_id, guild_id, channel_id, enabled
                ) VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(discord_user_id, guild_id) DO UPDATE SET
                    token_hash = excluded.token_hash,
                    channel_id = excluded.channel_id,
                    enabled = 1,
                    created_at = CURRENT_TIMESTAMP,
                    last_used_at = NULL
                """,
                (
                    pairing.token_hash,
                    pairing.discord_user_id,
                    pairing.guild_id,
                    pairing.channel_id,
                ),
            )
            connection.commit()

    def _authenticate_sync(self, token_hash: str) -> CompanionPairing | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT token_hash, discord_user_id, guild_id, channel_id
                FROM companion_pairings
                WHERE token_hash = ? AND enabled = 1
                """,
                (token_hash,),
            ).fetchone()
            if row is not None:
                connection.execute(
                    """
                    UPDATE companion_pairings
                    SET last_used_at = CURRENT_TIMESTAMP
                    WHERE token_hash = ?
                    """,
                    (token_hash,),
                )
                connection.commit()
        return CompanionPairing(*row) if row is not None else None

    def _disable_sync(self, discord_user_id: int, guild_id: int) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE companion_pairings SET enabled = 0
                WHERE discord_user_id = ? AND guild_id = ? AND enabled = 1
                """,
                (discord_user_id, guild_id),
            )
            connection.commit()
        return cursor.rowcount > 0

    def _record_match_sync(
        self,
        discord_user_id: int,
        guild_id: int,
        match_id: int,
    ) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO companion_match_submissions (
                    discord_user_id, guild_id, match_id
                ) VALUES (?, ?, ?)
                """,
                (discord_user_id, guild_id, match_id),
            )
            connection.commit()
        return cursor.rowcount > 0


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
