"""Tests for optional LLM-backed item-question interpretation."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from duckies_bot.features.item_questions import (
    InterpretedItemQuestion,
    QuestionInterpreterUnavailableError,
    canonical_item_question,
)
from duckies_bot.providers.question_interpreter import OpenAIQuestionInterpreter


class FakeResponses:
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload = payload or {
            "game": "league",
            "intent": "compare",
            "subject_type": "item",
            "primary_subject": "Infinity Edge",
            "comparison_subject": "Spear of Shojin",
            "character_context": "Smolder",
            "item_position": 2,
            "minimum_rank": None,
            "maximum_rank": None,
            "rank_scope": "all",
            "metrics": ["win_rate", "sample_size"],
            "match_mode": "ranked",
            "needs_clarification": False,
            "clarification": None,
            "confidence": "high",
        }
        self.calls: list[dict[str, object]] = []
        self.error: Exception | None = None

    async def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        schema = kwargs["text_format"]
        return SimpleNamespace(output_parsed=schema(**self.payload))


class FakeOpenAIClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class OpenAIQuestionInterpreterTests(unittest.IsolatedAsyncioTestCase):
    async def test_interprets_with_luna_structured_output_and_caches(self) -> None:
        responses = FakeResponses()
        client = FakeOpenAIClient(responses)
        interpreter = OpenAIQuestionInterpreter(
            "secret", client=client  # type: ignore[arg-type]
        )

        first = await interpreter.interpret(
            "is IE 2nd on smolder better than shojin", game="league"
        )
        second = await interpreter.interpret(
            " is  IE  2nd on SMOLDER better than shojin ", game="league"
        )

        self.assertEqual(first, second)
        self.assertEqual(first.primary_subject, "Infinity Edge")
        self.assertEqual(first.comparison_subject, "Spear of Shojin")
        self.assertEqual(len(responses.calls), 1)
        call = responses.calls[0]
        self.assertEqual(call["model"], "gpt-6-luna")
        self.assertEqual(call["reasoning"], {"effort": "none"})
        self.assertIs(call["store"], False)
        sent_input = str(call["input"]).casefold()
        self.assertNotIn("user_id", sent_input)
        self.assertNotIn("author", sent_input)

    async def test_low_confidence_requires_clarification(self) -> None:
        responses = FakeResponses()
        responses.payload["confidence"] = "low"
        interpreter = OpenAIQuestionInterpreter(
            "secret", client=FakeOpenAIClient(responses)  # type: ignore[arg-type]
        )

        result = await interpreter.interpret("ie or something", game="league")

        self.assertTrue(result.needs_clarification)
        self.assertIsNotNone(result.clarification)

    async def test_character_question_does_not_require_an_item(self) -> None:
        responses = FakeResponses({
            "game": "deadlock",
            "intent": "analyze",
            "subject_type": "character",
            "primary_subject": "Haze",
            "comparison_subject": None,
            "character_context": None,
            "item_position": None,
            "minimum_rank": "Oracle",
            "maximum_rank": None,
            "rank_scope": "and_above",
            "metrics": ["win_rate", "pick_rate", "sample_size"],
            "match_mode": "ranked",
            "needs_clarification": False,
            "clarification": None,
            "confidence": "high",
        })
        interpreter = OpenAIQuestionInterpreter(
            "secret", client=FakeOpenAIClient(responses)  # type: ignore[arg-type]
        )

        result = await interpreter.interpret("hze any gud oracle+", game="deadlock")

        self.assertEqual(result.subject_type, "character")
        self.assertEqual(result.primary_subject, "Haze")
        self.assertEqual(result.rank_scope, "and_above")
        self.assertIn("pick_rate", result.metrics)
        prompt = str(responses.calls[0]["input"])
        self.assertIn("Do not require an item", prompt)

    async def test_provider_failure_is_distinct_from_bad_user_input(self) -> None:
        responses = FakeResponses()
        responses.error = OSError("offline")
        interpreter = OpenAIQuestionInterpreter(
            "secret", client=FakeOpenAIClient(responses)  # type: ignore[arg-type]
        )

        with self.assertRaises(QuestionInterpreterUnavailableError):
            await interpreter.interpret("is ie good", game="league")

    def test_canonicalizes_luna_meaning_for_existing_validated_parsers(self) -> None:
        interpreted = InterpretedItemQuestion(
            game="league",
            intent="compare",
            subject_type="item",
            primary_subject="Infinity Edge",
            comparison_subject="Spear of Shojin",
            character_context="Smolder",
            item_position=2,
            minimum_rank="Gold",
            maximum_rank="Platinum",
            rank_scope="range",
            metrics=("win_rate",),
            match_mode="ranked",
            needs_clarification=False,
            clarification=None,
            confidence="high",
        )

        question = canonical_item_question(interpreted)

        self.assertEqual(
            question,
            "Infinity Edge 2 item on Smolder compared to Spear of Shojin Gold to Platinum",
        )

    def test_rejects_deadlock_item_order_from_model(self) -> None:
        interpreted = InterpretedItemQuestion(
            game="deadlock",
            intent="analyze",
            subject_type="item",
            primary_subject="Glass Cannon",
            comparison_subject=None,
            character_context="Haze",
            item_position=2,
            minimum_rank=None,
            maximum_rank=None,
            rank_scope="all",
            metrics=("win_rate",),
            match_mode="ranked",
            needs_clarification=False,
            clarification=None,
            confidence="medium",
        )

        with self.assertRaisesRegex(ValueError, "does not use item order"):
            from duckies_bot.features.item_questions import validate_analytics_question
            validate_analytics_question(interpreted, expected_game="deadlock")
