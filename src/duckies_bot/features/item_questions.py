"""Shared structured interpretation for conversational item questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class InterpretedItemQuestion:
    game: str
    intent: str
    primary_item: str | None
    comparison_item: str | None
    character: str | None
    item_position: int | None
    minimum_rank: str | None
    maximum_rank: str | None
    needs_clarification: bool
    clarification: str | None
    confidence: str


class ItemQuestionInterpreter(Protocol):
    async def interpret(
        self, question: str, *, game: str
    ) -> InterpretedItemQuestion: ...


class QuestionInterpreterUnavailableError(RuntimeError):
    """Raised when the optional interpretation provider cannot be reached."""


def canonical_item_question(
    interpreted: InterpretedItemQuestion,
    *,
    expected_game: str,
) -> str:
    """Turn validated structured meaning into the deterministic parser's grammar."""
    if interpreted.needs_clarification:
        raise ValueError(
            interpreted.clarification
            or "I could not confidently interpret that question. Please rephrase it."
        )
    if interpreted.game not in {expected_game, "unknown"}:
        raise ValueError(f"That looks like a {interpreted.game.title()} question.")
    if interpreted.intent not in {"analyze_item", "compare_items"}:
        raise ValueError("Ask about an item or compare two items.")
    if not interpreted.primary_item:
        raise ValueError("I could not identify the main item in that question.")
    if interpreted.item_position is not None and not 1 <= interpreted.item_position <= 6:
        raise ValueError("Item position must be between first and sixth.")
    if expected_game == "deadlock" and interpreted.item_position is not None:
        raise ValueError("Deadlock item analysis is hero-specific and does not use item order.")

    parts = [interpreted.primary_item]
    if interpreted.item_position is not None:
        parts.append(f"{interpreted.item_position} item")
    if interpreted.character:
        parts.append(f"on {interpreted.character}")
    if interpreted.comparison_item:
        parts.append(f"compared to {interpreted.comparison_item}")
    if interpreted.minimum_rank:
        parts.append(interpreted.minimum_rank)
    if (
        interpreted.maximum_rank
        and interpreted.maximum_rank.casefold()
        != (interpreted.minimum_rank or "").casefold()
    ):
        parts.append(f"to {interpreted.maximum_rank}")
    return " ".join(parts)
