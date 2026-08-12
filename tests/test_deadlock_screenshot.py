"""Tests for bottom-right Deadlock match-ID screenshot extraction."""

from io import BytesIO
import unittest

from PIL import Image

from duckies_bot.features.deadlock.screenshot import DeadlockScreenshotReader
from duckies_bot.features.tarkov.models import OCRLine
from duckies_bot.providers.deadlock.errors import (
    InvalidDeadlockScreenshotError,
    MatchIDNotRecognizedError,
)


class FakeOCR:
    def __init__(self, lines: tuple[OCRLine, ...]) -> None:
        self.lines = lines
        self.received: bytes | None = None

    async def extract_lines(self, image: bytes) -> tuple[OCRLine, ...]:
        self.received = image
        return self.lines


def screenshot_bytes(width: int = 400, height: int = 240) -> bytes:
    image = Image.new("RGB", (width, height), "black")
    for x in range(width // 2, width):
        for y in range(height // 2, height):
            image.putpixel((x, y), (255, 255, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class DeadlockScreenshotReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_crops_bottom_right_enlarges_and_reads_labelled_id(self) -> None:
        ocr = FakeOCR((OCRLine("Match ID: 98 855 986", 0.91),))
        reader = DeadlockScreenshotReader(ocr)

        match_id = await reader.extract_match_id(screenshot_bytes())

        self.assertEqual(match_id, 98855986)
        assert ocr.received is not None
        with Image.open(BytesIO(ocr.received)) as cropped:
            self.assertEqual(cropped.size, (600, 360))
            self.assertEqual(cropped.mode, "L")

    async def test_corrects_common_digit_substitutions_on_labelled_line(self) -> None:
        reader = DeadlockScreenshotReader(
            FakeOCR((OCRLine("MATCH ID 98855O86", 0.82),))
        )
        self.assertEqual(await reader.extract_match_id(screenshot_bytes()), 98855086)

    async def test_accepts_plain_eight_digit_candidate(self) -> None:
        reader = DeadlockScreenshotReader(FakeOCR((OCRLine("98855986", 0.75),)))
        self.assertEqual(await reader.extract_match_id(screenshot_bytes()), 98855986)

    async def test_rejects_image_without_match_id(self) -> None:
        reader = DeadlockScreenshotReader(FakeOCR((OCRLine("FPS 144", 0.99),)))
        with self.assertRaises(MatchIDNotRecognizedError):
            await reader.extract_match_id(screenshot_bytes())

    async def test_rejects_tiny_or_invalid_image(self) -> None:
        reader = DeadlockScreenshotReader(FakeOCR(()))
        with self.assertRaises(InvalidDeadlockScreenshotError):
            await reader.extract_match_id(screenshot_bytes(100, 100))
        with self.assertRaises(InvalidDeadlockScreenshotError):
            await reader.extract_match_id(b"not an image")
