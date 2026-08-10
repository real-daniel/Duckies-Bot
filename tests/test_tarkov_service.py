"""Tests for Tarkov item search orchestration."""

import unittest

from duckies_bot.features.tarkov.models import (
    ServerComponentStatus,
    TarkovItem,
    TarkovServerStatus,
    TarkovTask,
    TaskRewards,
)
from duckies_bot.features.tarkov.service import TarkovService
from duckies_bot.providers.tarkov.errors import (
    InvalidItemQueryError,
    InvalidTaskQueryError,
    ItemNotFoundError,
    TaskNotFoundError,
)


ITEM = TarkovItem("id", "LEDX", "LEDX", None, 1, 1, 2, 1, (), ())
EMPTY_REWARDS = TaskRewards((), (), (), ())
TASK = TarkovTask(
    "task-id", "Private Clinic", "Therapist", 35, (), (), (), (), (), 1,
    None, None, True, False, "Any", 0, 0, EMPTY_REWARDS, EMPTY_REWARDS, EMPTY_REWARDS,
)
STATUS = TarkovServerStatus(ServerComponentStatus("Global", None, 0, "OK"), (), ())


class FakeProvider:
    def __init__(self, error: Exception | None = None, task_error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.task_calls: list[str] = []
        self.status_calls = 0
        self.error = error
        self.task_error = task_error

    async def search_item(self, item_name: str) -> TarkovItem:
        self.calls.append(item_name)
        if self.error:
            raise self.error
        return ITEM

    async def search_task(self, task_name: str) -> TarkovTask:
        self.task_calls.append(task_name)
        if self.task_error:
            raise self.task_error
        return TASK

    async def get_server_status(self) -> TarkovServerStatus:
        self.status_calls += 1
        return STATUS


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TarkovServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_input_and_caches_case_insensitively(self) -> None:
        provider = FakeProvider()
        service = TarkovService(provider)
        self.assertIs(await service.lookup_item("  ledx   item "), ITEM)
        self.assertIs(await service.lookup_item("LEDX ITEM"), ITEM)
        self.assertEqual(provider.calls, ["ledx item"])

    async def test_cache_expires(self) -> None:
        provider = FakeProvider()
        clock = FakeClock()
        service = TarkovService(provider, cache_ttl_seconds=60, clock=clock)
        await service.lookup_item("LEDX")
        clock.now = 60
        await service.lookup_item("LEDX")
        self.assertEqual(provider.calls, ["LEDX", "LEDX"])

    async def test_blank_input_is_rejected(self) -> None:
        provider = FakeProvider()
        with self.assertRaises(InvalidItemQueryError):
            await TarkovService(provider).lookup_item("   ")
        self.assertEqual(provider.calls, [])

    async def test_errors_are_not_cached(self) -> None:
        provider = FakeProvider(ItemNotFoundError("missing"))
        service = TarkovService(provider)
        for _ in range(2):
            with self.assertRaises(ItemNotFoundError):
                await service.lookup_item("missing")
        self.assertEqual(provider.calls, ["missing", "missing"])

    async def test_task_input_is_normalized_and_cached(self) -> None:
        provider = FakeProvider()
        service = TarkovService(provider)
        self.assertIs(await service.lookup_task("  private   clinic "), TASK)
        self.assertIs(await service.lookup_task("PRIVATE CLINIC"), TASK)
        self.assertEqual(provider.task_calls, ["private clinic"])

    async def test_blank_task_input_is_rejected(self) -> None:
        provider = FakeProvider()
        with self.assertRaises(InvalidTaskQueryError):
            await TarkovService(provider).lookup_task("   ")
        self.assertEqual(provider.task_calls, [])

    async def test_task_errors_are_not_cached(self) -> None:
        provider = FakeProvider(task_error=TaskNotFoundError("missing"))
        service = TarkovService(provider)
        for _ in range(2):
            with self.assertRaises(TaskNotFoundError):
                await service.lookup_task("missing")
        self.assertEqual(provider.task_calls, ["missing", "missing"])

    async def test_server_status_has_short_cache(self) -> None:
        provider = FakeProvider()
        clock = FakeClock()
        service = TarkovService(provider, status_cache_ttl_seconds=15, clock=clock)
        self.assertIs(await service.get_server_status(), STATUS)
        self.assertIs(await service.get_server_status(), STATUS)
        self.assertEqual(provider.status_calls, 1)
        clock.now = 15
        await service.get_server_status()
        self.assertEqual(provider.status_calls, 2)
