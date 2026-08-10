"""Quest-log OCR matching and raid-preparation aggregation."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Protocol

from ...providers.tarkov.errors import QuestLogNotRecognizedError
from .models import (
    AggregatedTaskItem,
    AggregatedTaskKey,
    OCRLine,
    QuestLogMapGroup,
    QuestLogMatch,
    QuestLogSummary,
    TarkovTask,
    TaskMap,
)


class OCRProvider(Protocol):
    async def extract_lines(self, image: bytes) -> tuple[OCRLine, ...]: ...


class TaskCatalogProvider(Protocol):
    async def list_tasks(self) -> tuple[TarkovTask, ...]: ...


_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_IGNORED_LINES = {
    "active",
    "character inventory",
    "completed",
    "daily",
    "operational tasks",
    "task",
    "tasks",
    "trader",
}


def normalize_task_text(value: str) -> str:
    return " ".join(_NON_ALPHANUMERIC.sub(" ", value.casefold()).split())


def task_name_similarity(source: str, task_name: str) -> float:
    source_normalized = normalize_task_text(source)
    task_normalized = normalize_task_text(task_name)
    if not source_normalized or not task_normalized:
        return 0.0
    if task_normalized in source_normalized:
        return 1.0

    sequence_score = SequenceMatcher(None, source_normalized, task_normalized).ratio()
    source_tokens = set(source_normalized.split())
    task_tokens = set(task_normalized.split())
    token_score = (
        2 * len(source_tokens & task_tokens) / (len(source_tokens) + len(task_tokens))
        if source_tokens and task_tokens
        else 0.0
    )
    return max(sequence_score, token_score)


class QuestLogService:
    def __init__(
        self,
        task_provider: TaskCatalogProvider,
        ocr_provider: OCRProvider,
        match_threshold: float = 0.72,
        ambiguity_margin: float = 0.04,
    ) -> None:
        self._task_provider = task_provider
        self._ocr_provider = ocr_provider
        self._match_threshold = match_threshold
        self._ambiguity_margin = ambiguity_margin

    async def analyze(self, images: Sequence[bytes]) -> QuestLogSummary:
        extracted = await asyncio_gather_lines(self._ocr_provider, images)
        tasks = await self._task_provider.list_tasks()
        matches, unmatched = self._match_lines(extracted, tasks)
        if not matches and not unmatched:
            raise QuestLogNotRecognizedError()
        return _aggregate(matches, unmatched)

    def _match_lines(
        self,
        lines: Sequence[OCRLine],
        tasks: Sequence[TarkovTask],
    ) -> tuple[tuple[QuestLogMatch, ...], tuple[str, ...]]:
        # Some API tasks have faction variants with identical titles. One title is
        # sufficient for a screenshot match and avoids false ambiguity.
        unique_tasks = {task.name.casefold(): task for task in tasks}
        matched_by_task: dict[str, QuestLogMatch] = {}
        unmatched: list[str] = []

        for line in lines:
            normalized_line = normalize_task_text(line.text)
            if len(normalized_line) < 4 or normalized_line in _IGNORED_LINES:
                continue
            ranked = sorted(
                (
                    (task_name_similarity(line.text, task.name), task)
                    for task in unique_tasks.values()
                ),
                key=lambda result: result[0],
                reverse=True,
            )
            if not ranked:
                continue
            best_score, best_task = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else 0.0
            confidence = best_score * (0.75 + 0.25 * max(0.0, min(line.confidence, 1.0)))
            unambiguous = best_score == 1.0 or best_score - second_score >= self._ambiguity_margin
            if confidence >= self._match_threshold and unambiguous:
                match = QuestLogMatch(best_task, line.text, confidence)
                previous = matched_by_task.get(best_task.id)
                if previous is None or match.confidence > previous.confidence:
                    matched_by_task[best_task.id] = match
            elif (
                (best_score >= 0.48 or line.confidence >= 0.75)
                and len(line.text) <= 100
                and line.text not in unmatched
            ):
                unmatched.append(line.text)

        matches = tuple(sorted(matched_by_task.values(), key=lambda match: match.task.name.casefold()))
        return matches, tuple(unmatched[:12])


async def asyncio_gather_lines(
    provider: OCRProvider,
    images: Sequence[bytes],
) -> tuple[OCRLine, ...]:
    import asyncio

    batches = await asyncio.gather(*(provider.extract_lines(image) for image in images))
    seen: set[str] = set()
    lines: list[OCRLine] = []
    for batch in batches:
        for line in batch:
            key = normalize_task_text(line.text)
            if key and key not in seen:
                seen.add(key)
                lines.append(line)
    return tuple(lines)


def _aggregate(
    matches: Sequence[QuestLogMatch],
    unmatched: tuple[str, ...],
) -> QuestLogSummary:
    item_totals: dict[tuple[tuple[str, ...], bool], float] = defaultdict(float)
    item_tasks: dict[tuple[tuple[str, ...], bool], list[str]] = defaultdict(list)
    key_tasks: dict[str, list[str]] = defaultdict(list)
    map_tasks: dict[TaskMap, list[str]] = defaultdict(list)
    tasks_without_map: list[str] = []

    for match in matches:
        task = match.task
        for requirement in task.required_items:
            key = (requirement.item_names, requirement.found_in_raid)
            item_totals[key] += requirement.count
            if task.name not in item_tasks[key]:
                item_tasks[key].append(task.name)
        for key_name in task.required_keys:
            if task.name not in key_tasks[key_name]:
                key_tasks[key_name].append(task.name)
        if task.maps:
            for task_map in task.maps:
                if task.name not in map_tasks[task_map]:
                    map_tasks[task_map].append(task.name)
        else:
            tasks_without_map.append(task.name)

    items = tuple(
        AggregatedTaskItem(names, item_totals[(names, fir)], fir, tuple(item_tasks[(names, fir)]))
        for names, fir in sorted(item_totals, key=lambda key: (key[0], key[1]))
    )
    keys = tuple(
        AggregatedTaskKey(key_name, tuple(task_names))
        for key_name, task_names in sorted(key_tasks.items())
    )
    groups = tuple(
        QuestLogMapGroup(task_map, tuple(task_names))
        for task_map, task_names in sorted(map_tasks.items(), key=lambda entry: entry[0].name)
    )
    return QuestLogSummary(
        matches=tuple(matches),
        item_requirements=items,
        required_keys=keys,
        map_groups=groups,
        tasks_without_map=tuple(dict.fromkeys(tasks_without_map)),
        unmatched_lines=unmatched,
    )
