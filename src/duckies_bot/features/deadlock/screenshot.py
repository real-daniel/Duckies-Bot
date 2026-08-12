"""Extract a Deadlock match ID from the bottom-right of a screenshot."""

from __future__ import annotations

import asyncio
import re
from io import BytesIO
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from ..tarkov.models import OCRLine
from ...providers.deadlock.errors import (
    InvalidDeadlockScreenshotError,
    MatchIDNotRecognizedError,
)
from ...providers.tarkov.errors import OCRUnavailableError


class OCRProvider(Protocol):
    async def extract_lines(self, image: bytes) -> tuple[OCRLine, ...]: ...


class DeadlockScreenshotReader:
    def __init__(self, ocr_provider: OCRProvider) -> None:
        self.ocr_provider = ocr_provider

    async def extract_match_id(self, image: bytes) -> int:
        cropped = await asyncio.to_thread(_prepare_bottom_right, image)
        try:
            lines = await self.ocr_provider.extract_lines(cropped)
        except OCRUnavailableError as exc:
            raise InvalidDeadlockScreenshotError(
                "Screenshot reading is temporarily unavailable. Enter the match ID manually."
            ) from exc
        match_id = _find_match_id(lines)
        if match_id is None:
            raise MatchIDNotRecognizedError()
        return match_id


def _prepare_bottom_right(image: bytes) -> bytes:
    try:
        with Image.open(BytesIO(image)) as source:
            source.load()
            width, height = source.size
            if width < 200 or height < 120:
                raise InvalidDeadlockScreenshotError(
                    "That image is too small. Upload a full-resolution game screenshot."
                )
            quadrant = source.crop((width // 2, height // 2, width, height))
            grayscale = ImageOps.grayscale(quadrant)
            enhanced = ImageOps.autocontrast(grayscale)
            enlarged = enhanced.resize(
                (enhanced.width * 3, enhanced.height * 3),
                Image.Resampling.LANCZOS,
            )
            output = BytesIO()
            enlarged.save(output, format="PNG")
            return output.getvalue()
    except InvalidDeadlockScreenshotError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidDeadlockScreenshotError() from exc


def _find_match_id(lines: tuple[OCRLine, ...]) -> int | None:
    candidates: list[tuple[int, float, int]] = []
    for line in lines:
        text = line.text.strip()
        lowered = text.casefold()
        labelled = "match" in lowered or "match id" in lowered

        # A labelled line can tolerate common OCR substitutions and spaces
        # between digits, e.g. "Match ID: 98 855 986".
        if labelled:
            suffix = re.split(r"match\s*(?:id)?", text, maxsplit=1, flags=re.IGNORECASE)[-1]
            normalized = suffix.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"}))
            digits = "".join(re.findall(r"\d", normalized))
            if 7 <= len(digits) <= 12:
                candidates.append((2, line.confidence, int(digits)))

        for match in re.finditer(r"(?<!\d)(\d{7,12})(?!\d)", text):
            digits = match.group(1)
            likely_length = 1 if len(digits) in {8, 9} else 0
            candidates.append((likely_length, line.confidence, int(digits)))

    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]
