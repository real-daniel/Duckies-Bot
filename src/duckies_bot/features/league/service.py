"""League player lookup orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from ...providers.riot import RiotAPIError, RiotClient
from ..item_questions import (
    ItemQuestionInterpreter,
    QuestionInterpreterUnavailableError,
    canonical_item_question,
)
from .analytics import (
    LeagueItemQuestion,
    aggregate_item_position_stats,
    aggregate_item_stats,
    comparable_win_rate,
    item_verdict,
    parse_league_item_question,
    wilson_interval,
)
from .models import (
    LeagueChampion,
    LeagueItem,
    LeagueItemAnalysis,
    LeagueItemStats,
    LeagueMatch,
    LeagueMatchTimeline,
    LeaguePlayerLookup,
    SummonerProfile,
)


LOGGER = logging.getLogger(__name__)


class LeagueService:
    def __init__(
        self,
        client: RiotClient,
        *,
        question_interpreter: ItemQuestionInterpreter | None = None,
    ) -> None:
        self.client = client
        self.question_interpreter = question_interpreter
        self._match_cache: dict[str, LeagueMatch] = {}
        self._timeline_cache: dict[str, LeagueMatchTimeline] = {}
        self._data_dragon_cache: tuple[str | None, dict[int, str]] | None = None
        self._data_dragon_items_cache: tuple[str | None, tuple[LeagueItem, ...]] | None = None
        self._data_dragon_champions_cache: (
            tuple[str | None, tuple[LeagueChampion, ...]] | None
        ) = None
        self._analysis_sample_cache: dict[
            tuple[str, str, tuple[tuple[str, str], ...]], _CachedLeagueSample
        ] = {}

    async def answer_item_question(
        self,
        question: str,
        platform: str,
    ) -> LeagueItemAnalysis:
        version, items = await self._data_dragon_items()
        if version is None or not items:
            raise RiotAPIError("Riot's current League item catalog is unavailable.")
        _, champions = await self._data_dragon_champions(version)
        interpreted_question = question
        if self.question_interpreter is not None:
            try:
                interpretation = await self.question_interpreter.interpret(
                    question, game="league"
                )
            except QuestionInterpreterUnavailableError:
                LOGGER.warning(
                    "OpenAI question interpretation unavailable; using League parser",
                    exc_info=True,
                )
                interpreted_question = question
            else:
                interpreted_question = canonical_item_question(
                    interpretation, expected_game="league"
                )
        parsed = parse_league_item_question(interpreted_question, items, champions)
        matches = await self._ranked_sample(parsed, platform, version)
        patch_key = ".".join(version.split(".")[:2])
        current_matches = tuple(
            match
            for match in matches
            if match.queue_id == 420 and match.game_version.startswith(f"{patch_key}.")
        )
        if not current_matches:
            raise RiotAPIError(
                "Riot returned no current-patch ranked matches for that sample. Try again later."
            )
        relevant_matches = current_matches
        if parsed.champion is not None:
            relevant_matches = tuple(
                match
                for match in current_matches
                if any(
                    participant.champion_name.casefold() == parsed.champion.key.casefold()
                    or participant.champion_name.casefold() == parsed.champion.name.casefold()
                    for participant in match.participants
                )
            )
        if parsed.champion is not None and not relevant_matches:
            raise ValueError(
                f"No {parsed.champion.name} games appeared in the sampled "
                f"{parsed.rank_label} matches."
            )

        if parsed.champion is not None and parsed.item_position is not None:
            timelines = await self._timelines(
                tuple(match.match_id for match in relevant_matches), platform
            )
            timeline_by_id = {timeline.match_id: timeline for timeline in timelines}
            core_ids = {
                item.item_id
                for item in items
                if not item.builds_into and item.total_cost >= 2000 and "Boots" not in item.tags
            }
            observations = tuple(
                (
                    participant.win,
                    tuple(
                        item_id
                        for item_id in timeline_by_id.get(
                            match.match_id, LeagueMatchTimeline(match.match_id, {})
                        ).purchases.get(participant.participant_id, ())
                        if item_id in core_ids
                    ),
                )
                for match in relevant_matches
                for participant in match.participants
                if parsed.champion is not None
                and (
                    participant.champion_name.casefold() == parsed.champion.key.casefold()
                    or participant.champion_name.casefold() == parsed.champion.name.casefold()
                )
            )
            aggregate = lambda item_id: aggregate_item_position_stats(  # noqa: E731
                item_id, parsed.item_position or 1, observations
            )
        else:
            observations = tuple(
                (participant.win, participant.item_ids)
                for match in relevant_matches
                for participant in match.participants
                if parsed.champion is None
                or participant.champion_name.casefold() == parsed.champion.key.casefold()
                or participant.champion_name.casefold() == parsed.champion.name.casefold()
            )
            aggregate = lambda item_id: aggregate_item_stats(item_id, observations)  # noqa: E731
        target = aggregate(parsed.item.item_id)
        if target.matches == 0:
            raise ValueError(
                f"{parsed.item.name} did not appear for that champion/filter in the sampled "
                f"{parsed.rank_label} matches on the current patch."
            )
        observed_item_ids = {
            item_id for _, item_ids in observations for item_id in item_ids
        }
        all_stats = tuple(
            aggregate(item_id)
            for item_id in observed_item_ids
        )
        catalog = {item.item_id: item for item in items}
        comparison_stats = (
            aggregate(parsed.comparison_item.item_id)
            if parsed.comparison_item is not None else None
        )
        if comparison_stats is not None and comparison_stats.matches == 0:
            raise ValueError(
                f"{parsed.comparison_item.name} did not appear for the same champion and item slot."
            )
        if comparison_stats is not None:
            peer_rate, peer_count = comparison_stats.win_rate, 1
        else:
            peer_rate, peer_count = comparable_win_rate(
                target, parsed.item, all_stats, catalog
            )
        low, high = wilson_interval(target.wins, target.matches)
        verdict = item_verdict(target, peer_rate)
        if comparison_stats is not None and comparison_stats.matches < 10:
            verdict = "Too little comparison data"
        return LeagueItemAnalysis(
            item=parsed.item,
            stats=target,
            data_dragon_version=version,
            rank_label=parsed.rank_label,
            platform=platform.upper(),
            games_scanned=len(relevant_matches),
            players_scanned=len(observations),
            peer_win_rate=peer_rate,
            peer_count=peer_count,
            confidence_low=low,
            confidence_high=high,
            verdict=verdict,
            champion=parsed.champion,
            item_position=parsed.item_position,
            comparison_item=parsed.comparison_item,
            comparison_stats=comparison_stats,
        )

    async def player_lookup(
        self,
        riot_id: str,
        platform: str,
        *,
        match_count: int = 10,
    ) -> LeaguePlayerLookup:
        game_name, tag_line = parse_riot_id(riot_id)
        if not 5 <= match_count <= 15:
            raise ValueError("match_count must be between 5 and 15")

        account = await self.client.get_account(game_name, tag_line, platform)
        summoner_result, ranked_result, match_ids, catalog = await asyncio.gather(
            self._optional_summoner(account.puuid, platform),
            self.client.get_ranked_entries(account.puuid, platform),
            self.client.get_match_ids(account.puuid, platform, count=match_count),
            self._data_dragon_catalog(),
        )
        matches = await self._matches(match_ids, platform)
        version, item_names = catalog
        return LeaguePlayerLookup(
            account=account,
            platform=platform.upper(),
            summoner=summoner_result,
            ranked_entries=ranked_result,
            matches=matches,
            data_dragon_version=version,
            item_names=item_names,
        )

    async def _optional_summoner(
        self,
        puuid: str,
        platform: str,
    ) -> SummonerProfile | None:
        try:
            return await self.client.get_summoner(puuid, platform)
        except RiotAPIError as exc:
            if "does not have a League profile" in exc.user_message:
                return None
            raise

    async def _matches(
        self,
        match_ids: tuple[str, ...],
        platform: str,
    ) -> tuple[LeagueMatch, ...]:
        semaphore = asyncio.Semaphore(5)

        async def load(match_id: str) -> LeagueMatch | None:
            cached = self._match_cache.get(match_id)
            if cached is not None:
                return cached
            async with semaphore:
                try:
                    match = await self.client.get_match(match_id, platform)
                except RiotAPIError as exc:
                    if "no longer available" in exc.user_message:
                        return None
                    raise
            self._match_cache[match_id] = match
            if len(self._match_cache) > 500:
                self._match_cache.pop(next(iter(self._match_cache)))
            return match

        loaded = await asyncio.gather(*(load(match_id) for match_id in match_ids))
        return tuple(match for match in loaded if match is not None)

    async def _data_dragon_catalog(self) -> tuple[str | None, dict[int, str]]:
        if self._data_dragon_cache is None:
            self._data_dragon_cache = await self.client.get_data_dragon_catalog()
        return self._data_dragon_cache

    async def _data_dragon_items(self) -> tuple[str | None, tuple[LeagueItem, ...]]:
        if self._data_dragon_items_cache is None:
            self._data_dragon_items_cache = await self.client.get_data_dragon_items()
        return self._data_dragon_items_cache

    async def _data_dragon_champions(
        self, version: str
    ) -> tuple[str | None, tuple[LeagueChampion, ...]]:
        if self._data_dragon_champions_cache is None:
            loader = getattr(self.client, "get_data_dragon_champions", None)
            self._data_dragon_champions_cache = (
                await loader(version) if loader is not None else (version, ())
            )
        return self._data_dragon_champions_cache

    async def _timelines(
        self, match_ids: tuple[str, ...], platform: str
    ) -> tuple[LeagueMatchTimeline, ...]:
        semaphore = asyncio.Semaphore(5)

        async def load(match_id: str) -> LeagueMatchTimeline:
            cached = self._timeline_cache.get(match_id)
            if cached is not None:
                return cached
            async with semaphore:
                timeline = await self.client.get_match_timeline(match_id, platform)
            self._timeline_cache[match_id] = timeline
            if len(self._timeline_cache) > 500:
                self._timeline_cache.pop(next(iter(self._timeline_cache)))
            return timeline

        return tuple(await asyncio.gather(*(load(match_id) for match_id in match_ids)))

    async def _ranked_sample(
        self,
        question: LeagueItemQuestion,
        platform: str,
        version: str,
    ) -> tuple[LeagueMatch, ...]:
        bucket_key = tuple((bucket.tier, bucket.division) for bucket in question.buckets)
        key = (version, platform.upper(), bucket_key)
        now = time.monotonic()
        cached = self._analysis_sample_cache.get(key)
        if cached is not None and cached.expires_at > now:
            return cached.matches

        ladders = await asyncio.gather(
            *(
                self.client.get_league_entries(
                    platform,
                    bucket.tier,
                    bucket.division,
                )
                for bucket in question.buckets
            )
        )
        seeds = []
        for entries in ladders:
            if not entries:
                continue
            indexes = sorted(
                {
                    0,
                    len(entries) // 4,
                    len(entries) // 2,
                    3 * len(entries) // 4,
                    len(entries) - 1,
                }
            )
            seeds.extend(entries[index].puuid for index in indexes)
        unique_seeds = tuple(dict.fromkeys(seeds))[:6]
        if not unique_seeds:
            raise RiotAPIError("Riot returned no players for that ranked tier.")
        match_groups = await asyncio.gather(
            *(
                self.client.get_match_ids(
                    puuid,
                    platform,
                    count=10,
                    queue=420,
                )
                for puuid in unique_seeds
            )
        )
        match_ids = tuple(dict.fromkeys(match_id for group in match_groups for match_id in group))
        matches = await self._matches(match_ids, platform)
        self._analysis_sample_cache[key] = _CachedLeagueSample(now + 30 * 60, matches)
        return matches


def parse_riot_id(value: str) -> tuple[str, str]:
    value = value.strip()
    if "#" not in value:
        raise ValueError("Enter a complete Riot ID in the form Game Name#TAG.")
    game_name, tag_line = value.rsplit("#", 1)
    game_name = game_name.strip()
    tag_line = tag_line.strip()
    if not game_name or not tag_line:
        raise ValueError("Enter a complete Riot ID in the form Game Name#TAG.")
    if len(game_name) > 64 or len(tag_line) > 16:
        raise ValueError("That Riot ID is too long.")
    return game_name, tag_line


@dataclass(frozen=True, slots=True)
class _CachedLeagueSample:
    expires_at: float
    matches: tuple[LeagueMatch, ...]

