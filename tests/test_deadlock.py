"""Tests for Deadlock active-match lookup."""

from typing import Any
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from duckies_bot.features.deadlock import DeadlockService
from duckies_bot.features.deadlock.formatter import (
    build_live_match_embed,
    build_scout_overview_embeds,
    build_scout_player_embed,
    build_watch_tab_embed,
)
from duckies_bot.features.deadlock.models import (
    ActiveMatch,
    ActivePlayer,
    HeroExperience,
    HeroRecord,
    HeroSummary,
    ItemSummary,
    LiveChatMessage,
    LiveKillEvent,
    LiveMatchSnapshot,
    LivePlayer,
    MatchScout,
    PlayerHistory,
    PlayerRank,
    RankAsset,
    ScoutedPlayer,
)
from duckies_bot.providers.deadlock import DeadlockAPIError
from duckies_bot.providers.deadlock.client import DeadlockClient
from duckies_bot.providers.deadlock.live_client import DeadlockLiveClient
from duckies_bot.views import DeadlockScoutView, DeadlockWatchView
from duckies_bot.cogs.deadlock import (
    DeadlockCog,
    _bot_authenticated_message,
    _format_chat_message,
    _newer_live_snapshot,
)


class FakeResponse:
    def __init__(self, body: Any, status: int = 200) -> None:
        self.body = body
        self.status = status

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> Any:
        return self.body

    async def read(self) -> bytes:
        return self.body if isinstance(self.body, bytes) else b""


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, str] | None]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((url, kwargs.get("params")))
        return self.responses.pop(0)


class FakeContent:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __aiter__(self):
        async def iterate():
            for line in self.lines:
                yield line

        return iterate()


class FakeSSEResponse:
    def __init__(self, body: str, status: int = 200) -> None:
        self.status = status
        self.content = FakeContent(
            [line.encode("utf-8") for line in body.splitlines(keepends=True)]
        )

    async def __aenter__(self) -> "FakeSSEResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def text(self) -> str:
        return ""


class FakeSSESession:
    def __init__(self, response: FakeSSEResponse) -> None:
        self.response = response
        self.closed = False
        self.params: dict[str, str] | None = None
        self.url: str | None = None

    def get(self, url: str, **kwargs: Any) -> FakeSSEResponse:
        self.url = url
        self.params = kwargs.get("params")
        return self.response


def active_match_document() -> list[dict[str, Any]]:
    return [
        {
            "match_id": 987654,
            "duration_s": 754,
            "game_mode_parsed": "KECitadelGameModeNormal",
            "match_mode_parsed": "Unranked",
            "region_mode_parsed": "NA",
            "lobby_id": 42,
            "match_score": 3,
            "net_worth_team_0": 45_000,
            "net_worth_team_1": 43_500,
            "spectators": 5,
            "open_spectator_slots": 10,
            "players": [
                {
                    "account_id": 123456,
                    "hero_id": 1,
                    "team": 0,
                    "team_parsed": "Team0",
                    "abandoned": False,
                }
            ],
        }
    ]


class DeadlockClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_downloads_catalog_image_assets(self) -> None:
        session = FakeSession([FakeResponse(b"image-bytes")])
        client = DeadlockClient(session=session)  # type: ignore[arg-type]

        payload = await client.get_asset_bytes("https://assets.example.test/item.webp")

        self.assertEqual(payload, b"image-bytes")
        self.assertEqual(session.requests[0][0], "https://assets.example.test/item.webp")

    async def test_filters_active_matches_by_steam_account_id(self) -> None:
        session = FakeSession([FakeResponse(active_match_document())])
        client = DeadlockClient(base_url="https://example.test", session=session)  # type: ignore[arg-type]

        match = await client.get_active_match(123456)

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.match_id, 987654)
        self.assertEqual(match.player(123456).hero_id, 1)  # type: ignore[union-attr]
        self.assertEqual(session.requests[0][1], {"account_ids": "123456"})

    async def test_empty_active_match_result_is_not_in_game(self) -> None:
        client = DeadlockClient(session=FakeSession([FakeResponse([])]))  # type: ignore[arg-type]
        self.assertIsNone(await client.get_active_match(123456))

    async def test_parses_live_broadcast_url(self) -> None:
        session = FakeSession(
            [FakeResponse({"broadcast_url": "https://relay.example.test/match/123/"})]
        )
        client = DeadlockClient(base_url="https://example.test", session=session)  # type: ignore[arg-type]

        url = await client.get_live_broadcast_url(123)

        self.assertEqual(url, "https://relay.example.test/match/123")
        self.assertEqual(
            session.requests[0][0],
            "https://example.test/v1/matches/123/live/url",
        )

    async def test_parses_hero_asset(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    {
                        "id": 1,
                        "name": "Infernus",
                        "images": {"icon_image_small_webp": "https://example.test/hero.webp"},
                    }
                ),
                FakeResponse(
                    [
                        {"id": 1, "name": "Infernus", "images": {}},
                        {"id": 2, "name": "Seven", "images": {}},
                    ]
                ),
                FakeResponse(
                    [
                        {
                            "id": 1548066885,
                            "name": "Extended Magazine",
                            "type": "upgrade",
                            "shopable": True,
                            "item_slot_type": "weapon",
                            "item_tier": 1,
                            "cost": 800,
                            "shop_image_webp": "https://example.test/item.webp",
                        },
                        {"id": 2, "name": "Internal Ability", "shopable": False},
                    ]
                ),
            ]
        )
        client = DeadlockClient(session=session)  # type: ignore[arg-type]
        hero = await client.get_hero(1)
        heroes = await client.get_heroes()
        items = await client.get_items()
        self.assertEqual(hero.name, "Infernus")
        self.assertEqual([item.name for item in heroes], ["Infernus", "Seven"])
        self.assertEqual(items[0].item_id, 1548066885)
        self.assertEqual(items[0].slot_type, "weapon")
        self.assertEqual(items[0].icon_url, "https://example.test/item.webp")
        self.assertTrue(items[0].shopable)

    async def test_parses_rank_history_experience_and_rank_assets(self) -> None:
        session = FakeSession(
            [
                FakeResponse({"badge": 85, "rank": 8, "subrank": 5}),
                FakeResponse(
                    [
                        {"start_time": 20, "player_match_outcome": 2},
                        {"start_time": 30, "player_match_outcome": 1},
                        {"start_time": 10, "player_match_outcome": 5},
                    ]
                ),
                FakeResponse(
                    [
                        {
                            "account_id": 123,
                            "hero_id": 69,
                            "matches_played": 50,
                            "wins": 30,
                            "last_played": 999,
                        }
                    ]
                ),
                FakeResponse([{"tier": 8, "name": "Oracle"}]),
            ]
        )
        client = DeadlockClient(session=session)  # type: ignore[arg-type]

        rank = await client.get_player_rank(123)
        history = await client.get_player_history(123)
        experience = await client.get_hero_experience([123, 456], [69])
        assets = await client.get_rank_assets()

        self.assertEqual(rank, PlayerRank(8, 5))
        self.assertEqual(history.outcomes, ("W", "L"))
        self.assertEqual(experience[0].win_rate, 0.6)
        self.assertEqual(assets, (RankAsset(8, "Oracle"),))
        self.assertEqual(
            session.requests[2][1],
            [("account_ids", "123"), ("account_ids", "456"), ("hero_ids", "69")],
        )


class DeadlockLiveClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_hero_killed_entity_indexes_to_exact_players(self) -> None:
        events = []
        for slot in range(1, 13):
            events.append(
                'event: player_controller_entity_create\n'
                f'data: {{"tick":100,"game_time":60,"entity_index":{slot},'
                f'"steam_id":{3000 + slot},"steam_name":"Player {slot}",'
                f'"player_slot":{slot},"pawn":{100 + slot},'
                f'"team":{2 if slot <= 6 else 3}}}\n\n'
            )
        events.append(
            'event: hero_killed\n'
            'data: {"tick":120,"game_time":75.5,"event_type":"hero_killed",'
            '"entindex_victim":107,"entindex_attacker":101,'
            '"entindex_scorer":101,"entindex_assisters":[102]}\n\n'
            'event: end\ndata: {}\n\n'
        )
        client = DeadlockLiveClient(
            session=FakeSSESession(FakeSSEResponse("".join(events)))
        )  # type: ignore[arg-type]

        snapshots = [
            snapshot
            async for snapshot in client.stream_match_snapshots(
                789,
                "https://relay.example.test/match/789",
            )
        ]

        kill = snapshots[-1].kill_events[0]
        self.assertEqual(kill.tick, 120)
        self.assertEqual(kill.game_time_seconds, 75.5)
        self.assertEqual(kill.attacker_account_id, 3001)
        self.assertEqual(kill.victim_account_id, 3007)
        self.assertEqual(kill.assister_account_ids, (3002,))

    async def test_parses_detailed_controller_statistics(self) -> None:
        body = (
            'event: player_controller_entity_create\n'
            'data: {"steam_id":1001,"steam_name":"Ducky","player_slot":1,"team":2,'
            '"hero_id":1,"kills":3,"deaths":1,"assists":5,"net_worth":12345,'
            '"assigned_lane":2,"denies":4,"last_hits":50,"hero_healing":600,'
            '"self_healing":700,"hero_damage":8000,"objective_damage":900,'
            '"health_regen":3.5,"ultimate_trained":true,'
            '"ultimate_cooldown_end":42.5,"upgrades":[101,202],"game_time":120}\n\n'
            'event: tick_end\ndata: {"tick":1}\n\n'
        )
        client = DeadlockLiveClient(
            session=FakeSSESession(FakeSSEResponse(body))
        )  # type: ignore[arg-type]

        snapshot = await client.get_match_players(
            123,
            "https://relay.example.test/match/123",
        )

        player = snapshot.players[0]
        self.assertEqual(player.assigned_lane, 2)
        self.assertEqual(player.hero_damage, 8_000)
        self.assertEqual(player.objective_damage, 900)
        self.assertEqual(player.upgrades, (101, 202))
        self.assertEqual(player.health_regen, 3.5)
        self.assertTrue(player.ultimate_trained)

    async def test_collects_twelve_players_and_excludes_sourcetv(self) -> None:
        events = [
            'event: player_controller_entity_create\n'
            'data: {"steam_id":0,"steam_name":"SourceTV","player_slot":1}\n\n'
        ]
        for slot in range(1, 13):
            events.append(
                'event: player_controller_entity_create\n'
                f'data: {{"steam_id":{1000 + slot},"steam_name":"Player {slot}",'
                f'"player_slot":{slot},"team":{2 if slot <= 6 else 3},'
                f'"hero_id":{slot},"kills":1,"deaths":2,"assists":3,'
                '"net_worth":12345,"game_time":456.5}\n\n'
            )
        events.append(
            'event: player_controller_entity_update\n'
            'data: {"steam_id":1001,"steam_name":"Player 1","player_slot":1,'
            '"team":2,"hero_id":1,"game_time":456.5}\n\n'
        )
        session = FakeSSESession(FakeSSEResponse("".join(events)))
        client = DeadlockLiveClient(session=session)  # type: ignore[arg-type]

        snapshot = await client.get_match_players(
            98832895,
            "https://relay.example.test/match/98832895",
        )

        self.assertEqual(snapshot.match_id, 98832895)
        self.assertEqual(snapshot.game_time_seconds, 456.5)
        self.assertEqual(len(snapshot.players), 12)
        self.assertNotIn(0, {player.account_id for player in snapshot.players})
        self.assertEqual(session.url, "http://127.0.0.1:3000/v1/live/demo/events")
        self.assertEqual(
            session.params,
            {
                "broadcast_url": "https://relay.example.test/match/98832895",
                "subscribed_entities": "player_controller",
            },
        )

    async def test_first_update_completes_a_smaller_mode_roster(self) -> None:
        events = []
        for slot in range(1, 9):
            events.append(
                'event: player_controller_entity_create\n'
                f'data: {{"steam_id":{2000 + slot},"steam_name":"Player {slot}",'
                f'"player_slot":{slot},"team":{2 if slot <= 4 else 3}}}\n\n'
            )
        events.append('event: tick_end\ndata: {"tick":1}\n\n')
        session = FakeSSESession(FakeSSEResponse("".join(events)))

        snapshot = await DeadlockLiveClient(session=session).get_match_players(  # type: ignore[arg-type]
            123,
            "https://relay.example.test/match/123",
        )

        self.assertEqual(len(snapshot.players), 8)

    async def test_streams_updated_match_snapshots_without_losing_partial_fields(self) -> None:
        events = []
        for slot in range(1, 13):
            events.append(
                'event: player_controller_entity_create\n'
                f'data: {{"steam_id":{3000 + slot},"steam_name":"Player {slot}",'
                f'"player_slot":{slot},"team":{2 if slot <= 6 else 3},'
                f'"hero_id":{slot},"kills":1,"deaths":2,"assists":3,'
                '"net_worth":10000,"game_time":100}\n\n'
            )
        events.append(
            'event: player_controller_entity_update\n'
            'data: {"steam_id":3001,"kills":2,"net_worth":11000,"game_time":120}\n\n'
            'event: end\ndata: {}\n\n'
        )
        client = DeadlockLiveClient(
            session=FakeSSESession(FakeSSEResponse("".join(events)))
        )  # type: ignore[arg-type]

        snapshots = [
            snapshot
            async for snapshot in client.stream_match_snapshots(
                789,
                "https://relay.example.test/match/789",
            )
        ]

        self.assertEqual(len(snapshots), 2)
        updated = snapshots[-1].players[0]
        self.assertEqual(updated.kills, 2)
        self.assertEqual(updated.deaths, 2)
        self.assertEqual(updated.assists, 3)
        self.assertEqual(updated.hero_id, 1)
        self.assertEqual(updated.net_worth, 11_000)
        self.assertEqual(snapshots[-1].game_time_seconds, 120)

    async def test_inventory_is_replaced_only_when_upgrades_is_included(self) -> None:
        body = (
            'event: player_controller_entity_create\n'
            'data: {"steam_id":1001,"steam_name":"Ducky","player_slot":1,'
            '"team":2,"upgrades":[101,202,303],"game_time":100}\n\n'
            'event: tick_end\ndata: {"tick":1}\n\n'
            'event: player_controller_entity_update\n'
            'data: {"steam_id":1001,"kills":1,"game_time":101}\n\n'
            'event: player_controller_entity_update\n'
            'data: {"steam_id":1001,"upgrades":[101,303],"game_time":102}\n\n'
            'event: player_controller_entity_update\n'
            'data: {"steam_id":1001,"upgrades":[],"game_time":103}\n\n'
            'event: end\ndata: {}\n\n'
        )
        client = DeadlockLiveClient(
            session=FakeSSESession(FakeSSEResponse(body))
        )  # type: ignore[arg-type]

        snapshots = [
            snapshot
            async for snapshot in client.stream_match_snapshots(
                123,
                "https://relay.example.test/match/123",
            )
        ]

        inventories = [snapshot.players[0].upgrades for snapshot in snapshots]
        self.assertEqual(
            inventories,
            [
                (101, 202, 303),
                (101, 202, 303),
                (101, 303),
                (),
            ],
        )

    async def test_streams_chat_messages_until_end_event(self) -> None:
        body = (
            'event: tick_end\ndata: {"tick":1}\n\n'
            'event: chat_message\n'
            'data: {"steam_id":123,"steam_name":"Ducky","text":"gl hf",'
            '"game_time":65.2,"all_chat":true}\n\n'
            'event: end\ndata: {}\n\n'
        )
        session = FakeSSESession(FakeSSEResponse(body))
        client = DeadlockLiveClient(session=session)  # type: ignore[arg-type]

        messages = [
            message
            async for message in client.stream_chat_messages(456)
        ]

        self.assertEqual(
            messages,
            [LiveChatMessage(123, "Ducky", "gl hf", 65.2, True)],
        )
        self.assertEqual(
            session.params,
            {
                "subscribed_entities": "player_controller",
                "subscribed_chat_messages": "true",
            },
        )
        self.assertEqual(
            session.url,
            "http://127.0.0.1:3000/v1/matches/456/live/demo/events",
        )

    def test_chat_format_escapes_mentions_and_markdown(self) -> None:
        rendered = _format_chat_message(
            LiveChatMessage(
                123,
                "**Ducky**",
                "@everyone visit **mid** now",
                65.2,
                True,
            )
        )

        self.assertIn("`[1:05]`", rendered)
        self.assertIn("All", rendered)
        self.assertNotIn("@everyone", rendered)
        self.assertNotIn("****Ducky****", rendered)
        self.assertLessEqual(len(rendered), 2_000)

        team_rendered = _format_chat_message(
            LiveChatMessage(1, "A", "rotate", 1, False)
        )
        self.assertIn("· Team", team_rendered)

    def test_chat_commands_are_registered(self) -> None:
        command_names = {command.name for command in DeadlockCog.deadlock.commands}
        self.assertIn("chat", command_names)
        self.assertIn("chat-stop", command_names)
        self.assertIn("watch", command_names)
        self.assertIn("companion-pair", command_names)
        self.assertIn("companion-disable", command_names)
        self.assertNotIn("watchtest", command_names)

    def test_match_commands_accept_screenshots(self) -> None:
        commands = {command.name: command for command in DeadlockCog.deadlock.commands}
        self.assertNotIn("live", commands)
        for command_name in ("scout", "chat", "watch", "chat-stop", "watch-stop"):
            parameter_names = {parameter.name for parameter in commands[command_name].parameters}
            self.assertIn("match_id", parameter_names)
            self.assertIn("screenshot", parameter_names)


