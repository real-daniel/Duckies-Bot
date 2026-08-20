"""Tests for reusable Discord-to-Steam account links."""

import tempfile
import unittest
from pathlib import Path

from duckies_bot.features.accounts import SteamIDError, parse_steam_id
from duckies_bot.storage import (
    AccountAlreadyLinkedError,
    BroadcastURLRepository,
    SteamLinkRepository,
)


ACCOUNT_ID = 123_456
STEAM_ID64 = 76_561_197_960_389_184


class SteamIDTests(unittest.TestCase):
    def test_parses_supported_identifier_forms(self) -> None:
        values = (
            str(ACCOUNT_ID),
            str(STEAM_ID64),
            "[U:1:123456]",
            "STEAM_1:0:61728",
            f"https://steamcommunity.com/profiles/{STEAM_ID64}/",
        )
        for value in values:
            with self.subTest(value=value):
                account = parse_steam_id(value)
                self.assertEqual(account.account_id, ACCOUNT_ID)
                self.assertEqual(account.steam_id64, STEAM_ID64)

    def test_rejects_vanity_and_invalid_identifiers(self) -> None:
        for value in ("", "gaben", "https://steamcommunity.com/id/gaben", "0"):
            with self.subTest(value=value), self.assertRaises(SteamIDError):
                parse_steam_id(value)


class SteamLinkRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = SteamLinkRepository(Path(self.temp_dir.name) / "bot.sqlite3")
        await self.repository.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_link_replace_read_and_unlink(self) -> None:
        first = parse_steam_id("123456")
        second = parse_steam_id("654321")
        await self.repository.link(100, first)
        self.assertEqual(await self.repository.get(100), first)

        await self.repository.link(100, second)
        self.assertEqual(await self.repository.get(100), second)
        self.assertTrue(await self.repository.unlink(100))
        self.assertIsNone(await self.repository.get(100))
        self.assertFalse(await self.repository.unlink(100))

    async def test_one_steam_account_cannot_belong_to_two_discord_users(self) -> None:
        account = parse_steam_id("123456")
        await self.repository.link(100, account)
        with self.assertRaises(AccountAlreadyLinkedError):
            await self.repository.link(200, account)


class BroadcastURLRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = BroadcastURLRepository(
            Path(self.temp_dir.name) / "bot.sqlite3"
        )
        await self.repository.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_persists_unexpired_url_and_removes_expired_url(self) -> None:
        import time

        await self.repository.put(
            123,
            "https://relay.example.test/match/123",
            time.time() + 60,
        )
        stored = await self.repository.get(123)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.url, "https://relay.example.test/match/123")
        await self.repository.delete(123)
        self.assertIsNone(await self.repository.get(123))

        await self.repository.put(
            456,
            "https://relay.example.test/match/456",
            time.time() - 1,
        )
        self.assertIsNone(await self.repository.get(456))
