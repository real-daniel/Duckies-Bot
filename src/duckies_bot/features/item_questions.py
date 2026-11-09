"""Structured meaning shared by conversational game-statistics questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class InterpretedAnalyticsQuestion:
    """Provider-neutral query plan produced from one Discord question."""

    game: str
    intent: str
    subject_type: str
    primary_subject: str | None
    comparison_subject: str | None
    character_context: str | None
    item_position: int | None
    minimum_rank: str | None
    maximum_rank: str | None
    rank_scope: str
    metrics: tuple[str, ...]
    match_mode: str
    role: str
    lane_scope: str
    result_limit: int
    sort_by: str
    sort_direction: str
    needs_clarification: bool
    clarification: str | None
    confidence: str


class AnalyticsQuestionInterpreter(Protocol):
    async def interpret(
        self, question: str, *, game: str
    ) -> InterpretedAnalyticsQuestion: ...


class QuestionInterpreterUnavailableError(RuntimeError):
    """Raised when the optional interpretation provider cannot be reached."""


class QuestionClarificationError(ValueError):
    """Raised when a user can continue an ask conversation with missing detail."""


def validate_analytics_question(
    interpreted: InterpretedAnalyticsQuestion,
    *,
    expected_game: str,
) -> InterpretedAnalyticsQuestion:
    """Validate the model's query plan before it can influence API calls."""
    if interpreted.needs_clarification:
        raise QuestionClarificationError(
            interpreted.clarification
            or "I could not confidently interpret that question. Please rephrase it."
        )
    if interpreted.game not in {expected_game, "unknown"}:
        raise ValueError(f"That looks like a {interpreted.game.title()} question.")
    if interpreted.intent not in {
        "analyze", "compare", "rank", "recommend", "matchup", "synergy"
    }:
        raise ValueError(
            "Ask me to analyze, compare, rank, or recommend champions, heroes, or items."
        )
    if interpreted.subject_type not in {"item", "character"}:
        raise ValueError("Ask about a champion, hero, or item that has recorded statistics.")
    if not interpreted.primary_subject and interpreted.intent not in {"rank", "recommend"}:
        raise ValueError("I could not identify what you want analyzed.")
    if interpreted.intent == "compare" and not interpreted.comparison_subject:
        raise ValueError("Name both subjects you want compared.")
    if interpreted.item_position is not None and not 1 <= interpreted.item_position <= 6:
        raise ValueError("Item position must be between first and sixth.")
    if interpreted.subject_type == "character" and interpreted.item_position is not None:
        raise ValueError("Build position only applies to item questions.")
    if expected_game == "deadlock" and interpreted.item_position is not None:
        raise ValueError("Deadlock item analysis is hero-specific and does not use item order.")
    if interpreted.rank_scope not in {"all", "exact", "range", "and_above"}:
        raise ValueError("I could not understand the requested rank range.")
    if interpreted.match_mode not in {"ranked", "unranked", "all"}:
        raise ValueError("Match mode must be ranked, unranked, or all matches.")
    if interpreted.role not in {"any", "top", "jungle", "mid", "bottom", "support"}:
        raise ValueError("I could not understand the requested League role.")
    if interpreted.lane_scope not in {"any", "same_lane"}:
        raise ValueError("I could not understand the requested matchup scope.")
    if not 1 <= interpreted.result_limit <= 10:
        raise ValueError("Ask for between one and ten results.")
    if interpreted.sort_by not in {"win_rate", "pick_rate", "popularity", "matches"}:
        raise ValueError("I could not understand how to rank those results.")
    if interpreted.sort_direction not in {"high", "low"}:
        raise ValueError("I could not understand the requested ranking direction.")
    if interpreted.intent == "recommend" and interpreted.subject_type == "item" \
            and not interpreted.character_context:
        raise ValueError("Include the champion or hero you want item recommendations for.")
    return interpreted


def canonical_item_question(interpreted: InterpretedAnalyticsQuestion) -> str:
    """Translate an item query plan into the existing catalog-aware parser grammar."""
    if interpreted.subject_type != "item":
        raise ValueError("This question is not about an item.")
    parts = [interpreted.primary_subject or ""]
    if interpreted.item_position is not None:
        parts.append(f"{interpreted.item_position} item")
    if interpreted.character_context:
        parts.append(f"on {interpreted.character_context}")
    if interpreted.comparison_subject:
        parts.append(f"compared to {interpreted.comparison_subject}")
    parts.extend(_rank_parts(interpreted))
    return " ".join(part for part in parts if part)


def canonical_rank_question(interpreted: InterpretedAnalyticsQuestion) -> str:
    """Build rank-only text for the deterministic rank catalog parser."""
    return " ".join(_rank_parts(interpreted))


def _rank_parts(interpreted: InterpretedAnalyticsQuestion) -> list[str]:
    if not interpreted.minimum_rank:
        return []
    if interpreted.rank_scope == "range" and interpreted.maximum_rank:
        return [interpreted.minimum_rank, "to", interpreted.maximum_rank]
    return [interpreted.minimum_rank]


# Temporary compatibility names for callers outside the package.
InterpretedItemQuestion = InterpretedAnalyticsQuestion
ItemQuestionInterpreter = AnalyticsQuestionInterpreter
