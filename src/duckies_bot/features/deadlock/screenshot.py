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
    candidates: list[tuple[int, int, float, int]] = []
    for line in lines:
        text = line.text.strip()
        lowered = text.casefold()
        labelled = "match" in lowered or "match id" in lowered
        normalized = text.translate(
            str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"})
        )

        # A labelled line can tolerate common OCR substitutions and spaces
        # between digits, e.g. "Match ID: 98 855 986".
        if labelled:
            suffix = re.split(
                r"match\s*(?:id)?",
                text,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[-1]
            suffix = suffix.translate(
                str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"})
            )
            digits = "".join(re.findall(r"\d", suffix))
            if 7 <= len(digits) <= 12:
                candidates.append((2, len(digits) == 9, line.confidence, int(digits)))

        # RapidOCR may separate a longer ID into groups ("100 123 456") or
        # put it on a different line from the Match ID label. Match eight- and
        # nine-digit candidates with common separators in either case.
        for match in re.finditer(
            r"(?<!\d)(\d(?:[\s,._'-]*\d){8}|\d(?:[\s,._'-]*\d){7})"
            r"(?![\s,._'-]*\d)",
            normalized,
        ):
            digits = "".join(re.findall(r"\d", match.group(1)))
            candidates.append((1 if labelled else 0, len(digits) == 9, line.confidence, int(digits)))

    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1], item[2]))[3]
