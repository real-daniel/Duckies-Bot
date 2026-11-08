"""Tests for Riot-backed League player and all-mode match lookups."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from duckies_bot.cogs.league import LeagueCog
from duckies_bot.features.league.analytics import parse_league_item_question
from duckies_bot.features.league.formatter import (
    build_item_analysis_embed,
    build_match_embed,
    build_player_embed,
)
from duckies_bot.features.league.models import (
    LeagueChampion,
    LeagueChampionAnalysis,
    LeagueItem,
    LeagueItemAnalysis,
    LeagueItemStats,
    LeagueLadderEntry,
    LeagueMatch,
    LeagueMatchTimeline,
    MatchParticipant,
    RankedEntry,
    RiotAccount,
    SummonerProfile,
)
from duckies_bot.features.league.service import LeagueService, parse_riot_id
from duckies_bot.features.item_questions import InterpretedItemQuestion
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
    item_ids: tuple[int, ...] = (3089, 3020),
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
        item_ids=item_ids,
    )


def league_match(
    match_id: str,
    queue_id: int,
    *,
    win: bool = True,
    version: str = "16.21.1.123",
) -> LeagueMatch:
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
        game_version=version,
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

    async def test_parses_ladder_entries_and_ranked_match_filters(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    [{"puuid": "player-1", "tier": "GOLD", "rank": "II"}]
                ),
                FakeResponse(["NA1_1", "NA1_2"]),
            ]
        )
        client = RiotClient("key", session=session)  # type: ignore[arg-type]

        entries = await client.get_league_entries("NA1", "gold", "ii")
        match_ids = await client.get_match_ids(
            "player-1", "NA1", count=8, queue=420
        )

        self.assertEqual(entries, (LeagueLadderEntry("player-1", "GOLD", "II"),))
        self.assertEqual(match_ids, ("NA1_1", "NA1_2"))
        self.assertIn("/RANKED_SOLO_5x5/GOLD/II", session.requests[0][0])
        self.assertEqual(session.requests[0][1]["params"], {"page": "1"})
        self.assertEqual(
            session.requests[1][1]["params"],
            {"start": "0", "count": "8", "queue": "420"},
        )

    async def test_parses_current_summoners_rift_items(self) -> None:
        session = FakeSession(
            [
                FakeResponse(["16.22.1"]),
                FakeResponse(
                    {
                        "data": {
                            "3031": {
                                "name": "Infinity Edge",
                                "gold": {"purchasable": True, "total": 3500},
                                "maps": {"11": True},
                                "image": {"full": "3031.png"},
                                "tags": ["Damage", "CriticalStrike"],
                            },
                            "9999": {
                                "name": "Arena Item",
                                "gold": {"purchasable": True, "total": 3000},
                                "maps": {"11": False},
                            },
                            "323031": {
                                "name": "Infinity Edge",
                                "gold": {"purchasable": True, "total": 3500},
                                "maps": {"11": True},
                            },
                            "1101": {
                                "name": "Scorchclaw Pup",
                                "gold": {"purchasable": True, "total": 450},
                                "maps": {"11": True},
                            },
                            "1107": {
                                "name": "Scorchclaw Pup",
                                "gold": {"purchasable": True, "total": 450},
                                "maps": {"11": True},
                            },
                        }
                    }
                ),
            ]
        )
        client = RiotClient(None, session=session)  # type: ignore[arg-type]

        version, items = await client.get_data_dragon_items()

        self.assertEqual(version, "16.22.1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "Infinity Edge")
        self.assertEqual(items[0].total_cost, 3500)
        self.assertIn("16.22.1/img/item/3031.png", items[0].icon_url)


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
    def setUp(self) -> None:
        self.analysis_items = (
            LeagueItem(
                3031,
                "Infinity Edge",
                "https://example.test/3031.png",
                3500,
                ("Damage", "CriticalStrike"),
                (),
            ),
            LeagueItem(
                3072,
                "Bloodthirster",
                "https://example.test/3072.png",
                3400,
                ("Damage",),
                (),
            ),
            LeagueItem(
                3089,
                "Rabadon's Deathcap",
                "https://example.test/3089.png",
                3600,
                ("SpellDamage",),
                (),
            ),
            LeagueItem(
                2022,
                "Glowing Mote",
                "https://example.test/2022.png",
                250,
                ("CooldownReduction",),
                (3024,),
            ),
            LeagueItem(
                3161,
                "Spear of Shojin",
                "https://example.test/3161.png",
                3100,
                ("Damage", "CooldownReduction"),
                (),
            ),
        )
        self.champions = (
            LeagueChampion(901, "Smolder", "Smolder", "https://example.test/smolder.png"),
            LeagueChampion(103, "Ahri", "Ahri", "https://example.test/ahri.png"),
        )

    def test_parses_discord_style_item_and_rank_questions(self) -> None:
        cases = (
            (
                "how good is Infinity Edge in Gold to Platinum?",
                "Infinity Edge",
                "Gold–Platinum",
            ),
            ("how gud infinety edge gold2", "Infinity Edge", "Gold II"),
            ("IE plat", "Infinity Edge", "Platinum"),
            ("ie in gm", "Infinity Edge", "Grandmaster"),
            ("bloodthirser gm", "Bloodthirster", "Grandmaster"),
            ("rabadons deathcap dia", "Rabadon's Deathcap", "Diamond"),
            ("INFINITY EDGE!!! GOLD-PLAT!!!", "Infinity Edge", "Gold–Platinum"),
        )

        for question, item_name, rank_label in cases:
            with self.subTest(question=question):
                parsed = parse_league_item_question(question, self.analysis_items)
                self.assertEqual(parsed.item.name, item_name)
                self.assertEqual(parsed.rank_label, rank_label)

    def test_league_question_defaults_to_emerald_and_limits_wide_ranges(self) -> None:
        parsed = parse_league_item_question("is infinity edge good", self.analysis_items)
        self.assertEqual(parsed.rank_label, "Emerald (default)")
        self.assertEqual(parsed.buckets[0].tier, "EMERALD")
        with self.assertRaisesRegex(ValueError, "adjacent tiers"):
            parse_league_item_question(
                "infinity edge gold to diamond",
                self.analysis_items,
            )

    def test_rejects_incomplete_or_unresolvable_discord_questions(self) -> None:
        with self.assertRaisesRegex(ValueError, "current League item"):
            parse_league_item_question(
                "whats the build on smolder", self.analysis_items, self.champions
            )
        with self.assertRaisesRegex(ValueError, "Include a champion"):
            parse_league_item_question(
                "ie 2nd item vs shojin", self.analysis_items, self.champions
            )
        with self.assertRaisesRegex(ValueError, "both sides"):
            parse_league_item_question(
                "ie 2nd item smolder vs", self.analysis_items, self.champions
            )

    def test_same_tier_division_range_samples_both_ends(self) -> None:
        parsed = parse_league_item_question(
            "infinity edge gold 4 to gold 1",
            self.analysis_items,
        )

        self.assertEqual(parsed.rank_label, "Gold IV–Gold I")
        self.assertEqual(
            [(bucket.tier, bucket.division) for bucket in parsed.buckets],
            [("GOLD", "IV"), ("GOLD", "I")],
        )

    def test_parses_champion_slot_comparison_with_discord_variations(self) -> None:
        cases = (
            "how good is IE 2nd item on smolder compared to shojin",
            "ie second item smolder vs spear of shojin?",
            "IE 2nd item on smodler compared to shojin!!!",
            "ie item 2 smolder versus shojin",
        )
        for question in cases:
            with self.subTest(question=question):
                parsed = parse_league_item_question(
                    question, self.analysis_items, self.champions
                )
                self.assertEqual(parsed.item.name, "Infinity Edge")
                self.assertEqual(parsed.comparison_item.name, "Spear of Shojin")
                self.assertEqual(parsed.champion.name, "Smolder")
                self.assertEqual(parsed.item_position, 2)
                self.assertEqual(parsed.rank_label, "Emerald (default)")

    def test_parses_realistic_discord_champion_comparison_errors(self) -> None:
        cases = (
            "ie 2nd smolder v shojin",
            "is IE 2nd on smolder better than shojin",
            "how gud ie secnd item smodler compard to shojin",
            "IE 2 item Smolder VS Shojin!!!",
            "ie item2 on smolder compared 2 shojin",
            "infinityedge second item smolder against spearofshojin",
            "ie 2nd core item on smodler versus shojn",
            "yo ie second smolder instead of shojin?",
            "IE SECOND ITEM SMOLDER V SHOJIN NO PUNCTUATION",
        )
        for question in cases:
            with self.subTest(question=question):
                parsed = parse_league_item_question(
                    question, self.analysis_items, self.champions
                )
                self.assertEqual(parsed.item.name, "Infinity Edge")
                self.assertEqual(parsed.comparison_item.name, "Spear of Shojin")
                self.assertEqual(parsed.champion.name, "Smolder")
                self.assertEqual(parsed.item_position, 2)
                self.assertEqual(parsed.rank_label, "Emerald (default)")

    async def test_champion_item_slot_service_uses_timeline_purchase_order(self) -> None:
        match = league_match("NA1_slot", 420, version="16.22.1.999")
        players = tuple(
            replace(
                player,
                champion_name="Smolder" if index < 2 else player.champion_name,
                participant_id=index + 1,
                win=index == 0,
            )
            for index, player in enumerate(match.participants)
        )
        match = replace(match, participants=players)

        class ChampionAnalysisClient:
            async def get_data_dragon_items(self):
                return "16.22.1", self_items

            async def get_data_dragon_champions(self, version):
                return version, self_champions

            async def get_league_entries(self, platform, tier, division):
                return (LeagueLadderEntry("seed", tier, division),)

            async def get_match_ids(self, puuid, platform, *, count, queue):
                return (match.match_id,)

            async def get_match(self, match_id, platform):
                return match

            async def get_match_timeline(self, match_id, platform):
                return LeagueMatchTimeline(
                    match_id,
                    {1: (3072, 3031), 2: (3072, 3161)},
                )

        self_items, self_champions = self.analysis_items, self.champions
        interpreter = SimpleNamespace(
            interpret=AsyncMock(
                return_value=InterpretedItemQuestion(
                    game="league",
                    intent="compare",
                    subject_type="item",
                    primary_subject="Infinity Edge",
                    comparison_subject="Spear of Shojin",
                    character_context="Smolder",
                    item_position=2,
                    minimum_rank=None,
                    maximum_rank=None,
                    rank_scope="all",
                    metrics=("win_rate",),
                    match_mode="ranked",
                    needs_clarification=False,
                    clarification=None,
                    confidence="high",
                )
            )
        )
        result = await LeagueService(
            ChampionAnalysisClient(), question_interpreter=interpreter
        ).answer_item_question(
            "is IE 2nd on smolder better than shojin", "NA1"
        )

        self.assertEqual(result.stats, LeagueItemStats(3031, 1, 0))
        self.assertEqual(result.comparison_stats, LeagueItemStats(3161, 0, 1))
        self.assertEqual(result.champion.name, "Smolder")
        self.assertEqual(result.item_position, 2)
        interpreter.interpret.assert_awaited_once_with(
            "is IE 2nd on smolder better than shojin", game="league"
        )

    async def test_flexible_champion_question_routes_without_an_item(self) -> None:
        base = league_match("NA1_champion", 420, version="16.22.1.999")
        players = tuple(
            replace(
                player,
                champion_name="Smolder" if index < 2 else "Ahri",
                win=index in {0, 2, 3, 4, 5},
            )
            for index, player in enumerate(base.participants)
        )
        match = replace(base, participants=players)

        class ChampionClient:
            async def get_data_dragon_items(self):
                return "16.22.1", self_items

            async def get_data_dragon_champions(self, version):
                return version, self_champions

            async def get_league_entries(self, platform, tier, division):
                return (LeagueLadderEntry("seed", tier, division),)

            async def get_match_ids(self, puuid, platform, *, count, queue):
                return (match.match_id,)

            async def get_match(self, match_id, platform):
                return match

        self_items, self_champions = self.analysis_items, self.champions
        interpretation = InterpretedItemQuestion(
            game="league",
            intent="analyze",
            subject_type="character",
            primary_subject="Smolder",
            comparison_subject=None,
            character_context=None,
            item_position=None,
            minimum_rank="Emerald",
            maximum_rank=None,
            rank_scope="exact",
            metrics=("win_rate", "pick_rate", "sample_size"),
            match_mode="ranked",
            needs_clarification=False,
            clarification=None,
            confidence="high",
        )
        interpreter = SimpleNamespace(interpret=AsyncMock(return_value=interpretation))
        service = LeagueService(
            ChampionClient(), question_interpreter=interpreter  # type: ignore[arg-type]
        )

        for question in (
            "is smolder good emerald",
            "smodler gud in emrald",
            "SMOLDER EMERALD ANY GOOD",
            "yo hows smolder doing emerald no punctuation",
        ):
            with self.subTest(question=question):
                result = await service.answer_question(question, "NA1")
                self.assertIsInstance(result, LeagueChampionAnalysis)
                self.assertEqual(result.champion.name, "Smolder")
                self.assertEqual(result.stats.matches, 2)

    async def test_riot_client_parses_timeline_item_purchases_in_order(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    {
                        "metadata": {"matchId": "NA1_123"},
                        "info": {
                            "frames": [
                                {"events": [
                                    {"type": "ITEM_PURCHASED", "participantId": 4, "itemId": 1055},
                                    {"type": "CHAMPION_KILL", "participantId": 4},
                                ]},
                                {"events": [
                                    {"type": "ITEM_PURCHASED", "participantId": 4, "itemId": 3031},
                                    {"type": "ITEM_PURCHASED", "participantId": 7, "itemId": 3161},
                                ]},
                            ]
                        },
                    }
                )
            ]
        )
        client = RiotClient("test-key", session=session)  # type: ignore[arg-type]

        timeline = await client.get_match_timeline("NA1_123", "NA1")

        self.assertEqual(timeline.purchases[4], (1055, 3031))
        self.assertEqual(timeline.purchases[7], (3161,))
        self.assertIn("/timeline", session.requests[0][0])

    async def test_item_analysis_aggregates_current_patch_sample_and_caches(self) -> None:
        matches: dict[str, LeagueMatch] = {}
        for match_number in range(4):
            base = league_match(
                f"NA1_sample_{match_number}",
                420,
                version="16.22.1.999",
            )
            players = tuple(
                replace(
                    player,
                    item_ids=(3031,) if index in {0, 1, 2, 5, 6} else (3072,),
                )
                for index, player in enumerate(base.participants)
            )
            matches[base.match_id] = replace(base, participants=players)

        class AnalysisClient:
            def __init__(self) -> None:
                self.ladder_calls = 0
                self.match_calls = 0

            async def get_data_dragon_items(self):
                return "16.22.1", self_items

            async def get_league_entries(self, platform, tier, division):
                del platform, tier, division
                self.ladder_calls += 1
                return tuple(
                    LeagueLadderEntry(f"seed-{index}", "GOLD", "II")
                    for index in range(3)
                )

            async def get_match_ids(self, puuid, platform, *, count, queue):
                del puuid, platform, count, queue
                return tuple(matches)

            async def get_match(self, match_id, platform):
                del platform
                self.match_calls += 1
                return matches[match_id]

        self_items = self.analysis_items
        client = AnalysisClient()
        service = LeagueService(client)  # type: ignore[arg-type]

        result = await service.answer_item_question("is IE good in gold", "NA1")
        again = await service.answer_item_question("infinity edge gold", "NA1")

        self.assertEqual(result.stats, LeagueItemStats(3031, 12, 8))
        self.assertEqual(result.games_scanned, 4)
        self.assertEqual(result.players_scanned, 40)
        self.assertEqual(result.peer_win_rate, 0.4)
        self.assertEqual(result.verdict, "Promising, but low confidence")
        self.assertEqual(again, result)
        self.assertEqual(client.ladder_calls, 1)
        self.assertEqual(client.match_calls, 4)

    def test_item_analysis_embed_labels_bounded_sample(self) -> None:
        analysis = LeagueItemAnalysis(
            item=self.analysis_items[0],
            stats=LeagueItemStats(3031, 12, 8),
            data_dragon_version="16.22.1",
            rank_label="Gold",
            platform="NA1",
            games_scanned=4,
            players_scanned=40,
            peer_win_rate=0.4,
            peer_count=1,
            confidence_low=0.39,
            confidence_high=0.78,
            verdict="Promising, but low confidence",
        )

        embed = build_item_analysis_embed(analysis)

        self.assertIn("sample", (embed.description or "").casefold())
        self.assertIn("20 item observations", embed.fields[0].value)
        self.assertIn("20.0% above", embed.fields[1].value)
        self.assertIn("not a census", embed.fields[2].value)

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
        self.assertEqual(command_names, {"ask", "player"})

