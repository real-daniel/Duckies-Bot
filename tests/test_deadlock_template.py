"""Tests for the local scouting report preview cache."""

from pathlib import Path
import tempfile
import unittest

from duckies_bot.features.deadlock import ScoutTemplateCache, sample_scout_template
from duckies_bot.features.deadlock.formatter import build_scout_overview_embeds


class ScoutTemplateCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_cache_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = ScoutTemplateCache(Path(directory) / "missing.json")
            self.assertIsNone(await cache.load())

    async def test_report_round_trips_through_local_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "scout.json"
            cache = ScoutTemplateCache(path)
            expected = sample_scout_template()

            await cache.save(expected)
            actual = await cache.load()

            self.assertEqual(actual, expected)
            self.assertTrue(path.exists())

    async def test_fallback_exercises_full_scoreboard_layout(self) -> None:
        scout = sample_scout_template()
        embeds = build_scout_overview_embeds(scout)

        self.assertEqual(len(scout.players), 12)
        self.assertEqual([len(embed.fields) for embed in embeds], [0, 6, 6])
