"""Deadlock command orchestration."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

from .models import (
    HeroExperience,
    HeroSummary,
    LiveChatMessage,
    LiveLookup,
    LiveMatchSnapshot,
    MatchScout,
    PlayerHistory,
    PlayerRank,
    RankAsset,
    ScoutedPlayer,
)
from ...providers.deadlock import DeadlockAPIError, DeadlockClient, DeadlockLiveClient
from ...storage import BroadcastURLRepository


_BROADCAST_URL_TTL_SECONDS = 6 * 60 * 60
_ACTIVE_MATCHES_TTL_SECONDS = 30


class DeadlockService:
    def __init__(
        self,
        client: DeadlockClient,
        live_client: DeadlockLiveClient | None = None,
        broadcast_urls: BroadcastURLRepository | None = None,
    ) -> None:
        self.client = client
        self.live_client = live_client
        self.broadcast_urls = broadcast_urls
        self._profile_cache: dict[int, _CachedProfile] = {}
        self._experience_cache: dict[tuple[int, int], _CachedExperience] = {}
        self._hero_cache: dict[int, HeroSummary] = {}
        self._hero_cache_expires_at = 0.0
        self._hero_cache_lock = asyncio.Lock()
        self._rank_assets: tuple[RankAsset, ...] = ()
        self._rank_assets_expires_at = 0.0
        self._broadcast_url_cache: dict[int, _CachedBroadcastURL] = {}
        self._broadcast_url_locks: dict[int, asyncio.Lock] = {}
        self._active_match_ids: tuple[int, ...] = ()
        self._active_match_ids_expires_at = 0.0
        self._active_match_ids_lock = asyncio.Lock()

    async def live_lookup(self, account_id: int) -> LiveLookup | None:
        match = await self.client.get_active_match(account_id)
        if match is None:
            return None
        player = match.player(account_id)
        if player is None:
            return None
        hero = None
        if player.hero_id is not None:
            hero = (await self._get_heroes((player.hero_id,))).get(player.hero_id)
        return LiveLookup(match, player, hero)

    async def random_top_200_match_id(self) -> int:
        now = time.monotonic()
        if self._active_match_ids_expires_at <= now:
            async with self._active_match_ids_lock:
                now = time.monotonic()
                if self._active_match_ids_expires_at <= now:
                    matches = await self.client.get_active_matches()
                    self._active_match_ids = tuple(
                        match.match_id for match in matches if match.match_id > 0
                    )
                    self._active_match_ids_expires_at = (
                        now + _ACTIVE_MATCHES_TTL_SECONDS
                    )
        if not self._active_match_ids:
            raise DeadlockAPIError(
                "Deadlock's top-200 Watch feed does not currently contain a match."
            )
        return random.choice(self._active_match_ids)

    async def live_match(self, match_id: int) -> LiveMatchSnapshot:
        if self.live_client is None:
            raise DeadlockAPIError("The Deadlock live parser is not configured.")
        broadcast_url = await self._get_broadcast_url(match_id)
        snapshot = await self.live_client.get_match_players(match_id, broadcast_url)
        hero_ids = sorted(
            {player.hero_id for player in snapshot.players if player.hero_id is not None}
        )
        heroes_by_id = await self._get_heroes(hero_ids)
        heroes = tuple(
            heroes_by_id[hero_id]
            for hero_id in hero_ids
            if hero_id in heroes_by_id
        )
        return replace(snapshot, heroes=heroes)

    async def scout_match(self, match_id: int) -> MatchScout:
        snapshot = await self._live_match_with_retries(match_id)
        pairs = {
            (player.account_id, player.hero_id)
            for player in snapshot.players
            if player.hero_id is not None
        }
        profile_limit = asyncio.Semaphore(8)
        profiles_task = asyncio.gather(
            *(self._get_profile(player.account_id, profile_limit) for player in snapshot.players)
        )
        experiences_task = self._get_experiences(pairs)
        rank_assets_task = self._get_rank_assets()
        profiles, experiences, rank_assets = await asyncio.gather(
            profiles_task,
            experiences_task,
            rank_assets_task,
        )
        rank_names = {asset.tier: asset.name for asset in rank_assets}
        experience_by_pair = {
            (experience.account_id, experience.hero_id): experience
            for experience in experiences
        }

        players: list[ScoutedPlayer] = []
        for player, profile in zip(snapshot.players, profiles, strict=True):
            rank_name = None
            if profile.rank is not None:
                rank_name = rank_names.get(profile.rank.tier)
                if profile.rank.tier == 0:
                    rank_name = "Unranked"
            players.append(
                ScoutedPlayer(
                    player=player,
                    hero=snapshot.hero(player.hero_id),
                    rank=profile.rank,
                    rank_name=rank_name,
                    experience=experience_by_pair.get((player.account_id, player.hero_id)),
                    recent_outcomes=profile.history.outcomes if profile.history else (),
                )
            )
        return MatchScout(
            match_id=snapshot.match_id,
            game_time_seconds=snapshot.game_time_seconds,
            players=tuple(players),
        )

    async def stream_chat_messages(
        self,
        match_id: int,
    ) -> AsyncIterator[LiveChatMessage]:
        if self.live_client is None:
            raise DeadlockAPIError("The Deadlock live parser is not configured.")
        async for message in self.live_client.stream_chat_messages(match_id):
            yield message

    async def _get_broadcast_url(self, match_id: int) -> str:
        now = time.monotonic()
        cached = self._broadcast_url_cache.get(match_id)
        if cached is not None and cached.expires_at > now:
            return cached.url

        lock = self._broadcast_url_locks.setdefault(match_id, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            cached = self._broadcast_url_cache.get(match_id)
            if cached is not None and cached.expires_at > now:
                return cached.url

            if self.broadcast_urls is not None:
                stored = await self.broadcast_urls.get(match_id)
                if stored is not None:
                    remaining = stored.expires_at - time.time()
                    if remaining > 0:
                        self._broadcast_url_cache[match_id] = _CachedBroadcastURL(
                            expires_at=now + remaining,
                            url=stored.url,
                        )
                        return stored.url

            broadcast_url = await self.client.get_live_broadcast_url(match_id)
            expires_at_epoch = time.time() + _BROADCAST_URL_TTL_SECONDS
            self._broadcast_url_cache[match_id] = _CachedBroadcastURL(
                expires_at=now + _BROADCAST_URL_TTL_SECONDS,
                url=broadcast_url,
            )
            if self.broadcast_urls is not None:
                await self.broadcast_urls.put(
                    match_id,
                    broadcast_url,
                    expires_at_epoch,
                )
            return broadcast_url

    async def _live_match_with_retries(self, match_id: int) -> LiveMatchSnapshot:
        error: DeadlockAPIError | None = None
        for attempt in range(3):
            try:
                return await self.live_match(match_id)
            except DeadlockAPIError as exc:
                error = exc
                if attempt < 2:
                    await asyncio.sleep(5 * (attempt + 1))
        assert error is not None
        raise error

    async def _get_profile(
        self,
        account_id: int,
        limit: asyncio.Semaphore,
    ) -> _CachedProfile:
        now = time.monotonic()
        cached = self._profile_cache.get(account_id)
        if cached is not None and cached.expires_at > now:
            return cached
        async with limit:
            rank_result, history_result = await asyncio.gather(
                self._try_get_rank(account_id),
                self._try_get_history(account_id),
            )
        cached = _CachedProfile(
            expires_at=now + 15 * 60,
            rank=rank_result,
            history=history_result,
        )
        self._profile_cache[account_id] = cached
        return cached

    async def _get_experiences(
        self,
        pairs: set[tuple[int, int]],
    ) -> tuple[HeroExperience, ...]:
        now = time.monotonic()
        missing = {
            pair
            for pair in pairs
            if pair not in self._experience_cache
            or self._experience_cache[pair].expires_at <= now
        }
        if missing:
            try:
                fetched = await self.client.get_hero_experience(
                    [account_id for account_id, _ in missing],
                    [hero_id for _, hero_id in missing],
                )
            except DeadlockAPIError:
                fetched = ()
            fetched_by_pair = {
                (item.account_id, item.hero_id): item for item in fetched
            }
            for pair in missing:
                self._experience_cache[pair] = _CachedExperience(
                    expires_at=now + 15 * 60,
                    experience=fetched_by_pair.get(pair),
                )
        return tuple(
            cached.experience
            for pair in pairs
            if (cached := self._experience_cache.get(pair)) is not None
            and cached.experience is not None
        )

    async def _get_rank_assets(self) -> tuple[RankAsset, ...]:
        now = time.monotonic()
        if self._rank_assets and self._rank_assets_expires_at > now:
            return self._rank_assets
        try:
            assets = await self.client.get_rank_assets()
        except DeadlockAPIError:
            return self._rank_assets
        self._rank_assets = assets
        self._rank_assets_expires_at = now + 24 * 60 * 60
        return assets

    async def _get_heroes(
        self,
        hero_ids: list[int] | tuple[int, ...],
    ) -> dict[int, HeroSummary]:
        requested = set(hero_ids)
        if not requested:
            return {}
        now = time.monotonic()
        if self._hero_cache_expires_at > now:
            return {
                hero_id: self._hero_cache[hero_id]
                for hero_id in requested
                if hero_id in self._hero_cache
            }
        async with self._hero_cache_lock:
            now = time.monotonic()
            if self._hero_cache_expires_at <= now:
                try:
                    heroes = await self.client.get_heroes()
                except DeadlockAPIError:
                    heroes = ()
                if heroes:
                    self._hero_cache = {hero.hero_id: hero for hero in heroes}
                    self._hero_cache_expires_at = now + 24 * 60 * 60
        return {
            hero_id: self._hero_cache[hero_id]
            for hero_id in requested
            if hero_id in self._hero_cache
        }

    async def _try_get_rank(self, account_id: int) -> PlayerRank | None:
        try:
            return await self.client.get_player_rank(account_id)
        except DeadlockAPIError:
            return None

    async def _try_get_history(self, account_id: int) -> PlayerHistory | None:
        try:
            return await self.client.get_player_history(account_id)
        except DeadlockAPIError:
            return None


@dataclass(frozen=True, slots=True)
class _CachedProfile:
    expires_at: float
    rank: PlayerRank | None
    history: PlayerHistory | None


@dataclass(frozen=True, slots=True)
class _CachedExperience:
    expires_at: float
    experience: HeroExperience | None


@dataclass(frozen=True, slots=True)
class _CachedBroadcastURL:
    expires_at: float
    url: str
