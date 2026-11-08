"""Natural-language parsing and statistical helpers for Deadlock item questions."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from statistics import median

from .models import HeroSummary, ItemStats, ItemSummary, RankAsset


_ROMAN_SUBRANKS = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}


@dataclass(frozen=True, slots=True)
class ItemQuestion:
    item: ItemSummary
    comparison_item: ItemSummary | None
    hero: HeroSummary | None
    minimum_badge: int | None
    maximum_badge: int | None
    rank_label: str


@dataclass(frozen=True, slots=True)
class DeadlockRankFilter:
    minimum_badge: int | None
    maximum_badge: int | None
    rank_label: str


def parse_item_question(
    question: str,
    items: tuple[ItemSummary, ...],
    ranks: tuple[RankAsset, ...],
    heroes: tuple[HeroSummary, ...] = (),
) -> ItemQuestion:
    """Resolve an item and optional rank range from a conversational question."""
    question = question.strip()
    if len(question) < 3:
        raise ValueError("Ask a question such as: How good is Glass Cannon in Oracle to Phantom?")
    if len(question) > 300:
        raise ValueError("Keep the question under 300 characters.")

    primary_text, comparison_text = _comparison_parts(question)
    item, item_span = _resolve_item(primary_text, items)
    comparison_item = (
        _resolve_item(comparison_text, items)[0] if comparison_text is not None else None
    )
    hero = resolve_hero(question, heroes) if heroes else None

    rank_filter = parse_rank_filter(question, ranks, ignored_span=item_span)
    return ItemQuestion(
        item,
        comparison_item,
        hero,
        rank_filter.minimum_badge,
        rank_filter.maximum_badge,
        rank_filter.rank_label,
    )


def parse_rank_filter(
    question: str,
    ranks: tuple[RankAsset, ...],
    *,
    ignored_span: tuple[int, int] = (-1, -1),
    and_above: bool = False,
) -> DeadlockRankFilter:
    """Resolve rank text independently of any item or hero subject."""
    mentioned: list[tuple[int, int, int | None, str]] = []
    lowered = question.casefold()
    for rank in ranks:
        pattern = (
            rf"\b{re.escape(rank.name.casefold())}"
            r"(?:\s*(iii|ii|iv|vi|i|v|[1-6]))?\b"
        )
        for match in re.finditer(pattern, lowered):
            if ignored_span[0] <= match.start() < ignored_span[1]:
                continue
            subrank_text = match.group(1)
            mentioned.append(
                (
                    match.start(),
                    rank.tier,
                    _parse_subrank(subrank_text),
                    rank.name,
                )
            )
    mentioned.extend(_fuzzy_rank_mentions(question, ranks, ignored_span, mentioned))
    mentioned.sort()
    if not mentioned:
        return DeadlockRankFilter(None, None, "all ranks")

    first = mentioned[0]
    last = mentioned[-1]
    minimum_badge = _badge(first[1], first[2] or 1)
    maximum_badge = None if and_above else _badge(last[1], last[2] or 6)
    if maximum_badge is None:
        return DeadlockRankFilter(
            minimum_badge,
            None,
            f"{_rank_label(first[3], first[2])}+",
        )
    if minimum_badge > maximum_badge:
        minimum_badge, maximum_badge = maximum_badge, minimum_badge
        first, last = last, first

    first_label = _rank_label(first[3], first[2])
    last_label = _rank_label(last[3], last[2])
    rank_label = first_label if first_label == last_label else f"{first_label}–{last_label}"
    return DeadlockRankFilter(minimum_badge, maximum_badge, rank_label)


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


def resolve_hero(question: str, heroes: tuple[HeroSummary, ...]) -> HeroSummary | None:
    lowered = _normalize(question)
    exact = [
        hero
        for hero in heroes
        if _normalize(hero.name) in lowered
        or (hero.search_name and _normalize(hero.search_name) in lowered)
    ]
    if exact:
        return max(exact, key=lambda hero: len(_normalize(hero.name)))

    words = lowered.split()
    scored: list[tuple[float, HeroSummary]] = []
    for hero in heroes:
        name = _normalize(hero.name)
        count = len(name.split())
        for index in range(max(0, len(words) - count + 1)):
            phrase = " ".join(words[index : index + count])
            scored.append((SequenceMatcher(None, phrase, name).ratio(), hero))
    if not scored:
        return None
    scored.sort(key=lambda value: value[0], reverse=True)
    best = scored[0]
    runner_up = next((value for value in scored[1:] if value[1] != best[1]), None)
    if best[0] >= 0.82 and (runner_up is None or best[0] - runner_up[0] >= 0.08):
        return best[1]
    return None


def comparable_win_rate(
    target: ItemStats,
    item: ItemSummary,
    all_stats: tuple[ItemStats, ...],
    item_catalog: dict[int, ItemSummary],
) -> tuple[float | None, int]:
    """Return the median win rate of same-tier items bought at a similar time."""
    same_tier = [
        row
        for row in all_stats
        if row.item_id != target.item_id
        and row.matches >= 20
        and row.win_rate is not None
        and (peer := item_catalog.get(row.item_id)) is not None
        and peer.tier == item.tier
        and peer.slot_type == item.slot_type
    ]
    timed = [
        row
        for row in same_tier
        if abs(row.average_buy_time_seconds - target.average_buy_time_seconds) <= 300
    ]
    peers = timed if len(timed) >= 3 else same_tier
    rates = [row.win_rate for row in peers if row.win_rate is not None]
    return (median(rates), len(rates)) if rates else (None, 0)


def wilson_interval(wins: int, matches: int) -> tuple[float, float]:
    """Calculate a 95% Wilson score interval for a binomial win rate."""
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


def item_verdict(stats: ItemStats, peer_win_rate: float | None) -> str:
    if stats.matches < 20:
        return "Too little data"
    if peer_win_rate is None or stats.win_rate is None:
        return "Limited comparison data"
    delta = stats.win_rate - peer_win_rate
    if stats.matches < 100:
        return "Promising, but low confidence" if delta >= 0.03 else "Unclear with a small sample"
    if delta >= 0.03:
        return "Strong in this range"
    if delta >= 0.01:
        return "Above average"
    if delta <= -0.03:
        return "Weak in this range"
    if delta <= -0.01:
        return "Below average"
    return "About average"


def _normalize(value: str) -> str:
    value = value.casefold().replace("'", "").replace("’", "")
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _resolve_item(
    question: str,
    items: tuple[ItemSummary, ...],
) -> tuple[ItemSummary, tuple[int, int]]:
    words = list(re.finditer(r"[a-z0-9]+", question.casefold()))
    candidates: list[tuple[float, int, ItemSummary, tuple[int, int]]] = []
    for item in items:
        if not item.shopable:
            continue
        item_name = _normalize(item.name)
        compact_name = item_name.replace(" ", "")
        word_count = len(item_name.split())
        if word_count == 0:
            continue
        if word_count <= len(words):
            for index in range(len(words) - word_count + 1):
                window = words[index : index + word_count]
                phrase = " ".join(match.group(0) for match in window)
                score = SequenceMatcher(None, phrase, item_name).ratio()
                candidates.append(
                    (
                        score,
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
    if not candidates:
        raise ValueError(
            "I could not find an item name in that question. Use its current in-game name."
        )
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]), reverse=True)
    best = candidates[0]
    if best[0] < 0.80:
        raise ValueError(
            "I could not find an item name in that question. Use its current in-game name."
        )
    runner_up = next(
        (candidate for candidate in candidates[1:] if candidate[2].item_id != best[2].item_id),
        None,
    )
    if runner_up is not None and best[0] < 1.0 and best[0] - runner_up[0] < 0.06:
        raise ValueError(
            f"That item name is ambiguous. Did you mean {best[2].name} or {runner_up[2].name}?"
        )
    return best[2], best[3]


def _fuzzy_rank_mentions(
    question: str,
    ranks: tuple[RankAsset, ...],
    item_span: tuple[int, int],
    exact: list[tuple[int, int, int | None, str]],
) -> list[tuple[int, int, int | None, str]]:
    results: list[tuple[int, int, int | None, str]] = []
    exact_positions = {position for position, _, _, _ in exact}
    for token in re.finditer(r"[a-z0-9]+", question.casefold()):
        if len(token.group(0)) < 4:
            continue
        if item_span[0] <= token.start() < item_span[1] or token.start() in exact_positions:
            continue
        scored = sorted(
            (
                (SequenceMatcher(None, token.group(0), rank.name.casefold()).ratio(), rank)
                for rank in ranks
            ),
            key=lambda candidate: candidate[0],
            reverse=True,
        )
        if not scored or scored[0][0] < 0.78:
            continue
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.08:
            continue
        rank = scored[0][1]
        results.append((token.start(), rank.tier, None, rank.name))
    return results


def _parse_subrank(value: str | None) -> int | None:
    if value is None:
        return None
    if value.isdigit():
        return int(value)
    return _ROMAN_SUBRANKS.get(value)


def _badge(tier: int, subrank: int) -> int:
    if tier == 0:
        return 0
    return tier * 10 + subrank


def _rank_label(name: str, subrank: int | None) -> str:
    if subrank is None:
        return name
    roman = next(key.upper() for key, value in _ROMAN_SUBRANKS.items() if value == subrank)
    return f"{name} {roman}"
