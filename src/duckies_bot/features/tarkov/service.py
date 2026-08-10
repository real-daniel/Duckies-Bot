"""Tarkov search business rules and short-lived caching."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ...providers.tarkov.errors import InvalidItemQueryError, InvalidTaskQueryError
from .models import TarkovItem, TarkovServerStatus, TarkovTask


class TarkovProvider(Protocol):
    async def search_item(self, item_name: str) -> TarkovItem: ...

    async def search_task(self, task_name: str) -> TarkovTask: ...

    async def get_server_status(self) -> TarkovServerStatus: ...


# Retain the original public name for callers that only use item lookup.
ItemProvider = TarkovProvider


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    item: TarkovItem
    expires_at: float


@dataclass(frozen=True, slots=True)
class _TaskCacheEntry:
    task: TarkovTask
    expires_at: float


@dataclass(frozen=True, slots=True)
class _StatusCacheEntry:
    server_status: TarkovServerStatus
    expires_at: float


class TarkovService:
    def __init__(
        self,
        provider: TarkovProvider,
        cache_ttl_seconds: float = 60.0,
        status_cache_ttl_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds cannot be negative")
        if status_cache_ttl_seconds < 0:
            raise ValueError("status_cache_ttl_seconds cannot be negative")
        self._provider = provider
        self._cache_ttl_seconds = cache_ttl_seconds
        self._status_cache_ttl_seconds = status_cache_ttl_seconds
        self._clock = clock
        self._cache: dict[str, _CacheEntry] = {}
        self._task_cache: dict[str, _TaskCacheEntry] = {}
        self._status_cache: _StatusCacheEntry | None = None

    async def lookup_item(self, item_name: str) -> TarkovItem:
        normalized_name = " ".join(item_name.split())
        if not normalized_name:
            raise InvalidItemQueryError()

        cache_key = normalized_name.casefold()
        now = self._clock()
        entry = self._cache.get(cache_key)
        if entry is not None and entry.expires_at > now:
            return entry.item
        if entry is not None:
            self._cache.pop(cache_key, None)

        item = await self._provider.search_item(normalized_name)
        self._cache[cache_key] = _CacheEntry(item, self._clock() + self._cache_ttl_seconds)
        return item

    async def lookup_task(self, task_name: str) -> TarkovTask:
        normalized_name = " ".join(task_name.split())
        if not normalized_name:
            raise InvalidTaskQueryError()

        cache_key = normalized_name.casefold()
        now = self._clock()
        entry = self._task_cache.get(cache_key)
        if entry is not None and entry.expires_at > now:
            return entry.task
        if entry is not None:
            self._task_cache.pop(cache_key, None)

        task = await self._provider.search_task(normalized_name)
        self._task_cache[cache_key] = _TaskCacheEntry(
            task,
            self._clock() + self._cache_ttl_seconds,
        )
        return task

    async def get_server_status(self) -> TarkovServerStatus:
        now = self._clock()
        entry = self._status_cache
        if entry is not None and entry.expires_at > now:
            return entry.server_status

        server_status = await self._provider.get_server_status()
        self._status_cache = _StatusCacheEntry(
            server_status,
            self._clock() + self._status_cache_ttl_seconds,
        )
        return server_status

    def clear_cache(self) -> None:
        self._cache.clear()
        self._task_cache.clear()
        self._status_cache = None
