"""Tests for the deterministic live scoreboard image."""

from io import BytesIO
import unittest

from PIL import Image

from duckies_bot.features.deadlock.models import (
    HeroSummary,
    ItemSummary,
    LiveMatchSnapshot,
    LivePlayer,
)
from duckies_bot.features.deadlock.scoreboard import (
    render_discord_scoreboard,
    render_discord_scoreboard_page,
    render_live_scoreboard,
)


class DeadlockScoreboardTests(unittest.TestCase):
    def test_renders_fixed_size_png_deterministically(self) -> None:
        snapshot = _snapshot()

        first = render_live_scoreboard(snapshot, highlighted_account_id=1001)
        second = render_live_scoreboard(snapshot, highlighted_account_id=1001)

        self.assertTrue(first.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(first, second)
        with Image.open(BytesIO(first)) as image:
            self.assertEqual(image.size, (1600, 900))
            self.assertEqual(image.mode, "RGB")
        self.assertLess(len(first), 2 * 1024 * 1024)

    def test_renders_all_stream_states(self) -> None:
        snapshot = _snapshot()

        images = {
            status: render_live_scoreboard(snapshot, stream_status=status)
            for status in ("live", "reconnecting", "unreachable", "ended")
        }

        self.assertEqual(len(set(images.values())), 4)

    def test_renders_tall_discord_scoreboard(self) -> None:
        output = render_discord_scoreboard(_snapshot(), highlighted_account_id=1001)

        with Image.open(BytesIO(output)) as image:
            self.assertEqual(image.size, (1000, 1400))
        self.assertLess(len(output), 2 * 1024 * 1024)

    def test_renders_distinct_low_density_discord_pages(self) -> None:
        snapshot = _snapshot()
        outputs = {
            page: render_discord_scoreboard_page(
                snapshot,
                page,
                selected_account_id=1001,
            )
            for page in ("overview", "combat", "economy", "builds", "player", "timeline")
        }

        self.assertEqual(len(set(outputs.values())), len(outputs))
        for output in outputs.values():
            with Image.open(BytesIO(output)) as image:
                self.assertEqual(image.size, (1200, 760))

    def test_renders_resolved_item_icon_bytes(self) -> None:
        snapshot = _snapshot()
        item_id = snapshot.players[0].upgrades[0]
        mapped = LiveMatchSnapshot(
            snapshot.match_id,
            snapshot.game_time_seconds,
            snapshot.players,
            snapshot.heroes,
            (
                ItemSummary(
                    item_id,
                    "Test Item",
                    "https://example.test/item.webp",
                    "weapon",
                    1,
                    800,
                    True,
                ),
            ),
        )
        icon_output = BytesIO()
        Image.new("RGBA", (32, 32), (255, 0, 255, 255)).save(icon_output, "PNG")

        without_icon = render_live_scoreboard(mapped)
        with_icon = render_live_scoreboard(
            mapped,
            item_icons={item_id: icon_output.getvalue()},
        )

        self.assertNotEqual(without_icon, with_icon)

    def test_renders_resolved_hero_icon_bytes(self) -> None:
        snapshot = _snapshot()
        hero_id = snapshot.players[0].hero_id
        icon_output = BytesIO()
        Image.new("RGBA", (64, 64), (0, 255, 255, 255)).save(icon_output, "PNG")

        without_icon = render_discord_scoreboard_page(snapshot)
        with_icon = render_discord_scoreboard_page(
            snapshot,
            hero_icons={hero_id: icon_output.getvalue()},
        )

        self.assertNotEqual(without_icon, with_icon)


def _snapshot() -> LiveMatchSnapshot:
    players = tuple(
        LivePlayer(
            account_id=1000 + index,
            steam_name=f"Player with a long name {index}",
            hero_id=index,
            team=2 if index <= 6 else 3,
            player_slot=index,
            kills=index,
            deaths=index // 2,
            assists=index + 4,
            net_worth=10_000 + index * 1_000,
            hero_damage=index * 4_000,
            objective_damage=index * 800,
            hero_healing=index * 500,
            upgrades=tuple(range(index + 8)),
        )
        for index in range(1, 13)
    )
    return LiveMatchSnapshot(
        match_id=123,
        game_time_seconds=1_200,
        players=players,
        heroes=tuple(
            HeroSummary(index, f"Hero {index}", None)
            for index in range(1, 13)
        ),
    )
