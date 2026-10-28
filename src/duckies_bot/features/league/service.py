"""League player lookup orchestration."""

from __future__ import annotations

import asyncio

from ...providers.riot import RiotAPIError, RiotClient
from .models import LeagueMatch, LeaguePlayerLookup, SummonerProfile


class LeagueService:
    def __init__(self, client: RiotClient) -> None:
        self.client = client
        self._match_cache: dict[str, LeagueMatch] = {}
        self._data_dragon_cache: tuple[str | None, dict[int, str]] | None = None

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

