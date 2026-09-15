"""Local cache and representative fallback for scouting-format previews."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import (
    HeroExperience,
    HeroRecord,
    HeroSummary,
    LivePlayer,
    MatchScout,
    PlayerRank,
    ScoutedPlayer,
)


class ScoutTemplateCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def save(self, scout: MatchScout) -> None:
        await asyncio.to_thread(self._save_sync, scout)

    async def load(self) -> MatchScout | None:
        return await asyncio.to_thread(self._load_sync)

    def _save_sync(self, scout: MatchScout) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = _scout_to_dict(scout)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                json.dump(document, temporary, ensure_ascii=False, indent=2)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _load_sync(self) -> MatchScout | None:
        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8") as source:
            document = json.load(source)
        return _scout_from_dict(document)


def sample_scout_template() -> MatchScout:
    hero_names = (
        "Infernus",
        "Seven",
        "Vindicta",
        "Ivy",
        "Dynamo",
        "Pocket",
        "Bebop",
        "McGinnis",
        "Viscous",
        "Lady Geist",
        "Abrams",
        "Paradox",
    )
    player_names = (
        "Ducky",
        "A Surprisingly Long Name",
        "Lane Enjoyer",
        "Goose",
        "Soul Collector",
        "Nocturne",
        "Bongo",
        "Coffee Required",
        "Pocket Sand",
        "Calico Main",
        "Mid Boss Tourist",
        "Last Pick",
    )
    rank_names = (
        "Oracle",
        "Phantom",
        "Archon",
        "Emissary",
        "Ritualist",
        "Ascendant",
    )
    players: list[ScoutedPlayer] = []
    for index, (player_name, hero_name) in enumerate(
        zip(player_names, hero_names, strict=True),
        start=1,
    ):
        account_id = 900_000 + index
        hero_id = index
        games = (3, 11, 27, 48, 76, 115, 164, 230, 0, 39, 92, 310)[index - 1]
        wins = round(games * (0.42 + (index % 5) * 0.04))
        rank_tier = 6 + index % 5
        players.append(
            ScoutedPlayer(
                player=LivePlayer(
                    account_id=account_id,
                    steam_name=player_name,
                    hero_id=hero_id,
                    team=2 if index <= 6 else 3,
                    player_slot=index,
                    kills=0,
                    deaths=0,
                    assists=0,
                    net_worth=0,
                ),
                hero=HeroSummary(hero_id, hero_name, None),
                rank=PlayerRank(rank_tier, 1 + index % 6),
                rank_name=rank_names[index % len(rank_names)],
                experience=(
                    HeroExperience(account_id, hero_id, games, wins, 1_786_000_000)
                    if games
                    else None
                ),
                recent_outcomes=("W", "L", "W", "W", "L")
                if index % 3
                else ("L", "L", "W", "L", "W"),
                total_matches=games * 4 + 20,
                total_wins=round((games * 4 + 20) * (0.42 + (index % 5) * 0.04)),
                top_heroes=tuple(
                    HeroRecord(
                        experience=HeroExperience(
                            account_id,
                            candidate_hero_id,
                            max(games - offset * 8, 1),
                            max(round((games - offset * 8) * (0.44 + offset * 0.03)), 0),
                            1_786_000_000 - offset,
                        ),
                        hero=HeroSummary(
                            candidate_hero_id,
                            hero_names[(index - 1 + offset) % len(hero_names)],
                            None,
                        ),
                    )
                    for offset, candidate_hero_id in enumerate(
                        range(hero_id, hero_id + 5)
                    )
                    if games
                ),
            )
        )
    return MatchScout(match_id=99_999_999, game_time_seconds=75, players=tuple(players))


def _scout_to_dict(scout: MatchScout) -> dict[str, Any]:
    return {
        "version": 3,
        "match_id": scout.match_id,
        "game_time_seconds": scout.game_time_seconds,
        "players": [
            {
                "player": {
                    "account_id": item.player.account_id,
                    "steam_name": item.player.steam_name,
                    "hero_id": item.player.hero_id,
                    "team": item.player.team,
                    "player_slot": item.player.player_slot,
                    "kills": item.player.kills,
                    "deaths": item.player.deaths,
                    "assists": item.player.assists,
                    "net_worth": item.player.net_worth,
                },
                "hero": (
                    {
                        "hero_id": item.hero.hero_id,
                        "name": item.hero.name,
                        "icon_url": item.hero.icon_url,
                    }
                    if item.hero
                    else None
                ),
                "rank": (
                    {"tier": item.rank.tier, "subrank": item.rank.subrank}
                    if item.rank
                    else None
                ),
                "rank_name": item.rank_name,
                "experience": (
                    {
                        "account_id": item.experience.account_id,
                        "hero_id": item.experience.hero_id,
                        "matches_played": item.experience.matches_played,
                        "wins": item.experience.wins,
                        "last_played": item.experience.last_played,
                    }
                    if item.experience
                    else None
                ),
                "recent_outcomes": list(item.recent_outcomes),
                "total_matches": item.total_matches,
                "total_wins": item.total_wins,
                "top_heroes": [
                    {
                        "experience": {
                            "account_id": record.experience.account_id,
                            "hero_id": record.experience.hero_id,
                            "matches_played": record.experience.matches_played,
                            "wins": record.experience.wins,
                            "last_played": record.experience.last_played,
                        },
                        "hero": (
                            {
                                "hero_id": record.hero.hero_id,
                                "name": record.hero.name,
                                "icon_url": record.hero.icon_url,
                            }
                            if record.hero
                            else None
                        ),
                    }
                    for record in item.top_heroes
                ],
            }
            for item in scout.players
        ],
    }


def _scout_from_dict(document: Any) -> MatchScout:
    if not isinstance(document, dict) or document.get("version") != 3:
        raise ValueError("Unsupported Deadlock scouting template")
    players: list[ScoutedPlayer] = []
    for raw in document.get("players", []):
        player = raw["player"]
        hero = raw.get("hero")
        rank = raw.get("rank")
        experience = raw.get("experience")
        players.append(
            ScoutedPlayer(
                player=LivePlayer(**player),
                hero=HeroSummary(**hero) if hero else None,
                rank=PlayerRank(**rank) if rank else None,
                rank_name=raw.get("rank_name"),
                experience=HeroExperience(**experience) if experience else None,
                recent_outcomes=tuple(raw.get("recent_outcomes", ())),
                total_matches=raw.get("total_matches"),
                total_wins=raw.get("total_wins"),
                top_heroes=tuple(
                    HeroRecord(
                        experience=HeroExperience(**record["experience"]),
                        hero=HeroSummary(**record["hero"]) if record.get("hero") else None,
                    )
                    for record in raw.get("top_heroes", ())
                ),
            )
        )
    return MatchScout(
        match_id=int(document["match_id"]),
        game_time_seconds=document.get("game_time_seconds"),
        players=tuple(players),
    )
