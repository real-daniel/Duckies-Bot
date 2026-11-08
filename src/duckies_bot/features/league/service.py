"""League player lookup orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from ...providers.riot import RiotAPIError, RiotClient
from ..item_questions import (
    ItemQuestionInterpreter,
    QuestionInterpreterUnavailableError,
    canonical_item_question,
    canonical_rank_question,
    validate_analytics_question,
)
from .analytics import (
    LeagueItemQuestion,
    aggregate_item_position_stats,
    aggregate_item_stats,
    comparable_win_rate,
    item_verdict,
    parse_league_item_question,
    parse_rank_filter,
    resolve_champion,
    wilson_interval,
)
from .models import (
    LeagueChampion,
    LeagueChampionAnalysis,
    LeagueChampionStats,
    LeagueItem,
    LeagueItemAnalysis,
    LeagueItemStats,
    LeagueMatch,
    LeagueMatchTimeline,
    LeaguePlayerLookup,
    LeagueRankedInsight,
    LeagueRankedInsightsAnalysis,
    SummonerProfile,
)


LOGGER = logging.getLogger(__name__)


def _performance_verdict(
    win_rate: float | None,
    baseline: float | None,
    matches: int,
) -> str:
    if win_rate is None or baseline is None or matches < 10:
        return "Too little data"
    delta = win_rate - baseline
    if delta >= 0.03:
        return "Strong in this sample"
    if delta <= -0.03:
        return "Below the sampled field"
    return "Around the sampled average"


def _rank_league_insights(
    entries: tuple[LeagueRankedInsight, ...], interpretation
) -> tuple[LeagueRankedInsight, ...]:
    if interpretation.sort_by in {"popularity", "matches", "pick_rate"}:
        key = lambda entry: (entry.pick_rate or 0.0, entry.matches)
    else:
        key = lambda entry: (entry.win_rate or 0.0, entry.matches)
    return tuple(sorted(
        entries,
        key=key,
        reverse=interpretation.sort_direction == "high",
    )[: interpretation.result_limit])


def _position_for_role(role: str) -> str | None:
    return {
        "top": "TOP",
        "jungle": "JUNGLE",
        "mid": "MIDDLE",
        "bottom": "BOTTOM",
        "support": "UTILITY",
    }.get(role)


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
        result = await self.answer_question(question, platform)
        if not isinstance(result, LeagueItemAnalysis):
            raise ValueError("That question asks about a champion rather than an item.")
        return result

    async def answer_question(
        self,
        question: str,
        platform: str,
    ) -> LeagueItemAnalysis | LeagueChampionAnalysis | LeagueRankedInsightsAnalysis:
        """Interpret and route a flexible current-patch analytics question."""
        if self.question_interpreter is None:
            return await self._answer_item_question(question, platform)
        try:
            interpretation = await self.question_interpreter.interpret(
                question, game="league"
            )
        except QuestionInterpreterUnavailableError:
            LOGGER.warning(
                "OpenAI question interpretation unavailable; using League parser",
                exc_info=True,
            )
            return await self._answer_item_question(question, platform)
        interpretation = validate_analytics_question(
            interpretation, expected_game="league"
        )
        if interpretation.match_mode != "ranked":
            raise ValueError("League aggregate analysis currently supports ranked matches only.")
        if interpretation.intent == "rank":
            return await self._answer_meta_ranking(interpretation, platform)
        if interpretation.intent == "recommend":
            return await self._answer_item_recommendations(interpretation, platform)
        if interpretation.intent in {"matchup", "synergy"}:
            return await self._answer_champion_relationship(interpretation, platform)
        if interpretation.subject_type == "character":
            return await self._answer_champion_question(interpretation, platform)
        return await self._answer_item_question(
            canonical_item_question(interpretation),
            platform,
            rank_and_above=interpretation.rank_scope == "and_above",
        )

    async def _answer_item_question(
        self,
        question: str,
        platform: str,
        *,
        rank_and_above: bool = False,
    ) -> LeagueItemAnalysis:
        version, items = await self._data_dragon_items()
        if version is None or not items:
            raise RiotAPIError("Riot's current League item catalog is unavailable.")
        _, champions = await self._data_dragon_champions(version)
        parsed = parse_league_item_question(question, items, champions)
        if rank_and_above:
            rank_filter = parse_rank_filter(question, and_above=True)
            parsed = replace(
                parsed,
                buckets=rank_filter.buckets,
                rank_label=rank_filter.rank_label,
            )
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

    async def _answer_champion_question(
        self,
        interpretation,
        platform: str,
    ) -> LeagueChampionAnalysis:
        version, champions = await self._data_dragon_champions(
            (await self._data_dragon_items())[0] or ""
        )
        if version is None or not champions:
            raise RiotAPIError("Riot's current League champion catalog is unavailable.")
        champion = resolve_champion(interpretation.primary_subject or "", champions)
        if champion is None:
            raise ValueError(
                f"I could not find a League champion matching {interpretation.primary_subject!r}."
            )
        comparison = None
        if interpretation.comparison_subject:
            comparison = resolve_champion(interpretation.comparison_subject, champions)
            if comparison is None:
                raise ValueError(
                    f"I could not find a League champion matching {interpretation.comparison_subject!r}."
                )
        rank_filter = parse_rank_filter(
            canonical_rank_question(interpretation),
            and_above=interpretation.rank_scope == "and_above",
        )
        matches = await self._ranked_sample(rank_filter, platform, version)
        patch_key = ".".join(version.split(".")[:2])
        matches = tuple(
            match for match in matches
            if match.queue_id == 420 and match.game_version.startswith(f"{patch_key}.")
        )
        if not matches:
            raise RiotAPIError(
                "Riot returned no current-patch ranked matches for that sample. Try again later."
            )
        participants = tuple(player for match in matches for player in match.participants)
        expected_position = _position_for_role(interpretation.role)
        if expected_position:
            participants = tuple(
                player for player in participants
                if player.team_position.upper() == expected_position
            )

        def aggregate(target: LeagueChampion) -> LeagueChampionStats:
            selected = tuple(
                player for player in participants
                if player.champion_name.casefold() in {
                    target.key.casefold(), target.name.casefold()
                }
            )
            wins = sum(player.win for player in selected)
            return LeagueChampionStats(
                wins=wins,
                losses=len(selected) - wins,
                games_scanned=len(matches),
                players_scanned=len(participants),
                kills=sum(player.kills for player in selected),
                deaths=sum(player.deaths for player in selected),
                assists=sum(player.assists for player in selected),
                damage=sum(player.total_damage_to_champions for player in selected),
            )

        stats = aggregate(champion)
        if not stats.matches:
            raise ValueError(
                f"No {champion.name} games appeared in the sampled {rank_filter.rank_label} matches."
            )
        comparison_stats = aggregate(comparison) if comparison else None
        if comparison is not None and comparison_stats is not None and not comparison_stats.matches:
            raise ValueError(
                f"No {comparison.name} games appeared in the same ranked sample."
            )
        champion_rates = []
        for candidate in champions:
            candidate_stats = aggregate(candidate)
            if candidate_stats.win_rate is not None:
                champion_rates.append(candidate_stats.win_rate)
        field_rate = median(champion_rates) if champion_rates else None
        baseline = comparison_stats.win_rate if comparison_stats else field_rate
        verdict = _performance_verdict(stats.win_rate, baseline, stats.matches)
        return LeagueChampionAnalysis(
            champion=champion,
            stats=stats,
            data_dragon_version=version,
            rank_label=rank_filter.rank_label,
            platform=platform.upper(),
            field_win_rate=field_rate,
            verdict=verdict,
            comparison_champion=comparison,
            comparison_stats=comparison_stats,
        )

    async def _analytics_sample(self, interpretation, platform: str):
        version, items = await self._data_dragon_items()
        if version is None:
            raise RiotAPIError("Riot's current League catalog is unavailable.")
        _, champions = await self._data_dragon_champions(version)
        rank_filter = parse_rank_filter(
            canonical_rank_question(interpretation),
            and_above=interpretation.rank_scope == "and_above",
        )
        matches = await self._ranked_sample(rank_filter, platform, version)
        patch_key = ".".join(version.split(".")[:2])
        matches = tuple(
            match for match in matches
            if match.queue_id == 420 and match.game_version.startswith(f"{patch_key}.")
        )
        if not matches:
            raise RiotAPIError(
                "Riot returned no current-patch ranked matches for that sample. Try again later."
            )
        participants = tuple(player for match in matches for player in match.participants)
        expected_position = _position_for_role(interpretation.role)
        if expected_position:
            participants = tuple(
                player for player in participants
                if player.team_position.upper() == expected_position
            )
        return version, items, champions, rank_filter, matches, participants

    async def _answer_meta_ranking(
        self, interpretation, platform: str
    ) -> LeagueRankedInsightsAnalysis:
        version, _, champions, rank_filter, matches, participants = (
            await self._analytics_sample(interpretation, platform)
        )
        catalog = {
            champion.key.casefold(): champion for champion in champions
        } | {
            champion.name.casefold(): champion for champion in champions
        }
        grouped: dict[int, list] = defaultdict(list)
        resolved: dict[int, LeagueChampion] = {}
        for player in participants:
            champion = catalog.get(player.champion_name.casefold())
            if champion is not None:
                grouped[champion.champion_id].append(player)
                resolved[champion.champion_id] = champion
        entries = tuple(
            LeagueRankedInsight(
                champion_id,
                resolved[champion_id].name,
                resolved[champion_id].icon_url,
                sum(player.win for player in players),
                len(players),
                len(players) / len(participants) if participants else None,
            )
            for champion_id, players in grouped.items()
            if len(players) >= 2
        )
        selected = _rank_league_insights(entries, interpretation)
        return LeagueRankedInsightsAnalysis(
            category="Champion meta",
            subject_name=None,
            entries=selected,
            data_dragon_version=version,
            rank_label=rank_filter.rank_label,
            platform=platform.upper(),
            games_scanned=len(matches),
            players_scanned=len(participants),
            description="Champions ranked within Duckies' bounded Riot match sample.",
        )

    async def _answer_item_recommendations(
        self, interpretation, platform: str
    ) -> LeagueRankedInsightsAnalysis:
        version, items, champions, rank_filter, matches, participants = (
            await self._analytics_sample(interpretation, platform)
        )
        champion = resolve_champion(interpretation.character_context or "", champions)
        if champion is None:
            raise ValueError(
                f"I could not find a League champion matching {interpretation.character_context!r}."
            )
        selected_players = tuple(
            player for player in participants
            if player.champion_name.casefold() in {
                champion.key.casefold(), champion.name.casefold()
            }
        )
        if not selected_players:
            raise ValueError(
                f"No {champion.name} games appeared in the sampled {rank_filter.rank_label} matches."
            )
        core_items = {
            item.item_id: item for item in items
            if not item.builds_into and item.total_cost >= 2000 and "Boots" not in item.tags
        }
        observations: tuple[tuple[bool, tuple[int, ...]], ...]
        if interpretation.item_position is not None:
            timelines = await self._timelines(
                tuple(match.match_id for match in matches), platform
            )
            timeline_by_id = {timeline.match_id: timeline for timeline in timelines}
            observations = tuple(
                (
                    player.win,
                    tuple(
                        item_id for item_id in timeline_by_id.get(
                            match.match_id, LeagueMatchTimeline(match.match_id, {})
                        ).purchases.get(player.participant_id, ())
                        if item_id in core_items
                    ),
                )
                for match in matches
                for player in match.participants
                if player in selected_players
            )
            aggregate = lambda item_id: aggregate_item_position_stats(  # noqa: E731
                item_id, interpretation.item_position, observations
            )
        else:
            observations = tuple(
                (player.win, tuple(item for item in player.item_ids if item in core_items))
                for player in selected_players
            )
            aggregate = lambda item_id: aggregate_item_stats(item_id, observations)  # noqa: E731
        entries = []
        for item_id, item in core_items.items():
            stats = aggregate(item_id)
            if stats.matches:
                entries.append(LeagueRankedInsight(
                    item_id,
                    item.name,
                    item.icon_url,
                    stats.wins,
                    stats.matches,
                    stats.matches / len(observations) if observations else None,
                ))
        selected = _rank_league_insights(tuple(entries), interpretation)
        return LeagueRankedInsightsAnalysis(
            category="Item recommendations",
            subject_name=champion.name,
            entries=selected,
            data_dragon_version=version,
            rank_label=rank_filter.rank_label,
            platform=platform.upper(),
            games_scanned=len(matches),
            players_scanned=len(observations),
            description=(
                "Observed core-item results in the sampled builds; later items have "
                "survivorship and game-length bias."
            ),
        )

    async def _answer_champion_relationship(
        self, interpretation, platform: str
    ) -> LeagueRankedInsightsAnalysis:
        version, _, champions, rank_filter, matches, participants = (
            await self._analytics_sample(interpretation, platform)
        )
        champion = resolve_champion(interpretation.primary_subject or "", champions)
        if champion is None:
            raise ValueError(
                f"I could not find a League champion matching {interpretation.primary_subject!r}."
            )
        comparison = (
            resolve_champion(interpretation.comparison_subject, champions)
            if interpretation.comparison_subject else None
        )
        if interpretation.comparison_subject and comparison is None:
            raise ValueError(
                f"I could not find a League champion matching {interpretation.comparison_subject!r}."
            )
        catalog = {
            candidate.key.casefold(): candidate for candidate in champions
        } | {
            candidate.name.casefold(): candidate for candidate in champions
        }
        pairs: dict[int, list[bool]] = defaultdict(list)
        related_catalog: dict[int, LeagueChampion] = {}
        for match in matches:
            subjects = tuple(
                player for player in match.participants
                if player in participants and player.champion_name.casefold() in {
                    champion.key.casefold(), champion.name.casefold()
                }
            )
            for subject in subjects:
                for related in match.participants:
                    same_team = related.team_id == subject.team_id
                    wanted_team = (
                        same_team if interpretation.intent == "synergy" else not same_team
                    )
                    if not wanted_team or related is subject:
                        continue
                    if interpretation.lane_scope == "same_lane" and (
                        related.team_position != subject.team_position
                    ):
                        continue
                    related_champion = catalog.get(related.champion_name.casefold())
                    if related_champion is None:
                        continue
                    if comparison is not None and related_champion != comparison:
                        continue
                    pairs[related_champion.champion_id].append(subject.win)
                    related_catalog[related_champion.champion_id] = related_champion
        entries = tuple(
            LeagueRankedInsight(
                champion_id,
                related_catalog[champion_id].name,
                related_catalog[champion_id].icon_url,
                sum(outcomes),
                len(outcomes),
            )
            for champion_id, outcomes in pairs.items()
        )
        if not entries:
            raise ValueError(
                f"No {interpretation.intent} data appeared for {champion.name} in this bounded sample."
            )
        if comparison is not None:
            selected = entries[:1]
        elif interpretation.intent == "matchup":
            selected = tuple(sorted(
                entries,
                key=lambda entry: (entry.win_rate if entry.win_rate is not None else 1.0),
            )[: interpretation.result_limit])
        else:
            selected = tuple(sorted(
                entries,
                key=lambda entry: (entry.win_rate or 0.0, entry.matches),
                reverse=True,
            )[: interpretation.result_limit])
        return LeagueRankedInsightsAnalysis(
            category=("Counters and matchups" if interpretation.intent == "matchup" else "Champion synergies"),
            subject_name=champion.name,
            entries=selected,
            data_dragon_version=version,
            rank_label=rank_filter.rank_label,
            platform=platform.upper(),
            games_scanned=len(matches),
            players_scanned=len(participants),
            description=(
                "Lowest subject win rates appear first for counters."
                if interpretation.intent == "matchup" and comparison is None
                else "Pair results from the bounded sampled matches."
            ),
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