class DeadlockFormatterTests(unittest.TestCase):
    def test_live_match_embed_lists_players_by_team(self) -> None:
        snapshot = LiveMatchSnapshot(
            match_id=98832895,
            game_time_seconds=456.5,
            players=(
                LivePlayer(1001, "Ducky", 1, 2, 1, 4, 2, 8, 12_345),
                LivePlayer(1002, "Goose", 2, 3, 7, 1, 3, 2, 9_876),
            ),
            heroes=(HeroSummary(1, "Infernus", None), HeroSummary(2, "Seven", None)),
        )

        embed = build_live_match_embed(snapshot, highlighted_account_id=1001)

        self.assertEqual(embed.title, "Deadlock match 98832895")
        self.assertEqual(len(embed.fields), 2)
        self.assertIn("**Ducky** — Infernus — 4/2/8", embed.fields[0].value)
        self.assertIn("Goose — Seven — 1/3/2", embed.fields[1].value)

    def test_live_match_embed_displays_connection_and_end_states(self) -> None:
        snapshot = LiveMatchSnapshot(
            98832895,
            840,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 4, 2, 8, 12_345),),
        )

        reconnecting = build_live_match_embed(snapshot, stream_status="reconnecting")
        unavailable = build_live_match_embed(snapshot, stream_status="unreachable")
        ended = build_live_match_embed(snapshot, stream_status="ended")

        self.assertIn("Reconnecting", reconnecting.title)
        self.assertIn("14:00", reconnecting.description)
        self.assertIn("unreachable", unavailable.description)
        self.assertIn("Ended", ended.title)
        self.assertIn("match has ended", ended.description)

    def test_watch_tabs_render_detailed_match_views(self) -> None:
        player = LivePlayer(
            1001,
            "Ducky",
            1,
            2,
            1,
            4,
            2,
            8,
            12_345,
            assigned_lane=3,
            denies=4,
            last_hits=80,
            hero_healing=1_500,
            self_healing=900,
            hero_damage=20_000,
            objective_damage=4_500,
            upgrades=(101, 202),
        )
        snapshot = LiveMatchSnapshot(
            98832895,
            600,
            (player,),
            (HeroSummary(1, "Infernus", None),),
        )

        combat = build_watch_tab_embed(snapshot, "combat")
        economy = build_watch_tab_embed(snapshot, "economy")
        builds = build_watch_tab_embed(snapshot, "builds")
        timeline = build_watch_tab_embed(snapshot, "timeline", timeline=("A kill",))
        detail = build_watch_tab_embed(snapshot, "player", selected_account_id=1001)

        self.assertIn("Combat", combat.title)
        self.assertIn("20.0k", combat.fields[0].value)
        self.assertIn("80", economy.fields[0].value)
        self.assertIn("101", builds.fields[0].value)
        self.assertIn("A kill", timeline.description)
        self.assertTrue(any(field.name == "Damage" for field in detail.fields))

    def test_reconnect_snapshot_filter_rejects_replayed_old_state(self) -> None:
        player = LivePlayer(1001, "Ducky", 1, 2, 1, 1, 0, 0, 10_000)
        current = LiveMatchSnapshot(123, 840, (player,))
        replayed = LiveMatchSnapshot(123, 300, (player,))
        advanced = LiveMatchSnapshot(
            123,
            850,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 2, 0, 0, 11_000),),
        )
        kill_at_current_time = LiveMatchSnapshot(
            123,
            840,
            (player,),
            kill_events=(LiveKillEvent(500, 840, 1001, 1002),),
        )

        self.assertFalse(_newer_live_snapshot(replayed, current))
        self.assertTrue(_newer_live_snapshot(advanced, current))
        self.assertTrue(_newer_live_snapshot(kill_at_current_time, current))

    def test_scout_embed_explains_rank_history_and_form(self) -> None:
        live_player = LivePlayer(1001, "Ducky", 1, 2, 1, 0, 0, 0, 0)
        scout = MatchScout(
            match_id=98832895,
            game_time_seconds=65,
            players=(
                ScoutedPlayer(
                    player=live_player,
                    hero=HeroSummary(1, "Infernus", None),
                    rank=PlayerRank(8, 5),
                    rank_name="Oracle",
                    experience=HeroExperience(1001, 1, 50, 30, 999),
                    recent_outcomes=("W", "W", "L", "W", "L"),
                    total_matches=200,
                    top_heroes=(
                        HeroRecord(
                            HeroExperience(1001, 2, 80, 48, 998),
                            HeroSummary(2, "Seven", None),
                        ),
                        HeroRecord(
                            HeroExperience(1001, 1, 50, 30, 999),
                            HeroSummary(1, "Infernus", None),
                        ),
                    ),
                ),
            ),
        )

        embeds = build_scout_overview_embeds(scout, highlighted_account_id=1001)

        self.assertEqual(len(embeds), 2)
        self.assertEqual(embeds[1].title, "Amber")
        self.assertEqual(embeds[1].fields[0].name, "Ducky · You")
        value = embeds[1].fields[0].value
        self.assertIn("**Infernus**", value)
        self.assertIn("Oracle V · 200 total games", value)
        self.assertIn("50 (25%) hero games · 60% WR", value)
        self.assertIn("`W W L W L`", value)

        detail = build_scout_player_embed(scout, 0, highlighted_account_id=1001)
        self.assertIn("Player 1 of 1", detail.description)
        self.assertIn("Your linked account", detail.description)
        self.assertTrue(any(field.name == "Rank" and field.value == "Oracle V" for field in detail.fields))
        self.assertTrue(
            any(
                field.name == "Hero history"
                and "50 (25%) hero games" in field.value
                for field in detail.fields
            )
        )
        top_heroes = next(field.value for field in detail.fields if field.name == "Top heroes")
        self.assertIn("**Seven** — 80 games · 60% WR · 40% played", top_heroes)
        self.assertIn("**Infernus** — 50 games · 60% WR · 25% played", top_heroes)
        self.assertTrue(any("steamcommunity.com/profiles/" in field.value for field in detail.fields))

    def test_full_scout_overview_is_two_six_card_team_grids(self) -> None:
        players = tuple(
            ScoutedPlayer(
                player=LivePlayer(
                    2000 + index,
                    f"Player {index}",
                    index,
                    2 if index <= 6 else 3,
                    index,
                    0,
                    0,
                    0,
                    0,
                ),
                hero=HeroSummary(index, f"Hero {index}", None),
                rank=PlayerRank(8, 3),
                rank_name="Oracle",
                experience=HeroExperience(2000 + index, index, 100, 55, 999),
                recent_outcomes=("W", "L", "W", "W", "L"),
                total_matches=400,
            )
            for index in range(1, 13)
        )

        embeds = build_scout_overview_embeds(MatchScout(123, 65, players))

        self.assertEqual(len(embeds), 3)
        self.assertEqual([len(embed.fields) for embed in embeds], [0, 6, 6])
        self.assertTrue(all(field.inline for embed in embeds[1:] for field in embed.fields))
        self.assertTrue(all(len(field.value) < 1024 for embed in embeds for field in embed.fields))


