"""Tests for the mocked Tarkov JSON dataset boundary."""

import asyncio
import unittest
from typing import Any
from urllib.parse import urlsplit

from duckies_bot.providers.tarkov.client import TarkovClient
from duckies_bot.providers.tarkov.errors import (
    InvalidTarkovResponseError,
    ItemNotFoundError,
    TarkovRateLimitError,
    TarkovTimeoutError,
    TarkovUnavailableError,
    TaskNotFoundError,
)


LEDX_ID = "5c0530ee86f774697952d952"
THERAPIST_ID = "54cb57776803fa99248b456e"
ITEM_NAME_KEY = f"{LEDX_ID} Name"
ITEM_SHORT_NAME_KEY = f"{LEDX_ID} ShortName"
TASK_NAME_KEY = "private-clinic name"
TRADER_NAME_KEY = f"{THERAPIST_ID} Nickname"
CUSTOMS_ID = "customs-id"
CUSTOMS_NAME_KEY = f"{CUSTOMS_ID} Name"
KEY_ID = "key-id"
OBJECTIVE_KEY = "objective-id"


def raw_item(
    item_id: str = LEDX_ID,
    name_key: str = ITEM_NAME_KEY,
    short_name_key: str = ITEM_SHORT_NAME_KEY,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "name": name_key,
        "shortName": short_name_key,
        "iconLink": "https://example.test/icon.png",
        "avg24hPrice": 844_569,
        "low24hPrice": 650_000,
        "high24hPrice": 980_000,
        "lastLowPrice": 795_000,
        "sellToTrader": [
            {"priceRUB": 494_700, "trader": THERAPIST_ID},
        ],
        "buyFromTrader": [
            {
                "priceRUB": 500_000,
                "trader": THERAPIST_ID,
                "minTraderLevel": 4,
                "buyLimit": 1,
                "restockAmount": 10,
                "taskUnlock": "task-id",
            }
        ],
    }


def documents(items: dict[str, Any] | None = None) -> dict[str, Any]:
    item_data = items if items is not None else {
        LEDX_ID: raw_item(),
        KEY_ID: raw_item(KEY_ID, "Clinic key", "Key"),
    }
    return {
        "/pve/items": {"data": {"items": item_data}},
        "/pve/items_en": {
            "data": {
                ITEM_NAME_KEY: "LEDX Skin Transilluminator",
                ITEM_SHORT_NAME_KEY: "LEDX",
                "Clinic key": "Clinic key",
                "Key": "Key",
            }
        },
        "/pve/traders": {
            "data": {
                THERAPIST_ID: {
                    "id": THERAPIST_ID,
                    "name": TRADER_NAME_KEY,
                    "levels": [
                        {"level": 1, "requiredPlayerLevel": 0},
                        {"level": 4, "requiredPlayerLevel": 35},
                    ],
                }
            }
        },
        "/pve/traders_en": {"data": {TRADER_NAME_KEY: "Therapist"}},
        "/pve/tasks": {
            "data": {
                "tasks": {
                    "task-id": {
                        "id": "task-id",
                        "name": TASK_NAME_KEY,
                        "trader": THERAPIST_ID,
                        "minPlayerLevel": 35,
                        "map": CUSTOMS_ID,
                        "objectives": [
                            {
                                "type": "findItem",
                                "description": OBJECTIVE_KEY,
                                "count": 1,
                                "foundInRaid": True,
                                "items": [LEDX_ID],
                                "maps": [CUSTOMS_ID],
                                "requiredKeys": [KEY_ID],
                            },
                            {
                                "type": "giveItem",
                                "description": OBJECTIVE_KEY,
                                "count": 1,
                                "foundInRaid": True,
                                "items": [LEDX_ID],
                            },
                        ],
                        "taskRequirements": [],
                        "neededKeys": [KEY_ID],
                        "experience": 30600,
                        "wikiLink": "https://example.test/wiki",
                        "taskImageLink": "https://example.test/task.webp",
                        "kappaRequired": True,
                        "finishRewards": {
                            "items": [{"item": LEDX_ID, "count": 2}],
                            "traderStanding": [{"trader": THERAPIST_ID, "standing": 0.05}],
                        },
                    }
                }
            }
        },
        "/pve/tasks_en": {
            "data": {
                TASK_NAME_KEY: "Private Clinic",
                OBJECTIVE_KEY: "Find a LEDX in raid",
            }
        },
        "/pve/maps": {
            "data": {
                "maps": {
                    CUSTOMS_ID: {
                        "id": CUSTOMS_ID,
                        "name": CUSTOMS_NAME_KEY,
                        "normalizedName": "customs",
                    }
                }
            }
        },
        "/pve/maps_en": {"data": {CUSTOMS_NAME_KEY: "Customs"}},
    }


