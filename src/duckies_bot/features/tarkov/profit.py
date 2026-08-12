"""Craft and barter gross-profit ranking with outlier-resistant pricing."""

from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from .models import (
    PriceHistoryPoint,
    PricedIngredient,
    ProfitRecipe,
    ProfitResult,
    TraderFlipCandidate,
    TraderFlipResult,
)


_EXCLUDED_CRAFT_SOURCES = {"bitcoin farm"}


class ProfitProvider(Protocol):
    async def list_crafts(self) -> tuple[ProfitRecipe, ...]: ...

    async def list_barters(self) -> tuple[ProfitRecipe, ...]: ...

    async def get_price_history(self, item_id: str) -> tuple[PriceHistoryPoint, ...]: ...

    async def list_trader_flips(self) -> tuple[TraderFlipCandidate, ...]: ...


@dataclass(frozen=True, slots=True)
class _RobustPrice:
    value: int
    low_liquidity: bool


@dataclass(frozen=True, slots=True)
class _RankingCacheEntry:
    results: tuple[ProfitResult, ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class _FlipCacheEntry:
    results: tuple[TraderFlipResult, ...]
    expires_at: float


class ProfitService:
    def __init__(
        self,
        provider: ProfitProvider,
        cache_ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[str, str], _RankingCacheEntry] = {}
        self._flip_cache: dict[tuple[bool, int | None, bool], _FlipCacheEntry] = {}

    async def top_crafts(self, top: int, sort_by: str = "profit") -> tuple[ProfitResult, ...]:
        return await self._rank("craft", top, sort_by)

    async def top_barters(self, top: int) -> tuple[ProfitResult, ...]:
        return await self._rank("barter", top, "profit")

    async def top_flips(
        self,
        top: int,
        include_task_locked: bool = False,
        pmc_level: int | None = None,
        high_liquidity_only: bool = False,
    ) -> tuple[TraderFlipResult, ...]:
        if not 1 <= top <= 20:
            raise ValueError("top must be between 1 and 20")
        if pmc_level is not None and not 1 <= pmc_level <= 100:
            raise ValueError("pmc_level must be between 1 and 100")
        now = self._clock()
        cache_key = (include_task_locked, pmc_level, high_liquidity_only)
        cache = self._flip_cache.get(cache_key)
        if (
            cache is not None
            and cache.expires_at > now
            and len(cache.results) >= top
        ):
            return cache.results[:top]

        candidates = await self._provider.list_trader_flips()
        cheapest_by_item: dict[str, TraderFlipCandidate] = {}
        for candidate in candidates:
            if not include_task_locked and candidate.task_unlock_name:
                continue
            if (
                pmc_level is not None
                and candidate.required_player_level is not None
                and candidate.required_player_level > pmc_level
            ):
                continue
            existing = cheapest_by_item.get(candidate.item_id)
            if existing is None or candidate.trader_price < existing.trader_price:
                cheapest_by_item[candidate.item_id] = candidate

        preliminary = sorted(
            (
                candidate
                for candidate in cheapest_by_item.values()
                if candidate.snapshot_flea_price > candidate.trader_price
            ),
            key=lambda candidate: candidate.snapshot_flea_price - candidate.trader_price,
            reverse=True,
        )
        candidate_pool_size = max(200, top * 20) if high_liquidity_only else max(100, top * 10)
        candidate_limit = min(len(preliminary), candidate_pool_size)
        finalists = preliminary[:candidate_limit]
        robust_prices = await self._load_robust_prices(
            {candidate.item_id for candidate in finalists}
        )
        results: list[TraderFlipResult] = []
        for candidate in finalists:
            robust = robust_prices.get(candidate.item_id)
            if robust is None:
                continue
            if high_liquidity_only and robust.low_liquidity:
                continue
            flea_price = robust.value
            gross_profit = flea_price - candidate.trader_price
            if gross_profit <= 0:
                continue
            results.append(
                TraderFlipResult(
                    item_name=candidate.item_name,
                    trader_name=candidate.trader_name,
                    trader_price=candidate.trader_price,
                    flea_price=flea_price,
                    price_source="Recent flea floor",
                    gross_profit_each=gross_profit,
                    roi_percent=gross_profit / candidate.trader_price * 100,
                    min_trader_level=candidate.min_trader_level,
                    required_player_level=candidate.required_player_level,
                    task_unlock_name=candidate.task_unlock_name,
                    buy_limit=candidate.buy_limit,
                    restock_amount=candidate.restock_amount,
                    low_liquidity=robust.low_liquidity,
                )
            )
        results.sort(key=lambda result: result.gross_profit_each, reverse=True)
        ranked = tuple(results)
        self._flip_cache[cache_key] = _FlipCacheEntry(
            ranked,
            self._clock() + self._cache_ttl_seconds,
        )
        return ranked[:top]

    async def _rank(
        self,
        recipe_type: str,
        top: int,
        sort_by: str,
    ) -> tuple[ProfitResult, ...]:
        if not 1 <= top <= 20:
            raise ValueError("top must be between 1 and 20")
        if sort_by not in {"profit", "hourly"}:
            raise ValueError("sort_by must be profit or hourly")

        cache_key = (recipe_type, sort_by)
        cached = self._cache.get(cache_key)
        now = self._clock()
        if cached is not None and cached.expires_at > now and len(cached.results) >= top:
            return cached.results[:top]

        recipes = (
            await self._provider.list_crafts()
            if recipe_type == "craft"
            else await self._provider.list_barters()
        )
        if recipe_type == "craft":
            recipes = tuple(
                recipe
                for recipe in recipes
                if recipe.source_name.casefold() not in _EXCLUDED_CRAFT_SOURCES
            )
        preliminary = [
            result
            for recipe in recipes
            if (result := _price_recipe(recipe, {})) is not None
        ]
        preliminary.sort(key=lambda result: _sort_value(result, sort_by), reverse=True)
        candidate_limit = min(len(preliminary), max(30, top * 5))
        candidate_ids = {result.recipe_id for result in preliminary[:candidate_limit]}
        candidates = [recipe for recipe in recipes if recipe.id in candidate_ids]

        item_ids: set[str] = set()
        for recipe in candidates:
            if recipe.result_snapshot_flea_price is not None:
                item_ids.add(recipe.result_item_id)
            item_ids.update(
                ingredient.item_id
                for ingredient in recipe.ingredients
                if ingredient.snapshot_flea_price is not None
            )
        robust_prices = await self._load_robust_prices(item_ids)
        results = [
            result
            for recipe in candidates
            if (result := _price_recipe(recipe, robust_prices)) is not None
        ]
        results.sort(key=lambda result: _sort_value(result, sort_by), reverse=True)
        ranked = tuple(results)
        self._cache[cache_key] = _RankingCacheEntry(
            ranked,
            self._clock() + self._cache_ttl_seconds,
        )
        return ranked[:top]

    async def _load_robust_prices(
        self,
        item_ids: set[str],
    ) -> dict[str, _RobustPrice]:
        semaphore = asyncio.Semaphore(8)

        async def load(item_id: str) -> tuple[str, _RobustPrice | None]:
            async with semaphore:
                try:
                    history = await self._provider.get_price_history(item_id)
                except Exception:
                    return item_id, None
            return item_id, robust_floor_price(history)

        pairs = await asyncio.gather(*(load(item_id) for item_id in item_ids))
        return {item_id: price for item_id, price in pairs if price is not None}


def robust_floor_price(points: Sequence[PriceHistoryPoint]) -> _RobustPrice | None:
    usable = [point for point in points if point.minimum_price > 0]
    if not usable:
        return None
    usable.sort(key=lambda point: point.timestamp_ms)
    recent = [point for point in usable[-12:] if point.offer_count is None or point.offer_count >= 5]
    if len(recent) < 3:
        return None

    values = [point.minimum_price for point in recent]
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations)
    if mad:
        filtered = [value for value in values if abs(value - median) <= 3 * mad]
        if len(filtered) >= 3:
            values = filtered
    price = int(round(statistics.median(values)))
    offer_counts = [point.offer_count for point in recent if point.offer_count is not None]
    low_liquidity = bool(offer_counts and statistics.median(offer_counts) < 10)
    return _RobustPrice(price, low_liquidity)