class FakeScoutAPI:
    def __init__(self) -> None:
        self.rank_calls = 0
        self.history_calls = 0
        self.experience_calls = 0
        self.asset_calls = 0
        self.broadcast_url_calls = 0
        self.hero_asset_calls = 0
        self.item_asset_calls = 0
        self.image_download_calls = 0
        self.active_match_calls = 0

    async def get_active_matches(self) -> tuple[ActiveMatch, ...]:
        self.active_match_calls += 1
        standard_players = tuple(
            ActivePlayer(index + 1, None, None, None, False) for index in range(12)
        )
        street_brawl_players = tuple(
            ActivePlayer(index + 101, None, None, None, False) for index in range(8)
        )
        return (
            ActiveMatch(
                111,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                standard_players,
            ),
            ActiveMatch(
                222,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                street_brawl_players,
            ),
        )

    async def get_live_broadcast_url(self, match_id: int) -> str:
        self.broadcast_url_calls += 1
        return f"https://relay.example.test/match/{match_id}"

    async def get_heroes(self) -> tuple[HeroSummary, ...]:
        self.hero_asset_calls += 1
        return (HeroSummary(1, "Infernus", "https://example.test/hero.webp"),)

    async def get_items(self) -> tuple[ItemSummary, ...]:
        self.item_asset_calls += 1
        return (
            ItemSummary(
                1548066885,
                "Extended Magazine",
                "https://example.test/item.webp",
                "weapon",
                1,
                800,
                True,
            ),
            ItemSummary(2, "Internal Ability", None, None, None, None, False),
        )

    async def get_asset_bytes(self, url: str) -> bytes:
        self.image_download_calls += 1
        return f"image:{url}".encode()

    async def get_player_rank(self, account_id: int) -> PlayerRank:
        self.rank_calls += 1
        return PlayerRank(8, 5)

    async def get_player_history(self, account_id: int) -> PlayerHistory:
        self.history_calls += 1
        return PlayerHistory(account_id, ("W", "L"))

    async def get_hero_experience(
        self,
        account_ids: list[int],
        hero_ids: list[int] | None = None,
    ):
        self.experience_calls += 1
        return tuple(
            experience
            for account_id in account_ids
            for experience in (
                HeroExperience(account_id, 1, 20, 11, 999),
                HeroExperience(account_id, 2, 30, 15, 998),
            )
        )

    async def get_rank_assets(self):
        self.asset_calls += 1
        return (RankAsset(8, "Oracle"),)


