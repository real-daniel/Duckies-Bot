"""Render a representative live scoreboard without starting Discord."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from duckies_bot.features.deadlock.models import (  # noqa: E402
    HeroSummary,
    ItemSummary,
    LiveMatchSnapshot,
    LivePlayer,
)
from duckies_bot.features.deadlock.scoreboard import (  # noqa: E402
    render_discord_scoreboard,
    render_discord_scoreboard_page,
    render_live_scoreboard,
)


NAMES = (
    "Poge",
    "Dvoid",
    "Punished_Log",
    "DRGN",
    "Abwems",
    "Jukuren",
    "Buggy",
    "Tater",
    "Skrilla",
    "kuromi jelly snack",
    "Updøg",
    "Fytox",
)
HEROES = (
    "Seven",
    "The Doorman",
    "Mina",
    "Silver",
    "Abrams",
    "Apollo",
    "Mo & Krill",
    "Mirage",
    "Bebop",
    "Drifter",
    "Paradox",
    "Pocket",
)


def main() -> None:
    players = tuple(
        LivePlayer(
            account_id=800_000 + index,
            steam_name=name,
            hero_id=index,
            team=2 if index <= 6 else 3,
            player_slot=index,
            kills=(index * 5 + 3) % 19,
            deaths=(index * 3 + 2) % 14,
            assists=(index * 7 + 4) % 32,
            net_worth=39_000 + index * 1_750 + index % 3 * 2_100,
            hero_damage=21_000 + index * 3_350,
            objective_damage=1_100 + index * 930,
            hero_healing=500 + index * 1_840,
            upgrades=tuple(range(index * 10, index * 10 + 10 + index % 5)),
        )
        for index, name in enumerate(NAMES, start=1)
    )
    snapshot = LiveMatchSnapshot(
        match_id=99_138_829,
        game_time_seconds=2_440,
        players=players,
        heroes=tuple(
            HeroSummary(index, name, None)
            for index, name in enumerate(HEROES, start=1)
        ),
        items=tuple(
            ItemSummary(
                item_id=item_id,
                name=f"Preview item {item_id}",
                icon_url=None,
                slot_type=("weapon", "vitality", "spirit")[item_id % 3],
                tier=1 + item_id % 4,
                cost=800,
                shopable=True,
            )
            for item_id in sorted(
                {item_id for player in players for item_id in player.upgrades}
            )
        ),
    )
    output = ROOT / "data" / "deadlock_watch_scoreboard_preview.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(render_live_scoreboard(snapshot, highlighted_account_id=800_010))
    print(output)
    discord_output = ROOT / "data" / "deadlock_watchtest_scoreboard_preview.png"
    discord_output.write_bytes(
        render_discord_scoreboard_page(snapshot, highlighted_account_id=800_010)
    )
    print(discord_output)
    for page in ("combat", "economy", "builds", "player"):
        page_output = ROOT / "data" / f"deadlock_watchtest_{page}_preview.png"
        page_output.write_bytes(
            render_discord_scoreboard_page(
                snapshot,
                page,
                highlighted_account_id=800_010,
                selected_account_id=800_010,
            )
        )
        print(page_output)


if __name__ == "__main__":
    main()
