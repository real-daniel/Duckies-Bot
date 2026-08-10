"""Tests for environment-independent configuration validation."""

import unittest

from duckies_bot.config import Settings


class SettingsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        settings = Settings(discord_token="token")
        self.assertEqual(settings.tarkov_api_url, "https://json.tarkov.dev")
        self.assertEqual(settings.http_timeout_seconds, 30.0)
        self.assertIsNone(settings.discord_guild_id)

    def test_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            Settings(discord_token=" ")
        with self.assertRaises(ValueError):
            Settings(discord_token="token", http_timeout_seconds=0)
        with self.assertRaises(ValueError):
            Settings(discord_token="token", discord_guild_id=-1)
