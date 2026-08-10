"""Tests for the lazy RapidOCR adapter without loading real models."""

import unittest

from duckies_bot.providers.ocr import RapidOCRProvider


class FakeResult:
    txts = (" Private   Clinic ", "low confidence", "")
    scores = (0.98, 0.1, 0.99)


class FakeEngine:
    def __call__(self, image: bytes) -> FakeResult:
        return FakeResult()


class RapidOCRProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_and_filters_lines(self) -> None:
        provider = RapidOCRProvider(minimum_line_confidence=0.3)
        provider._engine = FakeEngine()
        lines = await provider.extract_lines(b"image")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].text, "Private Clinic")
        self.assertEqual(lines[0].confidence, 0.98)
