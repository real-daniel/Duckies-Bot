"""Tests for normalized Tarkov item models."""

import unittest

from duckies_bot.features.tarkov.models import TarkovItem, VendorPrice


def make_item(*offers: VendorPrice) -> TarkovItem:
    return TarkovItem("id", "Item", "Short", None, None, None, None, None, offers, ())


class TarkovItemTests(unittest.TestCase):
    def test_best_trader_is_highest_non_flea_offer(self) -> None:
        item = make_item(
            VendorPrice("Therapist", 120_000),
            VendorPrice("Flea Market", 200_000),
            VendorPrice("Mechanic", 125_000),
        )
        self.assertEqual(item.best_trader_offer, VendorPrice("Mechanic", 125_000))

    def test_missing_prices_are_ignored(self) -> None:
        item = make_item(VendorPrice("Therapist", None), VendorPrice("Mechanic", 125_000))
        self.assertEqual(item.best_trader_offer, VendorPrice("Mechanic", 125_000))

    def test_no_trader_offer_returns_none(self) -> None:
        self.assertIsNone(make_item(VendorPrice("FLEA MARKET", 200_000)).best_trader_offer)
        self.assertIsNone(make_item().best_trader_offer)

    def test_tied_prices_keep_first_offer(self) -> None:
        item = make_item(VendorPrice("Prapor", 100), VendorPrice("Mechanic", 100))
        self.assertEqual(item.best_trader_offer, VendorPrice("Prapor", 100))