class FakeScoutLiveClient:
    def __init__(self) -> None:
        self.broadcast_urls: list[str] = []

    async def get_match_players(
        self,
        match_id: int,
        broadcast_url: str,
    ) -> LiveMatchSnapshot:
        self.broadcast_urls.append(broadcast_url)
        return LiveMatchSnapshot(
            match_id,
            65,
            (
                LivePlayer(
                    1001,
                    "Ducky",
                    1,
                    2,
                    1,
                    0,
                    0,
                    0,
                    0,
                    upgrades=(1548066885, 2, 999),
                ),
                LivePlayer(1002, "Goose", 1, 3, 7, 0, 0, 0, 0),
            ),
        )


class DeadlockServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_match_id_token_resolves_numeric_and_top_200_values(self) -> None:
        api = FakeScoutAPI()
        service = DeadlockService(api)  # type: ignore[arg-type]
        cog = object.__new__(DeadlockCog)
        cog.service = service

        self.assertEqual(await cog._resolve_match_id(" 123 "), 123)
        self.assertEqual(await cog._resolve_match_id("TOP-200"), 111)
        with self.assertRaisesRegex(ValueError, "top-200"):
            await cog._resolve_match_id("random")

    async def test_random_top_200_match_uses_cached_active_feed(self) -> None:
        api = FakeScoutAPI()
        service = DeadlockService(api)  # type: ignore[arg-type]

        first = await service.random_top_200_match_id()
        second = await service.random_top_200_match_id()

        self.assertEqual(first, 111)
        self.assertEqual(second, 111)
        self.assertEqual(api.active_match_calls, 1)

    async def test_scout_enrichment_is_cached_between_lookups(self) -> None:
        api = FakeScoutAPI()
        live_client = FakeScoutLiveClient()
        service = DeadlockService(api, live_client)  # type: ignore[arg-type]

        first = await service.scout_match(123)
        second = await service.scout_match(123)

        self.assertEqual(len(first.players), 2)
        self.assertEqual(len(second.players), 2)
        self.assertEqual(api.rank_calls, 2)
        self.assertEqual(api.history_calls, 2)
        self.assertEqual(api.experience_calls, 1)
        self.assertEqual(api.asset_calls, 1)
        self.assertEqual(api.hero_asset_calls, 1)
        self.assertEqual(api.item_asset_calls, 1)
        self.assertEqual(api.broadcast_url_calls, 1)
        self.assertTrue(all(player.total_matches == 50 for player in first.players))
        self.assertTrue(all(player.hero_match_share == 0.4 for player in first.players))
        self.assertTrue(all(len(player.top_heroes) == 2 for player in first.players))
        self.assertTrue(
            all(player.top_heroes[0].experience.hero_id == 2 for player in first.players)
        )
        self.assertEqual(
            live_client.broadcast_urls,
            [
                "https://relay.example.test/match/123",
                "https://relay.example.test/match/123",
            ],
        )

    async def test_live_match_maps_shopable_upgrade_ids_to_items(self) -> None:
        api = FakeScoutAPI()
        live_client = FakeScoutLiveClient()
        service = DeadlockService(api, live_client)  # type: ignore[arg-type]

        snapshot = await service.live_match(123)

        inventory = snapshot.inventory(snapshot.players[0])
        self.assertEqual([item.name for item in inventory], ["Extended Magazine"])
        self.assertIsNone(snapshot.item(2))
        self.assertEqual(api.item_asset_calls, 1)

    async def test_scoreboard_item_icons_are_downloaded_once_and_cached(self) -> None:
        api = FakeScoutAPI()
        service = DeadlockService(api, FakeScoutLiveClient())  # type: ignore[arg-type]
        snapshot = await service.live_match(123)

        first = await service.scoreboard_item_icons(snapshot)
        second = await service.scoreboard_item_icons(snapshot)

        self.assertEqual(first, second)
        self.assertEqual(set(first), {1548066885})
        self.assertEqual(api.image_download_calls, 1)

    async def test_scoreboard_hero_icons_are_downloaded_once_and_cached(self) -> None:
        api = FakeScoutAPI()
        service = DeadlockService(api, FakeScoutLiveClient())  # type: ignore[arg-type]
        snapshot = await service.live_match(123)

        first = await service.scoreboard_hero_icons(snapshot)
        second = await service.scoreboard_hero_icons(snapshot)

        self.assertEqual(first, second)
        self.assertEqual(set(first), {1})
        self.assertEqual(api.image_download_calls, 1)


class DeadlockWatchReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_lived_watch_uses_bot_authenticated_message_handle(self) -> None:
        bot_message = SimpleNamespace(id=123)

        class FakeChannel:
            def __init__(self) -> None:
                self.requested_id: int | None = None

            def get_partial_message(self, message_id: int):
                self.requested_id = message_id
                return bot_message

        channel = FakeChannel()
        webhook_message = SimpleNamespace(id=123, channel=channel)

        result = _bot_authenticated_message(webhook_message)  # type: ignore[arg-type]

        self.assertIs(result, bot_message)
        self.assertEqual(channel.requested_id, 123)

    async def test_idle_open_stream_is_closed_and_eventually_marked_ended(self) -> None:
        snapshot = LiveMatchSnapshot(
            123,
            840,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 1, 0, 0, 10_000),),
        )
        never = __import__("asyncio").Event()

        async def hanging_stream():
            await never.wait()
            if False:
                yield snapshot

        async def empty_stream():
            if False:
                yield snapshot

        class FakeService:
            def stream_live_match(self, _match_id: int):
                return empty_stream()

        class FakeMessage:
            id = 41

            def __init__(self) -> None:
                self.embeds = []

            async def edit(self, *, embed, attachments=None, view=None):
                del view
                self.embeds.append(embed)
                for attachment in attachments or ():
                    attachment.close()

        cog = object.__new__(DeadlockCog)
        cog.service = FakeService()
        cog._live_watches = {}
        message = FakeMessage()

        with (
            patch("duckies_bot.cogs.deadlock._WATCH_IDLE_SECONDS", 0.01),
            patch("duckies_bot.cogs.deadlock._WATCH_RECONNECT_DELAYS", (0, 0)),
        ):
            await cog._update_live_watch(  # type: ignore[arg-type]
                (1, 123),
                hanging_stream(),
                message,
                None,
                snapshot,
            )

        self.assertTrue(any("Reconnecting" in embed.title for embed in message.embeds))
        self.assertIn("Ended", message.embeds[-1].title)

    async def test_clean_stream_end_reconnects_and_keeps_newer_state(self) -> None:
        initial = LiveMatchSnapshot(
            123,
            840,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 1, 0, 0, 10_000),),
        )
        recovered = LiveMatchSnapshot(
            123,
            900,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 2, 0, 0, 12_000),),
        )

        async def empty_stream():
            if False:
                yield initial

        async def recovered_stream():
            yield recovered

        class FakeService:
            def __init__(self) -> None:
                self.calls = 0

            def stream_live_match(self, _match_id: int):
                self.calls += 1
                return recovered_stream() if self.calls == 1 else empty_stream()

        class FakeMessage:
            id = 42

            def __init__(self) -> None:
                self.embeds = []

            async def edit(self, *, embed, attachments=None, view=None):
                del view
                self.embeds.append(embed)
                for attachment in attachments or ():
                    attachment.close()

        cog = object.__new__(DeadlockCog)
        cog.service = FakeService()
        cog._live_watches = {}
        message = FakeMessage()

        with (
            patch("duckies_bot.cogs.deadlock._WATCH_REFRESH_SECONDS", 0),
            patch("duckies_bot.cogs.deadlock._WATCH_RECONNECT_DELAYS", (0,)),
        ):
            await cog._update_live_watch(  # type: ignore[arg-type]
                (1, 123),
                empty_stream(),
                message,
                None,
                initial,
            )

        self.assertEqual(cog.service.calls, 2)
        self.assertTrue(any("15:00" in (embed.description or "") for embed in message.embeds))
        self.assertIn("Ended", message.embeds[-1].title)

    async def test_repeated_connection_errors_mark_embed_unreachable(self) -> None:
        snapshot = LiveMatchSnapshot(
            123,
            840,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 1, 0, 0, 10_000),),
        )

        async def failing_stream():
            if False:
                yield snapshot
            raise DeadlockAPIError("parser unavailable")

        class FakeService:
            def stream_live_match(self, _match_id: int):
                return failing_stream()

        class FakeMessage:
            id = 43

            def __init__(self) -> None:
                self.embeds = []

            async def edit(self, *, embed, attachments=None, view=None):
                del view
                self.embeds.append(embed)
                for attachment in attachments or ():
                    attachment.close()

        cog = object.__new__(DeadlockCog)
        cog.service = FakeService()
        cog._live_watches = {}
        message = FakeMessage()

        with patch("duckies_bot.cogs.deadlock._WATCH_RECONNECT_DELAYS", (0,)):
            await cog._update_live_watch(  # type: ignore[arg-type]
                (1, 123),
                failing_stream(),
                message,
                None,
                snapshot,
            )

        self.assertIn("Feed unavailable", message.embeds[-1].title)
        self.assertIn("unreachable", message.embeds[-1].description)


