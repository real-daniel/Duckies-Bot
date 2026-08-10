"""Lazy, locally hosted OCR using RapidOCR and ONNX Runtime."""

from __future__ import annotations

import asyncio
from typing import Any

from ...features.tarkov.models import OCRLine
from ..tarkov.errors import OCRUnavailableError


class RapidOCRProvider:
    """Extract ordered text lines without sending screenshots off-device."""

    def __init__(self, minimum_line_confidence: float = 0.30) -> None:
        self.minimum_line_confidence = minimum_line_confidence
        self._engine: Any = None
        self._lock = asyncio.Lock()

    async def extract_lines(self, image: bytes) -> tuple[OCRLine, ...]:
        async with self._lock:
            try:
                return await asyncio.to_thread(self._extract_lines_sync, image)
            except OCRUnavailableError:
                raise
            except Exception as exc:
                raise OCRUnavailableError(f"RapidOCR failed: {exc}") from exc

    def _extract_lines_sync(self, image: bytes) -> tuple[OCRLine, ...]:
        if self._engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError as exc:
                raise OCRUnavailableError(
                    "Install the rapidocr and onnxruntime packages."
                ) from exc
            self._engine = RapidOCR()

        result = self._engine(image)
        texts = getattr(result, "txts", ()) or ()
        scores = getattr(result, "scores", ()) or ()
        lines: list[OCRLine] = []
        for text, score in zip(texts, scores, strict=False):
            normalized = " ".join(str(text).split())
            confidence = float(score)
            if normalized and confidence >= self.minimum_line_confidence:
                lines.append(OCRLine(normalized, confidence))
        return tuple(lines)
