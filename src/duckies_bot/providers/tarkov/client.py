"""Asynchronous client for the supported Tarkov.dev JSON datasets."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp

from ...features.tarkov.models import (
    PriceHistoryPoint,
    ProfitRecipe,
    RecipeIngredient,
    ServerComponentStatus,
    ServerStatusMessage,
    TarkovItem,
    TarkovServerStatus,
    TarkovTask,
    TaskItemRequirement,
    TaskMap,
    TaskObjective,
    TaskRewardItem,
    TaskRewards,
    TraderFlipCandidate,
    VendorPrice,
)
from .errors import (
    InvalidTarkovResponseError,
    ItemNotFoundError,
    TaskNotFoundError,
    TarkovAPIError,
    TarkovRateLimitError,
    TarkovTimeoutError,
    TarkovUnavailableError,
)


_ITEM_REFERENCE_FIELDS = (
    "item",
    "items",
    "markerItem",
    "containsAll",
    "containsOne",
    "usingWeapon",
    "usingWeaponMods",
    "wearing",
    "notWearing",
    "requiredKeys",
)


@dataclass(frozen=True, slots=True)
class _Dataset:
    items: tuple[Mapping[str, Any], ...]
    items_by_id: Mapping[str, Mapping[str, Any]]
    item_translations: Mapping[str, str]
    item_names: Mapping[str, str]
    trader_names: Mapping[str, str]
    trader_level_requirements: Mapping[str, Mapping[int, int]]
    item_task_names: Mapping[str, tuple[str, ...]]
    tasks: tuple[Mapping[str, Any], ...]
    task_translations: Mapping[str, str]
    task_names: Mapping[str, str]
    maps: Mapping[str, TaskMap]


@dataclass(frozen=True, slots=True)
class _DatasetCache:
    dataset: _Dataset
    expires_at: float


@dataclass(frozen=True, slots=True)
class _DocumentCache:
    document: Mapping[str, Any]
    etag: str | None


class TarkovClient:
    """Load and search the public PvE datasets from ``json.tarkov.dev``."""

    def __init__(
        self,
        base_url: str = "https://json.tarkov.dev",
        timeout_seconds: float = 30.0,
        dataset_ttl_seconds: float = 300.0,
        session: aiohttp.ClientSession | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if dataset_ttl_seconds < 0:
            raise ValueError("dataset_ttl_seconds cannot be negative")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.dataset_ttl_seconds = dataset_ttl_seconds
        self._session = session
        self._owns_session = session is None
        self._clock = clock
        self._dataset_cache: _DatasetCache | None = None
        self._document_cache: dict[str, _DocumentCache] = {}
        self._dataset_lock = asyncio.Lock()

    async def search_item(self, item_name: str) -> TarkovItem:
        dataset = await self._get_dataset()
        selected = self._select_best_item(
            dataset.items,
            item_name,
            dataset.item_translations,
        )
        return self._parse_item(
            selected,
            dataset.item_translations,
            dataset.trader_names,
            dataset.item_task_names,
        )

    async def search_task(self, task_name: str) -> TarkovTask:
        dataset = await self._get_dataset()
        selected = self._select_best_task(
            dataset.tasks,
            task_name,
            dataset.task_translations,
        )
        return self._parse_task(selected, dataset)

    async def list_tasks(self) -> tuple[TarkovTask, ...]:
        """Return every currently available PvE task in localized form."""

        dataset = await self._get_dataset()
        return tuple(self._parse_task(task, dataset) for task in dataset.tasks)

    async def get_server_status(self) -> TarkovServerStatus:
        document = await self._fetch_json("/status")
        data = _required_mapping(document, "data")
        general_raw = data.get("generalStatus")
        if not isinstance(general_raw, Mapping):
            raise InvalidTarkovResponseError("Status was missing generalStatus")

        raw_components = data.get("currentStatuses")
        raw_messages = data.get("messages")
        if not isinstance(raw_components, list) or not isinstance(raw_messages, list):
            raise InvalidTarkovResponseError("Status components or messages was not a list")

        general = _parse_server_component(general_raw)
        components = tuple(
            _parse_server_component(component)
            for component in raw_components
            if isinstance(component, Mapping)
            and str(component.get("name") or "").casefold() != "global"
        )
        messages = tuple(
            _parse_server_message(message)
            for message in raw_messages
            if isinstance(message, Mapping)
        )
        return TarkovServerStatus(general, components, messages)

    async def list_crafts(self) -> tuple[ProfitRecipe, ...]:
        dataset, craft_document, hideout_document, hideout_translation_document = (
            await asyncio.gather(
                self._get_dataset(),
                self._fetch_json("/pve/crafts"),
                self._fetch_json("/pve/hideout"),
                self._fetch_json("/pve/hideout_en"),
            )
        )
        raw_crafts = _required_list(craft_document, "data")
        raw_stations = _required_mapping(hideout_document, "data")
        station_translations = _translation_mapping(
            hideout_translation_document,
            "hideout_en",
        )
        station_names = _build_localized_names(raw_stations, station_translations)
        recipes = [
            _parse_profit_recipe(
                raw,
                "craft",
                dataset,
                source_names=station_names,
            )
            for raw in raw_crafts
            if isinstance(raw, Mapping)
        ]
        return tuple(recipe for recipe in recipes if recipe is not None)

    async def list_barters(self) -> tuple[ProfitRecipe, ...]:
        dataset, barter_document = await asyncio.gather(
            self._get_dataset(),
            self._fetch_json("/pve/barters"),
        )
        raw_barters = _required_list(barter_document, "data")
        recipes = [
            _parse_profit_recipe(
                raw,
                "barter",
                dataset,
                source_names=dataset.trader_names,
            )
            for raw in raw_barters
            if isinstance(raw, Mapping)
        ]
        return tuple(recipe for recipe in recipes if recipe is not None)

    async def get_price_history(self, item_id: str) -> tuple[PriceHistoryPoint, ...]:
        try:
            document = await self._fetch_json(f"/pve/prices/{item_id}")
        except InvalidTarkovResponseError:
            return ()
        raw_points = _required_list(document, "data")
        points: list[PriceHistoryPoint] = []
        for raw in raw_points:
            if not isinstance(raw, Mapping):
                continue
            minimum_price = raw.get("priceMin")
            offer_count = raw.get("offerCount")
            timestamp = raw.get("timestamp")
            if (
                isinstance(minimum_price, int)
                and not isinstance(minimum_price, bool)
                and isinstance(timestamp, int)
                and not isinstance(timestamp, bool)
            ):
                points.append(
                    PriceHistoryPoint(
                        minimum_price,
                        offer_count if isinstance(offer_count, int) else None,
                        timestamp,
                    )
                )
        return tuple(points)

    async def list_trader_flips(self) -> tuple[TraderFlipCandidate, ...]:
        dataset = await self._get_dataset()
        candidates: dict[tuple[str, str, int, str | None], TraderFlipCandidate] = {}
        for item_id, item in dataset.items_by_id.items():
            flea_price = _snapshot_flea_price(item)
            item_types = item.get("types") or []
            if flea_price is None or "noFlea" in item_types:
                continue
            for offer in item.get("buyFromTrader") or []:
                if not isinstance(offer, Mapping):
                    continue
                trader_id = offer.get("trader")
                trader_price = offer.get("priceRUB")
                if (
                    not isinstance(trader_id, str)
                    or not isinstance(trader_price, int)
                    or isinstance(trader_price, bool)
                    or trader_price <= 0
                ):
                    continue
                task_unlock = offer.get("taskUnlock")
                min_trader_level = _safe_optional_int(offer.get("minTraderLevel"))
                candidate = TraderFlipCandidate(
                    item_id=item_id,
                    item_name=dataset.item_names.get(item_id, item_id),
                    trader_name=dataset.trader_names.get(trader_id, trader_id),
                    trader_price=trader_price,
                    snapshot_flea_price=flea_price,
                    min_trader_level=min_trader_level,
                    required_player_level=(
                        dataset.trader_level_requirements.get(trader_id, {}).get(
                            min_trader_level
                        )
                        if min_trader_level is not None
                        else None
                    ),
                    task_unlock_name=(
                        dataset.task_names.get(task_unlock, task_unlock)
                        if isinstance(task_unlock, str)
                        else None
                    ),
                    buy_limit=_safe_optional_int(offer.get("buyLimit")),
                    restock_amount=_safe_optional_int(offer.get("restockAmount")),
                )
                key = (
                    item_id,
                    trader_id,
                    min_trader_level or 0,
                    task_unlock if isinstance(task_unlock, str) else None,
                )
                existing = candidates.get(key)
                if existing is None or candidate.trader_price < existing.trader_price:
                    candidates[key] = candidate
        return tuple(candidates.values())

    async def _get_dataset(self) -> _Dataset:
        now = self._clock()
        cache = self._dataset_cache
        if cache is not None and cache.expires_at > now:
            return cache.dataset

        async with self._dataset_lock:
            now = self._clock()
            cache = self._dataset_cache
            if cache is not None and cache.expires_at > now:
                return cache.dataset
            try:
                dataset = await self._load_dataset()
            except TarkovAPIError:
                if cache is not None:
                    return cache.dataset
                raise
            self._dataset_cache = _DatasetCache(
                dataset=dataset,
                expires_at=self._clock() + self.dataset_ttl_seconds,
            )
            return dataset

    async def _load_dataset(self) -> _Dataset:
        (
            item_document,
            item_translation_document,
            trader_document,
            trader_translation_document,
            task_document,
            task_translation_document,
            map_document,
            map_translation_document,
        ) = await asyncio.gather(
            self._fetch_json("/pve/items"),
            self._fetch_json("/pve/items_en"),
            self._fetch_json("/pve/traders"),
            self._fetch_json("/pve/traders_en"),
            self._fetch_json("/pve/tasks"),
            self._fetch_json("/pve/tasks_en"),
            self._fetch_json("/pve/maps"),
            self._fetch_json("/pve/maps_en"),
        )

        raw_items = _required_mapping(item_document, "data.items")
        item_translations = _translation_mapping(item_translation_document, "items_en")
        raw_traders = _required_mapping(trader_document, "data")
        trader_translations = _translation_mapping(trader_translation_document, "traders_en")
        raw_tasks = _required_mapping(task_document, "data.tasks")
        task_translations = _translation_mapping(task_translation_document, "tasks_en")
        raw_maps = _required_mapping(map_document, "data.maps")
        map_translations = _translation_mapping(map_translation_document, "maps_en")

        items = tuple(item for item in raw_items.values() if isinstance(item, Mapping))
        tasks = tuple(task for task in raw_tasks.values() if isinstance(task, Mapping))
        if not items:
            raise InvalidTarkovResponseError("PvE items dataset was empty")
        if not tasks:
            raise InvalidTarkovResponseError("PvE tasks dataset was empty")

        item_names = _build_localized_names(raw_items, item_translations)
        task_names = _build_localized_names(raw_tasks, task_translations)

        return _Dataset(
            items=items,
            items_by_id={
                item_id: item
                for item_id, item in raw_items.items()
                if isinstance(item_id, str) and isinstance(item, Mapping)
            },
            item_translations=item_translations,
            item_names=item_names,
            trader_names=_build_trader_names(raw_traders, trader_translations),
            trader_level_requirements=_build_trader_level_requirements(raw_traders),
            item_task_names=_build_item_task_names(raw_tasks, task_translations),
            tasks=tasks,
            task_translations=task_translations,
            task_names=task_names,
            maps=_build_maps(raw_maps, map_translations),
        )

    async def _fetch_json(self, path: str) -> Mapping[str, Any]:
        session = self._get_session()
        url = f"{self.base_url}{path}"
        cached = self._document_cache.get(path)
        headers = {"Accept": "application/json", "User-Agent": "Duckies-Bot/0.1"}
        if cached is not None and cached.etag:
            headers["If-None-Match"] = cached.etag
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 304:
                    if cached is None:
                        raise InvalidTarkovResponseError(
                            f"Received 304 for {path} without a cached document"
                        )
                    return cached.document
                if response.status == 429:
                    raise TarkovRateLimitError(_parse_retry_after(response.headers.get("Retry-After")))
                if response.status >= 500:
                    raise TarkovUnavailableError(response.status)
                if response.status != 200:
                    raise InvalidTarkovResponseError(
                        f"Unexpected HTTP status {response.status} for {path}"
                    )
                try:
                    body = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError) as exc:
                    raise InvalidTarkovResponseError(
                        f"Response for {path} was not valid JSON"
                    ) from exc
        except asyncio.TimeoutError as exc:
            raise TarkovTimeoutError() from exc
        except aiohttp.ClientError as exc:
            raise TarkovUnavailableError() from exc

        if not isinstance(body, Mapping):
            raise InvalidTarkovResponseError(f"Response for {path} was not an object")
        self._document_cache[path] = _DocumentCache(
            document=body,
            etag=response.headers.get("ETag"),
        )
        return body

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            if not self._owns_session:
                raise RuntimeError("The injected HTTP session is closed")
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    @staticmethod
    def _select_best_item(
        items: Sequence[Any],
        query: str,
        translations: Mapping[str, str],
    ) -> Mapping[str, Any]:
        candidates = [item for item in items if isinstance(item, Mapping)]
        if not candidates:
            raise InvalidTarkovResponseError("No valid item objects were available")
        normalized_query = query.casefold().strip()

        def rank(item: Mapping[str, Any]) -> tuple[int, int, str]:
            name = _localized_text(item.get("name"), translations) or ""
            short_name = _localized_text(item.get("shortName"), translations) or ""
            folded_name = name.casefold()
            folded_short = short_name.casefold()
            if folded_name == normalized_query:
                match_rank = 0
            elif folded_short == normalized_query:
                match_rank = 1
            elif folded_name.startswith(normalized_query) or folded_short.startswith(normalized_query):
                match_rank = 2
            elif normalized_query in folded_name or normalized_query in folded_short:
                match_rank = 3
            else:
                match_rank = 4
            return match_rank, len(name), folded_name

        selected = min(candidates, key=rank)
        if rank(selected)[0] == 4:
            raise ItemNotFoundError(query)
        return selected

    @staticmethod
    def _select_best_task(
        tasks: Sequence[Any],
        query: str,
        translations: Mapping[str, str],
    ) -> Mapping[str, Any]:
        candidates = [task for task in tasks if isinstance(task, Mapping)]
        if not candidates:
            raise InvalidTarkovResponseError("No valid task objects were available")
        normalized_query = query.casefold().strip()

        def rank(task: Mapping[str, Any]) -> tuple[int, int, str]:
            name = _localized_text(task.get("name"), translations) or ""
            folded_name = name.casefold()
            normalized_name = str(task.get("normalizedName") or "").replace("-", " ").casefold()
            if folded_name == normalized_query or normalized_name == normalized_query:
                match_rank = 0
            elif folded_name.startswith(normalized_query) or normalized_name.startswith(normalized_query):
                match_rank = 1
            elif normalized_query in folded_name or normalized_query in normalized_name:
                match_rank = 2
            else:
                match_rank = 3
            return match_rank, len(name), folded_name

        selected = min(candidates, key=rank)
        if rank(selected)[0] == 3:
            raise TaskNotFoundError(query)
        return selected

    @staticmethod
    def _parse_item(
        raw: Mapping[str, Any],
        translations: Mapping[str, str],
        trader_names: Mapping[str, str],
        item_task_names: Mapping[str, tuple[str, ...]],
    ) -> TarkovItem:
        item_id = raw.get("id")
        name = _localized_text(raw.get("name"), translations)
        if not isinstance(item_id, str) or not name:
            raise InvalidTarkovResponseError("Item was missing a valid id or localized name")

        raw_offers = raw.get("sellToTrader")
        offers: list[VendorPrice] = []
        if raw_offers is not None:
            if not isinstance(raw_offers, list):
                raise InvalidTarkovResponseError("Item sellToTrader was not a list")
            for raw_offer in raw_offers:
                if not isinstance(raw_offer, Mapping):
                    continue
                trader_id = raw_offer.get("trader")
                if not isinstance(trader_id, str):
                    continue
                vendor_name = trader_names.get(trader_id, trader_id)
                offers.append(
                    VendorPrice(
                        vendor_name=vendor_name,
                        price_rub=_optional_int(raw_offer.get("priceRUB"), "priceRUB"),
                    )
                )

        return TarkovItem(
            id=item_id,
            name=name,
            short_name=_localized_text(raw.get("shortName"), translations),
            icon_url=_optional_str(raw.get("iconLink")),
            avg24h_price=_optional_int(raw.get("avg24hPrice"), "avg24hPrice"),
            low24h_price=_optional_int(raw.get("low24hPrice"), "low24hPrice"),
            high24h_price=_optional_int(raw.get("high24hPrice"), "high24hPrice"),
            last_low_price=_optional_int(raw.get("lastLowPrice"), "lastLowPrice"),
            sell_offers=tuple(offers),
            task_names=item_task_names.get(item_id, ()),
        )

    @staticmethod
    def _parse_task(raw: Mapping[str, Any], dataset: _Dataset) -> TarkovTask:
        task_id = raw.get("id")
        name = _localized_text(raw.get("name"), dataset.task_translations)
        if not isinstance(task_id, str) or not name:
            raise InvalidTarkovResponseError("Task was missing a valid id or localized name")

        objectives: list[TaskObjective] = []
        raw_objectives = raw.get("objectives")
        if not isinstance(raw_objectives, list):
            raise InvalidTarkovResponseError("Task objectives was not a list")
        for raw_objective in raw_objectives:
            if not isinstance(raw_objective, Mapping):
                continue
            objectives.append(_parse_task_objective(raw_objective, dataset))

        required_items = _aggregate_item_requirements(objectives)
        required_keys = tuple(
            dict.fromkeys(
                [
                    dataset.item_names.get(item_id, item_id)
                    for item_id in _collect_strings(raw.get("neededKeys"))
                ]
                + [
                    key
                    for objective in objectives
                    for key in objective.required_keys
                ]
            )
        )

        task_maps: list[TaskMap] = []
        primary_map_id = raw.get("map")
        if isinstance(primary_map_id, str) and primary_map_id in dataset.maps:
            task_maps.append(dataset.maps[primary_map_id])
        for objective in objectives:
            task_maps.extend(objective.maps)
        maps = tuple(dict.fromkeys(task_maps))

        prerequisite_names: list[str] = []
        for requirement in raw.get("taskRequirements") or []:
            if not isinstance(requirement, Mapping):
                continue
            required_task_id = requirement.get("task")
            if isinstance(required_task_id, str):
                prerequisite_names.append(dataset.task_names.get(required_task_id, required_task_id))

        trader_id = raw.get("trader")
        trader_name = (
            dataset.trader_names.get(trader_id, trader_id)
            if isinstance(trader_id, str)
            else "Unknown trader"
        )

        return TarkovTask(
            id=task_id,
            name=name,
            trader_name=trader_name,
            min_player_level=_optional_int(raw.get("minPlayerLevel"), "minPlayerLevel"),
            maps=maps,
            objectives=tuple(objectives),
            prerequisite_names=tuple(dict.fromkeys(prerequisite_names)),
            required_items=required_items,
            required_keys=required_keys,
            experience=_optional_int(raw.get("experience"), "experience") or 0,
            wiki_url=_optional_str(raw.get("wikiLink")),
            image_url=_optional_str(raw.get("taskImageLink")),
            kappa_required=bool(raw.get("kappaRequired")),
            lightkeeper_required=bool(raw.get("lightkeeperRequired")),
            faction_name=_optional_str(raw.get("factionName")),
            available_delay_min_seconds=_optional_int(
                raw.get("availableDelaySecondsMin"),
                "availableDelaySecondsMin",
            ) or 0,
            available_delay_max_seconds=_optional_int(
                raw.get("availableDelaySecondsMax"),
                "availableDelaySecondsMax",
            ) or 0,
            start_rewards=_parse_task_rewards(raw.get("startRewards"), dataset),
            finish_rewards=_parse_task_rewards(raw.get("finishRewards"), dataset),
            failure_rewards=_parse_task_rewards(raw.get("failureOutcome"), dataset),
        )

    def clear_dataset_cache(self) -> None:
        self._dataset_cache = None

    def clear_all_caches(self) -> None:
        self._dataset_cache = None
        self._document_cache.clear()

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()


def _required_mapping(document: Mapping[str, Any], dotted_path: str) -> Mapping[str, Any]:
    value: Any = document
    for component in dotted_path.split("."):
        value = value.get(component) if isinstance(value, Mapping) else None
    if not isinstance(value, Mapping):
        raise InvalidTarkovResponseError(f"Expected {dotted_path} to be an object")
    return value


def _required_list(document: Mapping[str, Any], dotted_path: str) -> list[Any]:
    value: Any = document
    for component in dotted_path.split("."):
        value = value.get(component) if isinstance(value, Mapping) else None
    if not isinstance(value, list):
        raise InvalidTarkovResponseError(f"Expected {dotted_path} to be a list")
    return value


def _translation_mapping(document: Mapping[str, Any], endpoint: str) -> Mapping[str, str]:
    raw = document.get("data")
    if not isinstance(raw, Mapping):
        raise InvalidTarkovResponseError(f"Expected {endpoint}.data to be an object")
    return {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)}


def _build_trader_names(
    traders: Mapping[str, Any],
    translations: Mapping[str, str],
) -> Mapping[str, str]:
    names: dict[str, str] = {}
    for trader_id, trader in traders.items():
        if not isinstance(trader_id, str) or not isinstance(trader, Mapping):
            continue
        name = _localized_text(trader.get("name"), translations)
        if name:
            names[trader_id] = name
    return names


def _build_trader_level_requirements(
    traders: Mapping[str, Any],
) -> Mapping[str, Mapping[int, int]]:
    requirements: dict[str, dict[int, int]] = {}
    for trader_id, trader in traders.items():
        if not isinstance(trader_id, str) or not isinstance(trader, Mapping):
            continue
        levels: dict[int, int] = {}
        for raw_level in trader.get("levels") or []:
            if not isinstance(raw_level, Mapping):
                continue
            level = _safe_optional_int(raw_level.get("level"))
            player_level = _safe_optional_int(raw_level.get("requiredPlayerLevel"))
            if level is not None and player_level is not None:
                levels[level] = player_level
        requirements[trader_id] = levels
    return requirements


def _build_localized_names(
    objects: Mapping[str, Any],
    translations: Mapping[str, str],
) -> Mapping[str, str]:
    names: dict[str, str] = {}
    for object_id, value in objects.items():
        if not isinstance(object_id, str) or not isinstance(value, Mapping):
            continue
        name = _localized_text(value.get("name"), translations)
        if name:
            names[object_id] = name
    return names


def _build_maps(
    maps: Mapping[str, Any],
    translations: Mapping[str, str],
) -> Mapping[str, TaskMap]:
    result: dict[str, TaskMap] = {}
    for map_id, value in maps.items():
        if not isinstance(map_id, str) or not isinstance(value, Mapping):
            continue
        name = _localized_text(value.get("name"), translations)
        normalized_name = value.get("normalizedName")
        if name and isinstance(normalized_name, str):
            result[map_id] = TaskMap(name=name, normalized_name=normalized_name)
    return result


def _parse_task_objective(raw: Mapping[str, Any], dataset: _Dataset) -> TaskObjective:
    objective_type = str(raw.get("type") or "objective")
    description = _localized_text(raw.get("description"), dataset.task_translations)
    if not description:
        description = objective_type.replace("Item", " item").replace("Quest", " quest")

    maps = tuple(
        dict.fromkeys(
            task_map
            for map_id in (raw.get("maps") or [])
            if isinstance(map_id, str)
            if (task_map := dataset.maps.get(map_id)) is not None
        )
    )
    count = _optional_number(raw.get("count"), "objective count") or 1
    found_in_raid = bool(raw.get("foundInRaid"))
    item_requirements: list[TaskItemRequirement] = []

    raw_items = raw.get("items")
    if isinstance(raw_items, list):
        names = tuple(
            dataset.item_names[item_id]
            for item_id in raw_items
            if isinstance(item_id, str) and item_id in dataset.item_names
        )
        if names:
            item_requirements.append(
                TaskItemRequirement(names, count, found_in_raid, description)
            )

    marker_item = raw.get("markerItem")
    if isinstance(marker_item, str) and marker_item in dataset.item_names:
        item_requirements.append(
            TaskItemRequirement(
                (dataset.item_names[marker_item],),
                count,
                False,
                description,
            )
        )

    if objective_type == "buildWeapon":
        component_ids = tuple(_collect_strings(raw.get("containsAll")))
        component_names = tuple(
            dataset.item_names[item_id]
            for item_id in component_ids
            if item_id in dataset.item_names
        )
        if component_names:
            item_requirements.append(
                TaskItemRequirement(component_names, 1, False, description)
            )

    required_keys = tuple(
        dict.fromkeys(
            dataset.item_names.get(item_id, item_id)
            for item_id in _collect_strings(raw.get("requiredKeys"))
        )
    )
    return TaskObjective(
        objective_type=objective_type,
        description=description,
        optional=bool(raw.get("optional")),
        maps=maps,
        item_requirements=tuple(item_requirements),
        required_keys=required_keys,
    )


def _aggregate_item_requirements(
    objectives: Sequence[TaskObjective],
) -> tuple[TaskItemRequirement, ...]:
    paired: dict[tuple[tuple[str, ...], bool], TaskItemRequirement] = {}
    other: list[TaskItemRequirement] = []
    for objective in objectives:
        for requirement in objective.item_requirements:
            if objective.objective_type not in {"findItem", "giveItem"}:
                other.append(requirement)
                continue
            key = (requirement.item_names, requirement.found_in_raid)
            existing = paired.get(key)
            if existing is None or requirement.count > existing.count:
                paired[key] = requirement
    return tuple(paired.values()) + tuple(other)


def _parse_task_rewards(raw: Any, dataset: _Dataset) -> TaskRewards:
    if not isinstance(raw, Mapping):
        return TaskRewards((), (), (), ())

    items: list[TaskRewardItem] = []
    for reward in raw.get("items") or []:
        if not isinstance(reward, Mapping):
            continue
        item_id = reward.get("item")
        if not isinstance(item_id, str):
            continue
        count = _optional_number(reward.get("count"), "reward item count") or 1
        items.append(TaskRewardItem(dataset.item_names.get(item_id, item_id), count))

    standing: list[str] = []
    for reward in raw.get("traderStanding") or []:
        if not isinstance(reward, Mapping):
            continue
        trader_id = reward.get("trader")
        value = _optional_number(reward.get("standing"), "trader standing")
        if isinstance(trader_id, str) and value is not None:
            standing.append(f"{dataset.trader_names.get(trader_id, trader_id)} {value:+g}")

    skills: list[str] = []
    for reward in raw.get("skillLevelReward") or []:
        if not isinstance(reward, Mapping):
            continue
        skill = reward.get("skill")
        level = _optional_number(reward.get("level"), "skill reward level")
        if isinstance(skill, str) and level is not None:
            skills.append(f"{skill} +{level:g}")

    unlocks: list[str] = []
    for field, label in (("offerUnlock", "Trader offer"), ("craftUnlock", "Craft")):
        for reward in raw.get(field) or []:
            if not isinstance(reward, Mapping):
                continue
            item_id = reward.get("item")
            if isinstance(item_id, str):
                unlocks.append(f"{label}: {dataset.item_names.get(item_id, item_id)}")
    for trader_id in _collect_strings(raw.get("traderUnlock")):
        unlocks.append(f"Trader: {dataset.trader_names.get(trader_id, trader_id)}")
    for map_id in _collect_strings(raw.get("locationUnlock")):
        task_map = dataset.maps.get(map_id)
        unlocks.append(f"Location: {task_map.name if task_map else map_id}")
    for reward in raw.get("customization") or []:
        if isinstance(reward, Mapping):
            name = _localized_text(reward.get("name"), dataset.task_translations)
            if name:
                unlocks.append(f"Customization: {name}")
    achievements = raw.get("achievement") or []
    if isinstance(achievements, list):
        unlocks.extend(f"Achievement: {value}" for value in _collect_strings(achievements))

    return TaskRewards(
        items=tuple(items),
        trader_standing=tuple(dict.fromkeys(standing)),
        skills=tuple(dict.fromkeys(skills)),
        unlocks=tuple(dict.fromkeys(unlocks)),
    )


_CURRENCY_ITEM_IDS = {
    "5449016a4bdc2d6f028b456f",  # Roubles
    "5696686a4bdc2da3298b456a",  # Dollars
    "569668774bdc2da2298b4568",  # Euros
}


def _parse_profit_recipe(
    raw: Mapping[str, Any],
    recipe_type: str,
    dataset: _Dataset,
    source_names: Mapping[str, str],
) -> ProfitRecipe | None:
    recipe_id = raw.get("id")
    result_raw = raw.get("productItem" if recipe_type == "craft" else "offeredItem")
    raw_ingredients = raw.get("requiredItems")
    source_id = raw.get("station" if recipe_type == "craft" else "trader")
    if (
        not isinstance(recipe_id, str)
        or not isinstance(result_raw, Mapping)
        or not isinstance(raw_ingredients, list)
        or not isinstance(source_id, str)
    ):
        return None

    result_id = result_raw.get("item")
    result_count = _optional_number(result_raw.get("count"), "recipe result count") or 1
    result_item = dataset.items_by_id.get(result_id) if isinstance(result_id, str) else None
    if not isinstance(result_id, str) or result_item is None:
        return None

    ingredients: list[RecipeIngredient] = []
    for ingredient_raw in raw_ingredients:
        if not isinstance(ingredient_raw, Mapping):
            continue
        item_id = ingredient_raw.get("item")
        item = dataset.items_by_id.get(item_id) if isinstance(item_id, str) else None
        if not isinstance(item_id, str) or item is None:
            continue
        count = _optional_number(ingredient_raw.get("count"), "recipe ingredient count") or 1
        attributes = ingredient_raw.get("attributes")
        reusable = bool(attributes.get("tool")) if isinstance(attributes, Mapping) else False
        ingredients.append(
            RecipeIngredient(
                item_id=item_id,
                item_name=dataset.item_names.get(item_id, item_id),
                count=count,
                reusable=reusable,
                snapshot_flea_price=_snapshot_flea_price(item),
                trader_price=_cheapest_trader_purchase(item, item_id),
            )
        )
    if len(ingredients) != len(raw_ingredients):
        return None

    task_unlock = raw.get("taskUnlock")
    duration = raw.get("duration") if recipe_type == "craft" else None
    buy_limit = raw.get("buyLimit") if recipe_type == "barter" else None
    return ProfitRecipe(
        id=recipe_id,
        recipe_type=recipe_type,
        result_item_id=result_id,
        result_item_name=dataset.item_names.get(result_id, result_id),
        result_count=result_count,
        result_snapshot_flea_price=_snapshot_flea_price(result_item),
        result_trader_price=_best_trader_sale(result_item),
        ingredients=tuple(ingredients),
        source_name=source_names.get(source_id, source_id),
        source_level=_optional_int(
            raw.get("level" if recipe_type == "craft" else "minTraderLevel"),
            "recipe source level",
        ),
        task_unlock_name=(
            dataset.task_names.get(task_unlock, task_unlock)
            if isinstance(task_unlock, str)
            else None
        ),
        duration_seconds=(
            _optional_int(duration, "craft duration") if duration is not None else None
        ),
        buy_limit=(
            _optional_int(buy_limit, "barter buy limit") if buy_limit is not None else None
        ),
    )


def _snapshot_flea_price(item: Mapping[str, Any]) -> int | None:
    value = item.get("lastLowPrice")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _cheapest_trader_purchase(item: Mapping[str, Any], item_id: str) -> int | None:
    prices = [
        offer.get("priceRUB")
        for offer in item.get("buyFromTrader") or []
        if isinstance(offer, Mapping)
        and isinstance(offer.get("priceRUB"), int)
        and offer.get("priceRUB") > 0
    ]
    if prices:
        return min(prices)
    if item_id in _CURRENCY_ITEM_IDS:
        base_price = item.get("basePrice")
        if isinstance(base_price, int) and base_price > 0:
            return base_price
    return None


def _best_trader_sale(item: Mapping[str, Any]) -> int | None:
    prices = [
        offer.get("priceRUB")
        for offer in item.get("sellToTrader") or []
        if isinstance(offer, Mapping)
        and isinstance(offer.get("priceRUB"), int)
        and offer.get("priceRUB") > 0
    ]
    return max(prices) if prices else None


def _build_item_task_names(
    tasks: Mapping[str, Any],
    translations: Mapping[str, str],
) -> Mapping[str, tuple[str, ...]]:
    index: dict[str, list[str]] = {}
    for task in tasks.values():
        if not isinstance(task, Mapping):
            continue
        task_name = _localized_text(task.get("name"), translations)
        objectives = task.get("objectives")
        if not task_name or not isinstance(objectives, list):
            continue
        referenced_items: set[str] = set()
        for objective in objectives:
            if not isinstance(objective, Mapping):
                continue
            for field in _ITEM_REFERENCE_FIELDS:
                referenced_items.update(_collect_strings(objective.get(field)))
        for item_id in referenced_items:
            task_list = index.setdefault(item_id, [])
            if task_name not in task_list:
                task_list.append(task_name)
    return {item_id: tuple(names) for item_id, names in index.items()}


def _collect_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _collect_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _collect_strings(nested)


def _localized_text(value: Any, translations: Mapping[str, str]) -> str | None:
    if not isinstance(value, str):
        return None
    return translations.get(value, value)


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidTarkovResponseError(f"{field_name} was not an integer or null")
    return value


def _safe_optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidTarkovResponseError(f"{field_name} was not numeric or null")
    return float(value)


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _parse_server_component(raw: Mapping[str, Any]) -> ServerComponentStatus:
    name = raw.get("name")
    status_code = raw.get("statusCode")
    status = raw.get("status")
    if not isinstance(name, str) or not isinstance(status_code, str):
        raise InvalidTarkovResponseError("Server status component was missing a name or code")
    if isinstance(status, bool) or not isinstance(status, int):
        raise InvalidTarkovResponseError("Server status numeric value was invalid")
    message = _optional_str(raw.get("message"))
    return ServerComponentStatus(name, message or None, status, status_code)


def _parse_server_message(raw: Mapping[str, Any]) -> ServerStatusMessage:
    content = raw.get("content")
    time_value = raw.get("time")
    message_type = raw.get("type")
    status_code = raw.get("statusCode")
    if not isinstance(content, str) or not isinstance(time_value, str):
        raise InvalidTarkovResponseError("Server status message was missing content or time")
    if isinstance(message_type, bool) or not isinstance(message_type, int):
        raise InvalidTarkovResponseError("Server status message type was invalid")
    if not isinstance(status_code, str):
        raise InvalidTarkovResponseError("Server status message was missing a status code")
    return ServerStatusMessage(
        content=content,
        time=time_value,
        message_type=message_type,
        solve_time=_optional_str(raw.get("solveTime")),
        status_code=status_code,
    )


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