class DeadlockWatchStopTests(unittest.TestCase):
    def test_defaults_to_most_recent_active_watch_in_the_guild(self) -> None:
        class FakeTask:
            def __init__(self, done: bool) -> None:
                self._done = done

            def done(self) -> bool:
                return self._done

        def watch(message_id: int, *, done: bool = False):
            return SimpleNamespace(
                message=SimpleNamespace(id=message_id),
                task=FakeTask(done),
            )

        cog = object.__new__(DeadlockCog)
        cog._live_watches = {
            (1, 100): watch(10),
            (1, 200): watch(30),
            (1, 300): watch(40, done=True),
            (2, 400): watch(50),
        }

        selected = cog._most_recent_live_watch(1)

        self.assertIsNotNone(selected)
        self.assertEqual(selected[0], 200)
        self.assertIsNone(cog._most_recent_live_watch(3))


class DeadlockWatchViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_uses_the_scoreboard_attachment(self) -> None:
        snapshot = LiveMatchSnapshot(
            123,
            100,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 1, 0, 2, 10_000),),
        )

        embed = DeadlockWatchView(snapshot, requester_id=42).render()

        self.assertEqual(embed.image.url, "attachment://deadlock-scoreboard.png")
        self.assertEqual(len(embed.fields), 0)

    async def test_discord_overview_leaves_scoreboard_as_a_raw_attachment(self) -> None:
        snapshot = LiveMatchSnapshot(
            123,
            100,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 1, 0, 2, 10_000),),
        )

        view = DeadlockWatchView(
            snapshot,
            requester_id=42,
            embed_scoreboard=False,
            scoreboard_layout="discord",
        )
        embed = view.render()

        self.assertIsNone(embed)
        self.assertEqual(view.scoreboard_layout, "discord")

    async def test_view_keeps_selected_tab_and_records_snapshot_changes(self) -> None:
        initial = LiveMatchSnapshot(
            123,
            100,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 1, 0, 2, 10_000, upgrades=(100,)),),
            items=(ItemSummary(100, "Basic Magazine", None, "weapon", 1, 500, True),),
        )
        updated = LiveMatchSnapshot(
            123,
            120,
            (
                LivePlayer(
                    1001,
                    "Ducky",
                    1,
                    2,
                    1,
                    2,
                    1,
                    2,
                    12_000,
                    upgrades=(101,),
                ),
            ),
            items=(ItemSummary(101, "Titanic Magazine", None, "weapon", 3, 3000, True),),
            kill_events=(LiveKillEvent(120, 120, 1001, 1002),),
        )
        updated = LiveMatchSnapshot(
            updated.match_id,
            updated.game_time_seconds,
            updated.players
            + (LivePlayer(1002, "Goose", 2, 3, 7, 0, 1, 0, 9_000),),
            updated.heroes,
            updated.items,
            updated.kill_events,
        )
        view = DeadlockWatchView(initial, requester_id=42)
        view.tab = "combat"

        view.update_snapshot(updated)

        self.assertEqual(view.tab, "combat")
        self.assertIn("Combat", view.render().title)
        self.assertTrue(any("Ducky** killed **Goose" in event for event in view.timeline))
        self.assertTrue(
            any(
                "replaced **Basic Magazine** with **Titanic Magazine**" in event
                for event in view.timeline
            )
        )
        self.assertEqual(len(view.children), 6)

    async def test_timeline_names_added_and_removed_items(self) -> None:
        items = (
            ItemSummary(100, "Basic Magazine", None, "weapon", 1, 500, True),
            ItemSummary(200, "Extra Health", None, "vitality", 1, 500, True),
            ItemSummary(300, "Mystic Burst", None, "spirit", 1, 500, True),
        )
        initial = LiveMatchSnapshot(
            123,
            100,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 0, 0, 0, 10_000, upgrades=(100,)),),
            items=items,
        )
        updated = LiveMatchSnapshot(
            123,
            120,
            (
                LivePlayer(
                    1001,
                    "Ducky",
                    1,
                    2,
                    1,
                    0,
                    0,
                    0,
                    12_000,
                    upgrades=(100, 200, 300),
                ),
            ),
            items=items,
        )
        cleared = LiveMatchSnapshot(
            123,
            140,
            (LivePlayer(1001, "Ducky", 1, 2, 1, 0, 0, 0, 13_000, upgrades=()),),
            items=(),
        )
        view = DeadlockWatchView(initial, requester_id=42)

        view.update_snapshot(updated)
        view.update_snapshot(cleared)

        self.assertTrue(
            any("added **Extra Health**, **Mystic Burst**" in event for event in view.timeline)
        )
        self.assertTrue(
            any(
                "removed **Basic Magazine**, **Extra Health**, **Mystic Burst**" in event
                for event in view.timeline
            )
        )


class DeadlockScoutViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_view_starts_on_overview_and_expires_with_disabled_buttons(self) -> None:
        report = MatchScout(
            match_id=123,
            game_time_seconds=10,
            players=(
                ScoutedPlayer(
                    LivePlayer(1001, "Ducky", 1, 2, 1, 0, 0, 0, 0),
                    HeroSummary(1, "Infernus", None),
                    PlayerRank(8, 5),
                    "Oracle",
                    HeroExperience(1001, 1, 50, 30, 999),
                    ("W", "L"),
                ),
            ),
        )
        view = DeadlockScoutView(report, requester_id=42)
        buttons = {button.custom_id: button for button in view.children}

        self.assertIsNone(view.player_index)
        self.assertTrue(buttons["deadlock_scout:overview"].disabled)
        self.assertFalse(buttons["deadlock_scout:next"].disabled)

        view.player_index = 0
        view._sync_buttons()
        self.assertFalse(buttons["deadlock_scout:overview"].disabled)

        class FakeResponse:
            def __init__(self) -> None:
                self.edits: list[dict[str, object]] = []

            async def edit_message(self, **kwargs: object) -> None:
                self.edits.append(kwargs)

        class FakeInteraction:
            def __init__(self) -> None:
                self.response = FakeResponse()

        interaction = FakeInteraction()
        await view.next_player.callback(interaction)  # type: ignore[arg-type]
        self.assertIn("embed", interaction.response.edits[-1])
        await view.overview.callback(interaction)  # type: ignore[arg-type]
        self.assertIn("embeds", interaction.response.edits[-1])

        await view.on_timeout()
        self.assertTrue(all(button.disabled for button in view.children))