def _price_recipe(
    recipe: ProfitRecipe,
    robust_prices: dict[str, _RobustPrice],
) -> ProfitResult | None:
    warnings: list[str] = []
    priced_ingredients: list[PricedIngredient] = []
    material_cost = 0
    tool_capital = 0

    for ingredient in recipe.ingredients:
        robust = robust_prices.get(ingredient.item_id)
        flea_price = robust.value if robust else ingredient.snapshot_flea_price
        options = [price for price in (flea_price, ingredient.trader_price) if price is not None]
        if not options:
            return None
        unit_price = min(options)
        if ingredient.trader_price is not None and unit_price == ingredient.trader_price:
            warnings.append("Assumes access to cheapest trader offers")
        if robust is None and ingredient.snapshot_flea_price is not None:
            warnings.append("Some inputs use latest-low fallback")
        if robust is not None and robust.low_liquidity:
            warnings.append("Some inputs have low flea liquidity")
        total = round(unit_price * ingredient.count)
        priced_ingredients.append(
            PricedIngredient(
                ingredient.item_name,
                ingredient.count,
                unit_price,
                total,
                ingredient.reusable,
            )
        )
        if ingredient.reusable:
            tool_capital += total
        else:
            material_cost += total

    result_robust = robust_prices.get(recipe.result_item_id)
    result_flea_price = (
        result_robust.value if result_robust else recipe.result_snapshot_flea_price
    )
    sale_options = [
        (result_flea_price, "Recent flea floor" if result_robust else "Latest flea low"),
        (recipe.result_trader_price, "Best trader"),
    ]
    valid_sales = [(price, source) for price, source in sale_options if price is not None]
    if not valid_sales:
        return None
    result_unit_price, result_source = max(valid_sales, key=lambda option: option[0])
    if result_robust is None and recipe.result_snapshot_flea_price is not None:
        warnings.append("Result uses latest-low fallback")
    if result_robust is not None and result_robust.low_liquidity:
        warnings.append("Result has low flea liquidity")
    if recipe.task_unlock_name:
        warnings.append("Task locked")

    result_value = round(result_unit_price * recipe.result_count)
    gross_profit = result_value - material_cost
    roi = gross_profit / material_cost * 100 if material_cost else None
    profit_per_hour = None
    if recipe.duration_seconds and recipe.duration_seconds > 0:
        profit_per_hour = round(gross_profit / (recipe.duration_seconds / 3600))

    return ProfitResult(
        recipe_id=recipe.id,
        recipe_type=recipe.recipe_type,
        result_item_name=recipe.result_item_name,
        result_count=recipe.result_count,
        result_unit_price=result_unit_price,
        result_value=result_value,
        result_price_source=result_source,
        ingredients=tuple(priced_ingredients),
        material_cost=material_cost,
        tool_capital=tool_capital,
        gross_profit=gross_profit,
        roi_percent=roi,
        profit_per_hour=profit_per_hour,
        source_name=recipe.source_name,
        source_level=recipe.source_level,
        task_unlock_name=recipe.task_unlock_name,
        duration_seconds=recipe.duration_seconds,
        buy_limit=recipe.buy_limit,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _sort_value(result: ProfitResult, sort_by: str) -> int:
    if sort_by == "hourly":
        return result.profit_per_hour if result.profit_per_hour is not None else -10**18
    return result.gross_profit
