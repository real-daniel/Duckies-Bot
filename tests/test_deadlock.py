"""Tests for Deadlock active-match lookup."""

from typing import Any
import unittest

from duckies_bot.features.deadlock import DeadlockService
from duckies_bot.features.deadlock.formatter import (
    build_live_embed,
    build_live_match_embed,
    build_scout_overview_embeds,
    build_scout_player_embed,
)
from duckies_bot.features.deadlock.models import (
    ActiveMatch,
    HeroExperience,
    HeroSummary,
    LiveLookup,
    LiveChatMessage,
    LiveMatchSnapshot,
    LivePlayer,
    MatchScout,
    PlayerHistory,
    PlayerRank,
    RankAsset,
    ScoutedPlayer,
)
from duckies_bot.providers.deadlock.client import DeadlockClient
from duckies_bot.providers.deadlock.live_client import DeadlockLiveClient
from duckies_bot.views import DeadlockScoutView
from duckies_bot.cogs.deadlock import (
    DeadlockCog,
    _format_chat_message,
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
            ]
        )
        client = DeadlockClient(session=session)  # type: ignore[arg-type]
        hero = await client.get_hero(1)
        heroes = await client.get_heroes()
        self.assertEqual(hero.name, "Infernus")
        self.assertEqual([item.name for item in heroes], ["Infernus", "Seven"])

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
        events.append(
            'event: player_controller_entity_update\n'
            'data: {"steam_id":2001,"steam_name":"Player 1","player_slot":1,"team":2}\n\n'
        )
        session = FakeSSESession(FakeSSEResponse("".join(events)))

        snapshot = await DeadlockLiveClient(session=session).get_match_players(  # type: ignore[arg-type]
            123,
            "https://relay.example.test/match/123",
        )

        self.assertEqual(len(snapshot.players), 8)

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


class DeadlockFormatterTests(unittest.TestCase):
    def test_live_embed_contains_match_summary(self) -> None:
        session = FakeSession([FakeResponse(active_match_document())])
        client = DeadlockClient(session=session)  # type: ignore[arg-type]
        match = __import__("asyncio").run(client.get_active_match(123456))
        assert match is not None
        player = match.player(123456)
        assert player is not None
        lookup = LiveLookup(match, player, HeroSummary(1, "Infernus", None))

        embed = build_live_embed("Ducky", lookup)

        self.assertIn("Ducky is in", embed.title)
        self.assertTrue(any(field.name == "Hero" and field.value == "Infernus" for field in embed.fields))
        self.assertIn("api.deadlock-api.com", embed.footer.text)

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

    def test_scout_embed_explains_rank_comfort_and_form(self) -> None:
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
                ),
            ),
        )

        embeds = build_scout_overview_embeds(scout, highlighted_account_id=1001)

        self.assertEqual(len(embeds), 2)
        self.assertEqual(embeds[1].title, "Amber team")
        self.assertEqual(embeds[1].fields[0].name, "Ducky · You")
        value = embeds[1].fields[0].value
        self.assertIn("**Infernus**", value)
        self.assertIn("Oracle V · High comfort", value)
        self.assertIn("50 games · 60% WR", value)
        self.assertIn("`W W L W L`", value)

        detail = build_scout_player_embed(scout, 0, highlighted_account_id=1001)
        self.assertIn("Player 1 of 1", detail.description)
        self.assertIn("Your linked account", detail.description)
        self.assertTrue(any(field.name == "Rank" and field.value == "Oracle V" for field in detail.fields))
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
        self.active_match_calls = 0

    async def get_active_matches(self) -> tuple[ActiveMatch, ...]:
        self.active_match_calls += 1
        return (
            ActiveMatch(111, None, None, None, None, None, None, None, None, None, None, ()),
            ActiveMatch(222, None, None, None, None, None, None, None, None, None, None, ()),
        )

    async def get_live_broadcast_url(self, match_id: int) -> str:
        self.broadcast_url_calls += 1
        return f"https://relay.example.test/match/{match_id}"

    async def get_heroes(self) -> tuple[HeroSummary, ...]:
        self.hero_asset_calls += 1
        return (HeroSummary(1, "Infernus", None),)

    async def get_player_rank(self, account_id: int) -> PlayerRank:
        self.rank_calls += 1
        return PlayerRank(8, 5)

    async def get_player_history(self, account_id: int) -> PlayerHistory:
        self.history_calls += 1
        return PlayerHistory(account_id, ("W", "L"))

    async def get_hero_experience(self, account_ids: list[int], hero_ids: list[int]):
        self.experience_calls += 1
        return tuple(HeroExperience(account_id, 1, 20, 11, 999) for account_id in account_ids)

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
                LivePlayer(1001, "Ducky", 1, 2, 1, 0, 0, 0, 0),
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
        self.assertIn(await cog._resolve_match_id("TOP-200"), (111, 222))
        with self.assertRaisesRegex(ValueError, "top-200"):
            await cog._resolve_match_id("random")

    async def test_random_top_200_match_uses_cached_active_feed(self) -> None:
        api = FakeScoutAPI()
        service = DeadlockService(api)  # type: ignore[arg-type]

        first = await service.random_top_200_match_id()
        second = await service.random_top_200_match_id()

        self.assertIn(first, (111, 222))
        self.assertIn(second, (111, 222))
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
        self.assertEqual(api.broadcast_url_calls, 1)
        self.assertEqual(
            live_client.broadcast_urls,
            [
                "https://relay.example.test/match/123",
                "https://relay.example.test/match/123",
            ],
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
