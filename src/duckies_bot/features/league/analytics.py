"""Conversational parsing and statistics for sampled League item analysis."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from statistics import median

from .models import LeagueChampion, LeagueItem, LeagueItemStats


TIERS = (
    "IRON",
    "BRONZE",
    "SILVER",
    "GOLD",
    "PLATINUM",
    "EMERALD",
    "DIAMOND",
    "MASTER",
    "GRANDMASTER",
    "CHALLENGER",
)
_ROMAN_DIVISIONS = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_TIER_ALIASES = {
    "plat": 4,
    "emer": 5,
    "dia": 6,
    "masters": 7,
    "gm": 8,
    "chall": 9,
}


@dataclass(frozen=True, slots=True)
class LeagueRankBucket:
    tier: str
    division: str


@dataclass(frozen=True, slots=True)
class LeagueItemQuestion:
    item: LeagueItem
    buckets: tuple[LeagueRankBucket, ...]
    rank_label: str
    champion: LeagueChampion | None = None
    item_position: int | None = None
    comparison_item: LeagueItem | None = None


@dataclass(frozen=True, slots=True)
class LeagueRankFilter:
    buckets: tuple[LeagueRankBucket, ...]
    rank_label: str


def parse_league_item_question(
    question: str,
    items: tuple[LeagueItem, ...],
    champions: tuple[LeagueChampion, ...] = (),
) -> LeagueItemQuestion:
    question = question.strip()
    if len(question) < 3:
        raise ValueError(
            "Ask a question such as: How good is Infinity Edge in Gold to Platinum?"
        )
    if len(question) > 300:
        raise ValueError("Keep the question under 300 characters.")
    primary_text, comparison_text = _comparison_parts(question)
    item, item_span = _resolve_item(primary_text, items)
    comparison_item = (
        _resolve_item(comparison_text, items)[0] if comparison_text is not None else None
    )
    champion = resolve_champion(question, champions) if champions else None
    item_position = _item_position(question)
    if item_position is not None and champion is None and champions:
        raise ValueError("Include a champion for an item-slot question, such as: on Smolder.")
    if comparison_item is not None and champion is None and champions:
        raise ValueError("Include a champion for an item comparison, such as: on Smolder.")
    rank_filter = parse_rank_filter(question, ignored_span=item_span)
    return LeagueItemQuestion(
        item,
        rank_filter.buckets,
        rank_filter.rank_label,
        champion,
        item_position,
        comparison_item,
    )


def parse_rank_filter(
    question: str,
    *,
    ignored_span: tuple[int, int] = (-1, -1),
    and_above: bool = False,
) -> LeagueRankFilter:
    """Resolve ranked ladder buckets independently of the analyzed subject."""
    ranks = _rank_mentions(question, ignored_span)
    if not ranks:
        return LeagueRankFilter(
            (LeagueRankBucket("EMERALD", "II"),),
            "Emerald (default)",
        )

    first = ranks[0]
    last = ranks[-1]
    if first[1] > last[1]:
        first, last = last, first
    if and_above:
        last = (last[0], min(first[1] + 1, len(TIERS) - 1), None)
    if last[1] - first[1] > 1:
        raise ValueError("Keep League rank ranges to one or two adjacent tiers.")

    buckets: list[LeagueRankBucket] = []
    if first[1] == last[1] and first[2] != last[2]:
        tier = TIERS[first[1]]
        divisions = (first[2], last[2])
        buckets.extend(
            LeagueRankBucket(tier, _division_for_api(tier, division))
            for division in dict.fromkeys(divisions)
        )
    else:
        for tier_index in range(first[1], last[1] + 1):
            division = None
            if tier_index == first[1]:
                division = first[2]
            if tier_index == last[1] and last[2] is not None:
                division = last[2]
            tier = TIERS[tier_index]
            buckets.append(LeagueRankBucket(tier, _division_for_api(tier, division)))

    first_label = _rank_label(TIERS[first[1]], first[2])
    last_label = _rank_label(TIERS[last[1]], last[2])
    rank_label = first_label if first_label == last_label else f"{first_label}–{last_label}"
    if and_above:
        rank_label = f"{first_label}+ (two-tier sample)"
    return LeagueRankFilter(tuple(buckets), rank_label)


def aggregate_item_stats(
    item_id: int,
    observations: tuple[tuple[bool, tuple[int, ...]], ...],
) -> LeagueItemStats:
    wins = sum(won for won, item_ids in observations if item_id in item_ids)
    matches = sum(item_id in item_ids for _, item_ids in observations)
    return LeagueItemStats(item_id, wins, matches - wins)


def aggregate_item_position_stats(
    item_id: int,
    position: int,
    observations: tuple[tuple[bool, tuple[int, ...]], ...],
) -> LeagueItemStats:
    selected = [
        won
        for won, item_ids in observations
        if len(item_ids) >= position and item_ids[position - 1] == item_id
    ]
    wins = sum(selected)
    return LeagueItemStats(item_id, wins, len(selected) - wins)


def _comparison_parts(question: str) -> tuple[str, str | None]:
    match = re.search(
        r"\b(?:compar[a-z]*\s+(?:to|2|against)|versus|vs\.?|"
        r"v(?!\s+(?:to|through|thru)\b)|instead\s+of|against|"
        r"(?:better|worse)\s+than)\b",
        question,
        re.I,
    )
    if match is None:
        return question, None
    left, right = question[: match.start()].strip(), question[match.end() :].strip()
    if not left or not right:
        raise ValueError("Name an item on both sides of the comparison.")
    return left, right


def _item_position(question: str) -> int | None:
    words = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}
    match = re.search(
        r"\b(1st|2nd|3rd|4th|5th|6th|first|second|third|fourth|fifth|sixth)"
        r"\s+(?:completed\s+|core\s+)?item\b",
        question,
        re.I,
    )
    if match is None:
        match = re.search(r"\b(?:item\s*([1-6])|([1-6])\s*item)\b", question, re.I)
        if match:
            return int(match.group(1) or match.group(2))
        for token in re.findall(r"[a-z0-9]+", question.casefold()):
            ordinal = re.fullmatch(r"([1-6])(?:st|nd|rd|th)", token)
            if ordinal:
                return int(ordinal.group(1))
            scored = sorted(
                (SequenceMatcher(None, token, name).ratio(), position)
                for name, position in words.items()
            )
            if scored[-1][0] >= 0.80:
                return scored[-1][1]
        return None
    value = match.group(1).casefold()
    return words.get(value, int(value[0]) if value[0].isdigit() else 0) or None


def resolve_champion(
    question: str, champions: tuple[LeagueChampion, ...]
) -> LeagueChampion | None:
    normalized = _normalize(question)
    exact = [
        champion
        for champion in champions
        if re.search(rf"\b{re.escape(_normalize(champion.name))}\b", normalized)
        or re.search(rf"\b{re.escape(_normalize(champion.key))}\b", normalized)
    ]
    if exact:
        return max(exact, key=lambda champion: len(champion.name))
    words = normalized.split()
    scored: list[tuple[float, LeagueChampion]] = []
    for champion in champions:
        name = _normalize(champion.name)
        count = len(name.split())
        for index in range(max(0, len(words) - count + 1)):
            phrase = " ".join(words[index : index + count])
            scored.append((SequenceMatcher(None, phrase, name).ratio(), champion))
    scored.sort(key=lambda value: value[0], reverse=True)
    if not scored or scored[0][0] < 0.82:
        return None
    runner_up = next((value for value in scored[1:] if value[1] != scored[0][1]), None)
    if runner_up is not None and scored[0][0] - runner_up[0] < 0.08:
        return None
    return scored[0][1]


def comparable_win_rate(
    target: LeagueItemStats,
    item: LeagueItem,
    all_stats: tuple[LeagueItemStats, ...],
    catalog: dict[int, LeagueItem],
) -> tuple[float | None, int]:
    peers = [
        row
        for row in all_stats
        if row.item_id != target.item_id
        and row.matches >= 5
        and row.win_rate is not None
        and (peer := catalog.get(row.item_id)) is not None
        and abs(peer.total_cost - item.total_cost) <= 750
        and bool(set(peer.tags) & set(item.tags))
    ]
    rates = [row.win_rate for row in peers if row.win_rate is not None]
    return (median(rates), len(rates)) if rates else (None, 0)


def wilson_interval(wins: int, matches: int) -> tuple[float, float]:
    if matches <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    proportion = wins / matches
    denominator = 1 + z * z / matches
    centre = proportion + z * z / (2 * matches)
    margin = z * math.sqrt(
        proportion * (1 - proportion) / matches + z * z / (4 * matches * matches)
    )
    return (centre - margin) / denominator, (centre + margin) / denominator


def item_verdict(stats: LeagueItemStats, peer_win_rate: float | None) -> str:
    if stats.matches < 10:
        return "Too little sampled data"
    if stats.win_rate is None or peer_win_rate is None:
        return "Limited comparison data"
    delta = stats.win_rate - peer_win_rate
    if stats.matches < 30:
        return "Promising, but low confidence" if delta >= 0.04 else "Unclear with a small sample"
    if delta >= 0.03:
        return "Strong in this sample"
    if delta >= 0.01:
        return "Above the sampled baseline"
    if delta <= -0.03:
        return "Weak in this sample"
    if delta <= -0.01:
        return "Below the sampled baseline"
    return "About average in this sample"


def _resolve_item(
    question: str,
    items: tuple[LeagueItem, ...],
) -> tuple[LeagueItem, tuple[int, int]]:
    words = list(re.finditer(r"[a-z0-9]+", question.casefold()))
    candidates: list[tuple[float, int, LeagueItem, tuple[int, int]]] = []
    token_counts: dict[str, int] = {}
    for catalog_item in items:
        for token in set(_normalize(catalog_item.name).split()):
            if len(token) >= 5:
                token_counts[token] = token_counts.get(token, 0) + 1
    for item in items:
        item_name = _normalize(item.name)
        compact_name = item_name.replace(" ", "")
        word_count = len(item_name.split())
        if word_count == 0:
            continue
        if word_count <= len(words):
            for index in range(len(words) - word_count + 1):
                window = words[index : index + word_count]
                phrase = " ".join(match.group(0) for match in window)
                candidates.append(
                    (
                        SequenceMatcher(None, phrase, item_name).ratio(),
                        len(item_name),
                        item,
                        (window[0].start(), window[-1].end()),
                    )
                )
        if word_count > 1:
            for word in words:
                score = SequenceMatcher(None, word.group(0), compact_name).ratio()
                if score >= 0.80:
                    candidates.append(
                        (score, len(compact_name), item, (word.start(), word.end()))
                    )
        initials = "".join(word[0] for word in item_name.split() if word)
        if len(initials) >= 2:
            for word in words:
                if (
                    word.group(0) == initials
                    and word.group(0) not in _TIER_ALIASES
                    and word.group(0) not in {tier.casefold() for tier in TIERS}
                ):
                    candidates.append(
                        (1.0, len(item_name), item, (word.start(), word.end()))
                    )
        for token in item_name.split():
            if len(token) >= 5 and token_counts.get(token) == 1:
                for word in words:
                    score = SequenceMatcher(None, word.group(0), token).ratio()
                    if score >= 0.84:
                        candidates.append(
                            (score, len(token), item, (word.start(), word.end()))
                        )
    if not candidates:
        raise ValueError("I could not find a current League item in that question.")
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]), reverse=True)
    best = candidates[0]
    if best[0] < 0.80:
        raise ValueError("I could not find a current League item in that question.")
    runner_up = next(
        (candidate for candidate in candidates[1:] if candidate[2].item_id != best[2].item_id),
        None,
    )
    ambiguous_acronym = (
        runner_up is not None
        and best[0] == runner_up[0] == 1.0
        and best[3] == runner_up[3]
    )
    if runner_up is not None and (
        ambiguous_acronym or (best[0] < 1.0 and best[0] - runner_up[0] < 0.06)
    ):
        raise ValueError(
            f"That item name is ambiguous. Did you mean {best[2].name} or {runner_up[2].name}?"
        )
    return best[2], best[3]


def _rank_mentions(
    question: str,
    item_span: tuple[int, int],
) -> list[tuple[int, int, int | None]]:
    lowered = question.casefold()
    results: list[tuple[int, int, int | None]] = []
    for tier_index, tier in enumerate(TIERS):
        pattern = rf"\b{tier.casefold()}(?:\s*(iii|ii|iv|i|[1-4]))?\b"
        for match in re.finditer(pattern, lowered):
            if item_span[0] <= match.start() < item_span[1]:
                continue
            results.append((match.start(), tier_index, _parse_division(match.group(1))))

    occupied = {position for position, _, _ in results}
    for alias, tier_index in _TIER_ALIASES.items():
        pattern = rf"\b{alias}(?:\s*(iii|ii|iv|i|[1-4]))?\b"
        for match in re.finditer(pattern, lowered):
            if item_span[0] <= match.start() < item_span[1] or match.start() in occupied:
                continue
            results.append((match.start(), tier_index, _parse_division(match.group(1))))

    exact_positions = {position for position, _, _ in results}
    for token in re.finditer(r"[a-z0-9]+", lowered):
        if len(token.group(0)) < 4:
            continue
        if item_span[0] <= token.start() < item_span[1] or token.start() in exact_positions:
            continue
        scored = sorted(
            (
                (SequenceMatcher(None, token.group(0), tier.casefold()).ratio(), index)
                for index, tier in enumerate(TIERS)
            ),
            reverse=True,
        )
        if scored[0][0] >= 0.78 and scored[0][0] - scored[1][0] >= 0.08:
            results.append((token.start(), scored[0][1], None))
    return sorted(results)


def _parse_division(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else _ROMAN_DIVISIONS.get(value)


def _division_for_api(tier: str, division: int | None) -> str:
    if tier in {"MASTER", "GRANDMASTER", "CHALLENGER"}:
        return "I"
    value = division or 2
    return next(roman.upper() for roman, number in _ROMAN_DIVISIONS.items() if number == value)


def _rank_label(tier: str, division: int | None) -> str:
    name = tier.title()
    if division is None or tier in {"MASTER", "GRANDMASTER", "CHALLENGER"}:
        return name
    roman = next(key.upper() for key, value in _ROMAN_DIVISIONS.items() if value == division)
    return f"{name} {roman}"


def _normalize(value: str) -> str:
    value = value.casefold().replace("'", "").replace("’", "")
    return " ".join(re.findall(r"[a-z0-9]+", value))
