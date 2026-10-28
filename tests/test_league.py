"""Tests for Riot-backed League player and all-mode match lookups."""

from __future__ import annotations

import unittest
from typing import Any

from duckies_bot.cogs.league import LeagueCog
from duckies_bot.features.league.formatter import build_match_embed, build_player_embed
from duckies_bot.features.league.models import (
    LeagueMatch,
    MatchParticipant,
    RankedEntry,
    RiotAccount,
    SummonerProfile,
)
from duckies_bot.features.league.service import LeagueService, parse_riot_id
from duckies_bot.providers.riot.client import RiotClient, regional_route
from duckies_bot.providers.riot.errors import RiotAPIError
from duckies_bot.presentation import Palette
from duckies_bot.views.league_player import LeaguePlayerView


class FakeResponse:
    def __init__(
        self,
        document: Any,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.document = document
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> Any:
        return self.document


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


def participant(
    puuid: str,
    *,
    team_id: int = 100,
    champion: str = "Ahri",
    win: bool = True,
) -> MatchParticipant:
    return MatchParticipant(
        puuid=puuid,
        riot_id="Duckie#NA1" if puuid == "target" else f"Player {puuid}#NA1",
        champion_name=champion,
        team_id=team_id,
        team_position="MIDDLE",
        win=win,
        kills=8,
        deaths=2,
        assists=10,
        champion_level=18,
        gold_earned=15_000,
        minions_killed=210,
        neutral_minions_killed=10,
        vision_score=32,
        wards_placed=12,
        wards_killed=5,
        control_wards_bought=3,
        total_damage_to_champions=28_000,
        physical_damage_to_champions=2_000,
        magic_damage_to_champions=25_000,
        true_damage_to_champions=1_000,
        total_damage_taken=16_000,
        total_heal=2_500,
        damage_to_objectives=11_000,
        damage_to_turrets=4_000,
        turret_kills=2,
        inhibitor_kills=1,
        total_time_cc_dealt=30,
        largest_multi_kill=3,
        placement=None,
        subteam_id=None,
        item_ids=(3089, 3020),
    )


def league_match(match_id: str, queue_id: int, *, win: bool = True) -> LeagueMatch:
    players = [participant("target", win=win)]
    players.extend(participant(f"ally-{index}", champion="Garen", win=win) for index in range(4))
    players.extend(
        participant(f"enemy-{index}", team_id=200, champion="Lux", win=not win)
        for index in range(5)
    )
    return LeagueMatch(
        match_id=match_id,
        queue_id=queue_id,
        game_mode="CLASSIC" if queue_id != 450 else "ARAM",
        game_type="MATCHED_GAME",
        game_version="16.21.1.123",
        started_at_ms=1_790_000_000_000,
        duration_seconds=1_800,
        participants=tuple(players),
    )


class RiotClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_correct_routes_and_server_side_key(self) -> None:
        session = FakeSession(
            [FakeResponse({"puuid": "abc", "gameName": "Duckie", "tagLine": "NA1"})]
        )
        client = RiotClient("secret", session=session)  # type: ignore[arg-type]
        account = await client.get_account("Duckie", "NA1", "NA1")
        self.assertEqual(account.riot_id, "Duckie#NA1")
        url, kwargs = session.requests[0]
        self.assertTrue(url.startswith("https://americas.api.riotgames.com/"))
        self.assertEqual(kwargs["headers"]["X-Riot-Token"], "secret")

    async def test_parses_ranked_entries_and_detailed_match(self) -> None:
        ranked_session = FakeSession(
            [
                FakeResponse(
                    [
                        {
                            "queueType": "RANKED_SOLO_5x5",
                            "tier": "DIAMOND",
                            "rank": "II",
                            "leaguePoints": 61,
                            "wins": 42,
                            "losses": 30,
                            "hotStreak": True,
                        }
                    ]
                )
            ]
        )
        client = RiotClient("key", session=ranked_session)  # type: ignore[arg-type]
        entries = await client.get_ranked_entries(
            "target", "NA1"
        )
        self.assertEqual(entries[0].games, 72)
        self.assertAlmostEqual(entries[0].win_rate or 0, 42 / 72)
        self.assertIn("/entries/by-puuid/target", ranked_session.requests[0][0])

        match_document = {
            "metadata": {"matchId": "NA1_123"},
            "info": {
                "queueId": 450,
                "gameMode": "ARAM",
                "gameType": "MATCHED_GAME",
                "gameVersion": "16.21.1",
                "gameStartTimestamp": 1_790_000_000_000,
                "gameDuration": 1234,
                "participants": [
                    {
                        "puuid": "target",
                        "riotIdGameName": "Duckie",
                        "riotIdTagline": "NA1",
                        "championName": "Ahri",
                        "teamId": 100,
                        "teamPosition": "MIDDLE",
                        "win": True,
                        "kills": 8,
                        "deaths": 2,
                        "assists": 10,
                        "totalMinionsKilled": 80,
                        "neutralMinionsKilled": 5,
                        "totalDamageDealtToChampions": 28000,
                        "item0": 3089,
                        "item1": 3020,
                    }
                ],
            },
        }
        session = FakeSession([FakeResponse(match_document)])
        client = RiotClient("key", session=session)  # type: ignore[arg-type]
        match = await client.get_match("NA1_123", "NA1")
        self.assertEqual(match.queue_id, 450)
        self.assertEqual(match.participants[0].creep_score, 85)
        self.assertEqual(match.participants[0].item_ids, (3089, 3020))

    async def test_reports_missing_or_rate_limited_key(self) -> None:
        with self.assertRaisesRegex(RiotAPIError, "RIOT_API_KEY"):
            await RiotClient(None, session=FakeSession([])).get_match_ids(  # type: ignore[arg-type]
                "target", "NA1"
            )
        response = FakeResponse({}, status=429, headers={"Retry-After": "7"})
        with self.assertRaisesRegex(RiotAPIError, "7 seconds"):
            client = RiotClient("key", session=FakeSession([response]))  # type: ignore[arg-type]
            await client.get_match_ids(
                "target", "NA1"
            )


class FakeRiotClient:
    def __init__(self) -> None:
        self.matches = (
            league_match("NA1_ranked", 420),
            league_match("NA1_aram", 450, win=False),
            league_match("NA1_arena", 1700),
        )

    async def get_account(self, game_name: str, tag_line: str, platform: str) -> RiotAccount:
        del platform
        return RiotAccount("target", game_name, tag_line)

    async def get_summoner(self, puuid: str, platform: str) -> SummonerProfile:
        del puuid, platform
        return SummonerProfile(level=321, profile_icon_id=1)

    async def get_ranked_entries(self, puuid: str, platform: str) -> tuple[RankedEntry, ...]:
        del puuid, platform
        return (RankedEntry("RANKED_SOLO_5x5", "EMERALD", "I", 80, 50, 40),)

    async def get_match_ids(self, puuid: str, platform: str, *, count: int) -> tuple[str, ...]:
        del puuid, platform, count
        return tuple(match.match_id for match in self.matches)

    async def get_match(self, match_id: str, platform: str) -> LeagueMatch:
        del platform
        return next(match for match in self.matches if match.match_id == match_id)

    async def get_data_dragon_catalog(self) -> tuple[str, dict[int, str]]:
        return "16.21.1", {3089: "Rabadon's Deathcap", 3020: "Sorcerer's Shoes"}


class LeagueFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_preserves_history_from_all_modes(self) -> None:
        lookup = await LeagueService(FakeRiotClient()).player_lookup(  # type: ignore[arg-type]
            "Duckie#NA1", "NA1"
        )
        self.assertEqual([match.queue_id for match in lookup.matches], [420, 450, 1700])
        overview = build_player_embed(lookup)
        history = "\n".join(
            field.value for field in overview.fields if field.name.startswith("Recent matches")
        )
        self.assertIn("Ranked Solo/Duo", history)
        self.assertIn("ARAM", history)
        self.assertIn("Arena", history)
        self.assertIn("🟩", history)
        self.assertIn("🟥", history)

        detail = build_match_embed(lookup, 1)
        field_names = {field.name for field in detail.fields}
        expected = {
            "KDA & participation",
            "Farm & economy",
            "Vision",
            "Damage profile",
            "Objectives & control",
            "Final build",
        }
        self.assertTrue(expected <= field_names)
        self.assertEqual(detail.color, Palette.RED)
        self.assertTrue(detail.title.startswith("🟥 Defeat"))
        build = next(field.value for field in detail.fields if field.name == "Final build")
        self.assertIn("Rabadon's Deathcap", build)
        self.assertTrue(all(len(field.value) <= 1024 for field in overview.fields))
        self.assertTrue(all(len(field.value) <= 1024 for field in detail.fields))

        view = LeaguePlayerView(lookup, requester_id=123)
        select = next(child for child in view.children if hasattr(child, "options"))
        self.assertEqual(len(select.options), 3)  # type: ignore[attr-defined]

    async def test_match_cache_avoids_refetching_immutable_matches(self) -> None:
        client = FakeRiotClient()
        calls = 0
        original = client.get_match

        async def counted(match_id: str, platform: str) -> LeagueMatch:
            nonlocal calls
            calls += 1
            return await original(match_id, platform)

        client.get_match = counted  # type: ignore[method-assign]
        service = LeagueService(client)  # type: ignore[arg-type]
        await service.player_lookup("Duckie#NA1", "NA1")
        await service.player_lookup("Duckie#NA1", "NA1")
        self.assertEqual(calls, 3)

    def test_riot_id_region_and_command_validation(self) -> None:
        self.assertEqual(parse_riot_id(" Duckie # NA1 "), ("Duckie", "NA1"))
        with self.assertRaises(ValueError):
            parse_riot_id("Duckie")
        self.assertEqual(regional_route("EUW1"), "europe")
        self.assertEqual(regional_route("SG2"), "sea")
        command_names = {command.name for command in LeagueCog.league.commands}
        self.assertEqual(command_names, {"player"})

