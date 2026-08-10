"""Tests for OCR task matching and quest-log aggregation."""

import unittest

from duckies_bot.features.tarkov.models import (
    OCRLine,
    TarkovTask,
    TaskItemRequirement,
    TaskMap,
    TaskObjective,
    TaskRewards,
)
from duckies_bot.features.tarkov.quest_log import (
    QuestLogService,
    normalize_task_text,
    task_name_similarity,
)
from duckies_bot.providers.tarkov.errors import QuestLogNotRecognizedError


EMPTY_REWARDS = TaskRewards((), (), (), ())
CUSTOMS = TaskMap("Customs", "customs")


def task(
    task_id: str,
    name: str,
    item_count: float = 0,
    key: str | None = None,
    task_map: TaskMap | None = CUSTOMS,
) -> TarkovTask:
    requirement = (
        TaskItemRequirement(("Salewa",), item_count, True, "Find Salewas")
        if item_count
        else None
    )
    objective = TaskObjective(
        "findItem",
        "Find Salewas",
        False,
        (task_map,) if task_map else (),
        (requirement,) if requirement else (),
        (key,) if key else (),
    )
    return TarkovTask(
        task_id,
        name,
        "Therapist",
        1,
        (task_map,) if task_map else (),
        (objective,),
        (),
        (requirement,) if requirement else (),
        (key,) if key else (),
        100,
        None,
        None,
        False,
        False,
        "Any",
        0,
        0,
        EMPTY_REWARDS,
        EMPTY_REWARDS,
        EMPTY_REWARDS,
    )


class FakeOCR:
    def __init__(self, batches: list[tuple[OCRLine, ...]]) -> None:
        self.batches = batches
        self.calls = 0

    async def extract_lines(self, image: bytes) -> tuple[OCRLine, ...]:
        result = self.batches[self.calls]
        self.calls += 1
        return result


class FakeCatalog:
    def __init__(self, tasks: tuple[TarkovTask, ...]) -> None:
        self.tasks = tasks

    async def list_tasks(self) -> tuple[TarkovTask, ...]:
        return self.tasks


class QuestLogServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_normalization_and_typo_similarity(self) -> None:
        self.assertEqual(normalize_task_text("  Chemical – Part 1 "), "chemical part 1")
        self.assertGreater(task_name_similarity("Private Clink", "Private Clinic"), 0.85)

    async def test_matches_deduplicates_and_aggregates_requirements(self) -> None:
        catalog = FakeCatalog(
            (
                task("one", "Private Clinic", 1, "USEC stash key"),
                task("two", "Shortage", 2, "USEC stash key"),
            )
        )
        ocr = FakeOCR(
            [
                (OCRLine("Private Clink", 0.96), OCRLine("Shortage", 0.99)),
                (OCRLine("Private Clinic", 0.98),),
            ]
        )
        summary = await QuestLogService(catalog, ocr).analyze((b"one", b"two"))
        self.assertEqual(tuple(match.task.name for match in summary.matches), ("Private Clinic", "Shortage"))
        self.assertEqual(summary.item_requirements[0].count, 3)
        self.assertEqual(summary.item_requirements[0].task_names, ("Private Clinic", "Shortage"))
        self.assertEqual(summary.required_keys[0].task_names, ("Private Clinic", "Shortage"))
        self.assertEqual(summary.map_groups[0].task_names, ("Private Clinic", "Shortage"))

    async def test_ambiguous_title_is_not_silently_selected(self) -> None:
        catalog = FakeCatalog(
            (task("one", "Chemical - Part 1"), task("two", "Chemical - Part 2"))
        )
        ocr = FakeOCR([(OCRLine("Chemical Part", 0.99),)])
        summary = await QuestLogService(catalog, ocr).analyze((b"image",))
        self.assertEqual(summary.matches, ())
        self.assertEqual(summary.unmatched_lines, ("Chemical Part",))

    async def test_no_recognized_names_has_specific_error(self) -> None:
        catalog = FakeCatalog((task("one", "Private Clinic"),))
        ocr = FakeOCR([(OCRLine("Tasks", 0.99),)])
        with self.assertRaises(QuestLogNotRecognizedError):
            await QuestLogService(catalog, ocr).analyze((b"image",))

    async def test_api_missing_task_is_returned_as_unmatched_text(self) -> None:
        catalog = FakeCatalog((task("one", "Private Clinic"),))
        ocr = FakeOCR([(OCRLine("Brand New 1.1 Mission", 0.99),)])
        summary = await QuestLogService(catalog, ocr).analyze((b"image",))
        self.assertEqual(summary.matches, ())
        self.assertEqual(summary.unmatched_lines, ("Brand New 1.1 Mission",))