class FakeResponse:
    def __init__(
        self,
        body: Any = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
        json_error: Exception | None = None,
        enter_error: Exception | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.json_error = json_error
        self.enter_error = enter_error

    async def __aenter__(self) -> "FakeResponse":
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self, **kwargs: object) -> Any:
        if self.json_error:
            raise self.json_error
        return self.body


class FakeSession:
    def __init__(
        self,
        response_documents: dict[str, Any] | None = None,
        default_response: FakeResponse | None = None,
        use_etags: bool = False,
    ) -> None:
        self.documents = response_documents or {}
        self.default_response = default_response
        self.use_etags = use_etags
        self.closed = False
        self.calls: list[str] = []
        self.request_headers: list[dict[str, str]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        path = urlsplit(url).path
        headers = dict(kwargs.get("headers", {}))
        self.calls.append(path)
        self.request_headers.append(headers)
        if self.default_response is not None:
            return self.default_response
        if self.use_etags and headers.get("If-None-Match") == f'"{path}"':
            return FakeResponse(status=304)
        response_headers = {"ETag": f'"{path}"'} if self.use_etags else None
        return FakeResponse(self.documents[path], headers=response_headers)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TarkovClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_server_status_and_removes_duplicate_global_component(self) -> None:
        status_document = {
            "/status": {
                "data": {
                    "generalStatus": {
                        "name": "Global",
                        "message": "",
                        "status": 0,
                        "statusCode": "OK",
                    },
                    "currentStatuses": [
                        {"name": "Matchmaking", "status": 2, "statusCode": "Unstable"},
                        {"name": "Global", "status": 0, "statusCode": "OK"},
                    ],
                    "messages": [
                        {
                            "content": "Matchmaking delays",
                            "time": "2026-08-09T12:00:00+00:00",
                            "type": 2,
                            "solveTime": None,
                            "statusCode": "Unstable",
                        }
                    ],
                }
            }
        }
        status = await TarkovClient(session=FakeSession(status_document)).get_server_status()
        self.assertEqual(status.general_status.status_code, "OK")
        self.assertEqual(tuple(component.name for component in status.components), ("Matchmaking",))
        self.assertEqual(status.components[0].status_code, "Unstable")
        self.assertTrue(status.messages[0].is_active)

    async def test_loads_localizes_and_joins_all_pve_datasets(self) -> None:
        session = FakeSession(documents())
        item = await TarkovClient(session=session).search_item("LEDX")
        self.assertEqual(item.name, "LEDX Skin Transilluminator")
        self.assertEqual(item.short_name, "LEDX")
        self.assertEqual(item.avg24h_price, 844_569)
        self.assertEqual(item.task_names, ("Private Clinic",))
        self.assertEqual(item.best_trader_offer.vendor_name, "Therapist")
        self.assertEqual(set(session.calls), set(documents()))

    async def test_dataset_is_reused_for_different_searches(self) -> None:
        session = FakeSession(documents())
        client = TarkovClient(session=session)
        await client.search_item("LEDX")
        await client.search_item("transilluminator")
        self.assertEqual(len(session.calls), 8)

    async def test_expired_dataset_uses_conditional_etag_requests(self) -> None:
        clock = FakeClock()
        session = FakeSession(documents(), use_etags=True)
        client = TarkovClient(session=session, dataset_ttl_seconds=60, clock=clock)
        first = await client.search_item("LEDX")
        clock.now = 60
        second = await client.search_item("LEDX")
        self.assertEqual(second, first)
        self.assertEqual(len(session.calls), 16)
        self.assertTrue(all("If-None-Match" in headers for headers in session.request_headers[8:]))

    async def test_loads_and_joins_task_prep_details(self) -> None:
        task = await TarkovClient(session=FakeSession(documents())).search_task("private")
        self.assertEqual(task.name, "Private Clinic")
        self.assertEqual(task.trader_name, "Therapist")
        self.assertEqual(task.min_player_level, 35)
        self.assertEqual(tuple(map_.name for map_ in task.maps), ("Customs",))
        self.assertEqual(len(task.required_items), 1)
        self.assertEqual(task.required_items[0].item_names, ("LEDX Skin Transilluminator",))
        self.assertTrue(task.required_items[0].found_in_raid)
        self.assertEqual(task.required_keys, ("Clinic key",))
        self.assertEqual(task.finish_rewards.items[0].count, 2)
        self.assertIn("Therapist +0.05", task.finish_rewards.trader_standing)

    async def test_lists_localized_tasks_for_quest_log_matching(self) -> None:
        tasks = await TarkovClient(session=FakeSession(documents())).list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].name, "Private Clinic")

    async def test_lists_trader_to_flea_flip_candidates(self) -> None:
        flips = await TarkovClient(session=FakeSession(documents())).list_trader_flips()
        ledx = next(flip for flip in flips if flip.item_id == LEDX_ID)
        self.assertEqual(ledx.item_name, "LEDX Skin Transilluminator")
        self.assertEqual(ledx.trader_name, "Therapist")
        self.assertEqual(ledx.trader_price, 500_000)
        self.assertEqual(ledx.min_trader_level, 4)
        self.assertEqual(ledx.required_player_level, 35)
        self.assertEqual(ledx.task_unlock_name, "Private Clinic")

    async def test_no_matching_task_raises_not_found(self) -> None:
        client = TarkovClient(session=FakeSession(documents()))
        with self.assertRaises(TaskNotFoundError):
            await client.search_task("definitely missing")

    async def test_ranking_uses_localized_full_short_and_prefix_names(self) -> None:
        select = TarkovClient._select_best_item
        translations = {
            "long-name": "Long LEDX-related item",
            "exact-name": "LEDX",
            "short-key": "LEDX",
            "prefix-long": "LEDX long prefix",
            "prefix-short": "LEDX short",
        }
        items = [raw_item(name_key="long-name"), raw_item(name_key="exact-name")]
        self.assertEqual(select(items, "ledx", translations)["name"], "exact-name")
        items = [raw_item(name_key="long-name", short_name_key="short-key"), raw_item(name_key="prefix-short")]
        self.assertEqual(select(items, "ledx", translations)["shortName"], "short-key")
        items = [raw_item(name_key="prefix-long"), raw_item(name_key="prefix-short")]
        self.assertEqual(select(items, "ledx", translations)["name"], "prefix-short")

    async def test_no_matching_item_raises_not_found(self) -> None:
        client = TarkovClient(session=FakeSession(documents()))
        with self.assertRaises(ItemNotFoundError):
            await client.search_item("definitely missing")

    async def test_malformed_dataset_is_rejected(self) -> None:
        broken = documents()
        broken["/pve/items"] = {"data": {"items": []}}
        with self.assertRaises(InvalidTarkovResponseError):
            await TarkovClient(session=FakeSession(broken)).search_item("LEDX")

    async def test_rate_limit_timeout_and_outage(self) -> None:
        limited = TarkovClient(
            session=FakeSession(
                default_response=FakeResponse(status=429, headers={"Retry-After": "2.5"})
            )
        )
        with self.assertRaises(TarkovRateLimitError) as context:
            await limited.search_item("LEDX")
        self.assertEqual(context.exception.retry_after, 2.5)

        timed_out = TarkovClient(
            session=FakeSession(default_response=FakeResponse(enter_error=asyncio.TimeoutError()))
        )
        with self.assertRaises(TarkovTimeoutError):
            await timed_out.search_item("LEDX")

        unavailable = TarkovClient(session=FakeSession(default_response=FakeResponse(status=503)))
        with self.assertRaises(TarkovUnavailableError) as context:
            await unavailable.search_item("LEDX")
        self.assertEqual(context.exception.status_code, 503)

    async def test_stale_dataset_is_used_when_refresh_fails(self) -> None:
        clock = FakeClock()
        session = FakeSession(documents())
        client = TarkovClient(session=session, dataset_ttl_seconds=60, clock=clock)
        first = await client.search_item("LEDX")
        clock.now = 60
        session.default_response = FakeResponse(status=503)
        second = await client.search_item("LEDX")
        self.assertEqual(second, first)
