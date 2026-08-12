"""Tests for craft/barter profit calculations and robust pricing."""

import unittest

from duckies_bot.features.tarkov.models import (
    PriceHistoryPoint,
    ProfitRecipe,
    RecipeIngredient,
    TraderFlipCandidate,
)
from duckies_bot.features.tarkov.profit import ProfitService, robust_floor_price


def history(*prices: int) -> tuple[PriceHistoryPoint, ...]:
    return tuple(
        PriceHistoryPoint(price, 100, index * 1000)
        for index, price in enumerate(prices, 1)
    )


CRAFT = ProfitRecipe(
    id="craft",
    recipe_type="craft",
    result_item_id="result",
    result_item_name="Crafted item",
    result_count=2,
    result_snapshot_flea_price=90,
    result_trader_price=80,
    ingredients=(
        RecipeIngredient("material", "Material", 2, False, 60, None),
        RecipeIngredient("tool", "Tool", 1, True, 30, None),
    ),
    source_name="Workbench",
    source_level=2,
    task_unlock_name=None,
    duration_seconds=3600,
    buy_limit=None,
)

BITCOIN_FARM = ProfitRecipe(
    id="bitcoin",
    recipe_type="craft",
    result_item_id="result",
    result_item_name="Physical Bitcoin",
    result_count=1,
    result_snapshot_flea_price=900_000,
    result_trader_price=800_000,
    ingredients=(),
    source_name="Bitcoin Farm",
    source_level=1,
    task_unlock_name=None,
    duration_seconds=1000,
    buy_limit=None,
)


class FakeProfitProvider:
    def __init__(self, crafts: tuple[ProfitRecipe, ...] = (CRAFT,)) -> None:
        self.craft_calls = 0
        self.crafts = crafts
        self.histories = {
            "result": history(100, 102, 101, 999_999),
            "material": history(49, 50, 51, 50),
            "tool": history(29, 30, 31, 30),
        }
        self.flip_calls = 0
        self.flips = (
            TraderFlipCandidate(
                "result", "Flip item", "Mechanic", 50, 90, 3, 30, None, 2, 100,
            ),
        )

    async def list_crafts(self) -> tuple[ProfitRecipe, ...]:
        self.craft_calls += 1
        return self.crafts

    async def list_barters(self) -> tuple[ProfitRecipe, ...]:
        return ()

    async def get_price_history(self, item_id: str) -> tuple[PriceHistoryPoint, ...]:
        return self.histories.get(item_id, ())

    async def list_trader_flips(self) -> tuple[TraderFlipCandidate, ...]:
        self.flip_calls += 1
        return self.flips


class ProfitTests(unittest.IsolatedAsyncioTestCase):
    def test_recent_median_rejects_extreme_floor(self) -> None:
        price = robust_floor_price(history(100, 102, 101, 999_999))
        self.assertIsNotNone(price)
        self.assertEqual(price.value, 101)

    async def test_craft_excludes_reusable_tool_and_calculates_hourly_profit(self) -> None:
        provider = FakeProfitProvider()
        service = ProfitService(provider)
        result = (await service.top_crafts(1))[0]
        self.assertEqual(result.material_cost, 100)
        self.assertEqual(result.tool_capital, 30)
        self.assertEqual(result.result_value, 202)
        self.assertEqual(result.gross_profit, 102)
        self.assertEqual(result.profit_per_hour, 102)
        self.assertEqual(result.result_price_source, "Recent flea floor")

    async def test_rankings_are_cached(self) -> None:
        provider = FakeProfitProvider()
        service = ProfitService(provider)
        await service.top_crafts(1)
        await service.top_crafts(1)
        self.assertEqual(provider.craft_calls, 1)

    async def test_bitcoin_farm_is_excluded_from_craft_rankings(self) -> None:
        provider = FakeProfitProvider((BITCOIN_FARM, CRAFT))
        results = await ProfitService(provider).top_crafts(2)
        self.assertEqual(tuple(result.recipe_id for result in results), ("craft",))

    async def test_trader_flips_use_robust_floor_and_cache_results(self) -> None:
        provider = FakeProfitProvider()
        service = ProfitService(provider)
        result = (await service.top_flips(1))[0]
        self.assertEqual(result.flea_price, 101)
        self.assertEqual(result.gross_profit_each, 51)
        self.assertAlmostEqual(result.roi_percent, 102.0)
        await service.top_flips(1)
        self.assertEqual(provider.flip_calls, 1)

    async def test_trader_flips_require_validated_price_history(self) -> None:
        provider = FakeProfitProvider()
        provider.histories["result"] = ()
        self.assertEqual(await ProfitService(provider).top_flips(1), ())

    async def test_trader_flips_exclude_task_locks_unless_requested(self) -> None:
        provider = FakeProfitProvider()
        provider.flips = (
            TraderFlipCandidate(
                "result", "Flip item", "Mechanic", 50, 90, 3, 30,
                "Required Task", 2, 100,
            ),
        )
        service = ProfitService(provider)
        self.assertEqual(await service.top_flips(1), ())
        self.assertEqual(
            len(await service.top_flips(1, include_task_locked=True)),
            1,
        )

    async def test_pmc_level_uses_cheapest_accessible_trader_offer(self) -> None:
        provider = FakeProfitProvider()
        provider.flips = (
            TraderFlipCandidate(
                "result", "Flip item", "Mechanic", 40, 90, 4, 40, None, 2, 100,
            ),
            TraderFlipCandidate(
                "result", "Flip item", "Prapor", 60, 90, 2, 10, None, 2, 100,
            ),
        )
        result = (await ProfitService(provider).top_flips(1, pmc_level=10))[0]
        self.assertEqual(result.trader_name, "Prapor")
        self.assertEqual(result.required_player_level, 10)

    async def test_high_liquidity_filter_removes_thin_items(self) -> None:
        provider = FakeProfitProvider()
        provider.histories["result"] = tuple(
            PriceHistoryPoint(price, 6, index * 1000)
            for index, price in enumerate((100, 102, 101), 1)
        )
        service = ProfitService(provider)
        self.assertEqual(len(await service.top_flips(1)), 1)
        self.assertEqual(await service.top_flips(1, high_liquidity_only=True), ())
