"""Tests for the deterministic graphical scouting report."""

from io import BytesIO
import unittest

from PIL import Image

from duckies_bot.features.deadlock.models import (
    HeroExperience,
    HeroRecord,
    HeroSummary,
    LivePlayer,
    MatchScout,
    PlayerRank,
    ScoutedPlayer,
)
from duckies_bot.features.deadlock.scout_graphic import (
    _ranked_record_lines,
    render_scout_graphic,
)


class DeadlockScoutGraphicTests(unittest.TestCase):
    def test_renders_distinct_overview_and_player_pages(self) -> None:
        scout = _scout()

        overview = render_scout_graphic(scout, highlighted_account_id=1001)
        player = render_scout_graphic(scout, 0, highlighted_account_id=1001)

        self.assertNotEqual(overview, player)
        for output in (overview, player):
            self.assertTrue(output.startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(BytesIO(output)) as image:
                self.assertEqual(image.size, (1200, 760))
                self.assertEqual(image.mode, "RGB")
            self.assertLess(len(output), 2 * 1024 * 1024)

    def test_uses_downloaded_hero_portrait(self) -> None:
        icon = BytesIO()
        Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(icon, "PNG")

        without_icon = render_scout_graphic(_scout())
        with_icon = render_scout_graphic(_scout(), hero_icons={1: icon.getvalue()})

        self.assertNotEqual(without_icon, with_icon)

    def test_rejects_invalid_player_index(self) -> None:
        with self.assertRaises(IndexError):
            render_scout_graphic(_scout(), 99)

    def test_formats_ranked_record_for_overview(self) -> None:
        player = _scout().players[0]

        self.assertEqual(_ranked_record_lines(player), ("101W - 100L", "50%"))


def _scout() -> MatchScout:
    players = []
    for index in range(1, 13):
        hero = HeroSummary(index, f"Hero {index}", None)
        experience = HeroExperience(1000 + index, index, 50 + index, 30, 999)
        players.append(
            ScoutedPlayer(
                player=LivePlayer(
                    1000 + index,
                    f"Player {index}",
                    index,
                    2 if index <= 6 else 3,
                    index,
                    0,
                    0,
                    0,
                    0,
                ),
                hero=hero,
                rank=PlayerRank(8, (index % 6) + 1),
                rank_name="Oracle",
                experience=experience,
                recent_outcomes=("W", "L", "W", "W", "L"),
                total_matches=200 + index,
                total_wins=100 + index,
                top_heroes=(HeroRecord(experience, hero),),
            )
        )
    return MatchScout(123, 65, tuple(players))
