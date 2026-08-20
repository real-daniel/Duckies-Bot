"""Deadlock command orchestration."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

from .models import (
    HeroExperience,
    HeroRecord,
    HeroSummary,
    ItemSummary,
    LiveChatMessage,
    LiveMatchSnapshot,
    MatchScout,
    PlayerHistory,
    PlayerRank,
    RankAsset,
    ScoutedPlayer,
)
from ...providers.deadlock import (
    DeadlockAPIError,
    DeadlockClient,
    DeadlockLiveClient,
    LiveDemoUnavailableError,
)
from ...storage import BroadcastURLRepository


_BROADCAST_URL_TTL_SECONDS = 15 * 60
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
        self._total_matches_cache: dict[int, _CachedTotalMatches] = {}
        self._hero_cache: dict[int, HeroSummary] = {}
        self._hero_cache_expires_at = 0.0
        self._hero_cache_lock = asyncio.Lock()
        self._hero_icon_cache: dict[int, _CachedAssetIcon] = {}
        self._hero_icon_locks: dict[int, asyncio.Lock] = {}
        self._item_cache: dict[int, ItemSummary] = {}
        self._item_cache_expires_at = 0.0
        self._item_cache_lock = asyncio.Lock()
        self._item_icon_cache: dict[int, _CachedAssetIcon] = {}
        self._item_icon_locks: dict[int, asyncio.Lock] = {}
        self._asset_icon_limit = asyncio.Semaphore(8)
        self._rank_assets: tuple[RankAsset, ...] = ()
        self._rank_assets_expires_at = 0.0
        self._broadcast_url_cache: dict[int, _CachedBroadcastURL] = {}
        self._broadcast_url_locks: dict[int, asyncio.Lock] = {}
        self._active_match_ids: tuple[int, ...] = ()
        self._active_match_ids_expires_at = 0.0
        self._active_match_ids_lock = asyncio.Lock()

    async def active_match_id(self, account_id: int) -> int | None:
        """Find a linked account's match without fetching unused hero assets."""
        match = await self.client.get_active_match(account_id)
        return match.match_id if match is not None else None

    async def random_top_200_match_id(self) -> int:
        now = time.monotonic()
        if self._active_match_ids_expires_at <= now:
            async with self._active_match_ids_lock:
                now = time.monotonic()
                if self._active_match_ids_expires_at <= now:
                    matches = await self.client.get_active_matches()
                    self._active_match_ids = tuple(
                        match.match_id
                        for match in matches
                        if match.match_id > 0 and len(match.players) >= 10
                    )
                    self._active_match_ids_expires_at = (
                        now + _ACTIVE_MATCHES_TTL_SECONDS
                    )
        if not self._active_match_ids:
            raise DeadlockAPIError(
                "Deadlock's top-200 Watch feed does not currently contain a "
                "standard 6v6 match."
            )
        return random.choice(self._active_match_ids)

    async def live_match(self, match_id: int) -> LiveMatchSnapshot:
        if self.live_client is None:
            raise DeadlockAPIError("The Deadlock live parser is not configured.")
        broadcast_url = await self._get_broadcast_url(match_id)
        snapshot = await self.live_client.get_match_players(match_id, broadcast_url)
        return await self._enrich_live_snapshot(snapshot)

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
        profiles, experience_data, rank_assets = await asyncio.gather(
            profiles_task,
            experiences_task,
            rank_assets_task,
        )
        experiences, total_matches_by_account, all_experiences = experience_data
        top_experiences_by_account: dict[int, tuple[HeroExperience, ...]] = {}
        top_hero_ids: set[int] = set()
        for account_id in {player.account_id for player in snapshot.players}:
            top = tuple(
                sorted(
                    (
                        item
                        for item in all_experiences
                        if item.account_id == account_id and item.matches_played > 0
                    ),
                    key=lambda item: (-item.matches_played, -item.wins, item.hero_id),
                )[:5]
            )
            top_experiences_by_account[account_id] = top
            top_hero_ids.update(item.hero_id for item in top)
        top_hero_assets = await self._get_heroes(tuple(top_hero_ids))
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
                    total_matches=total_matches_by_account.get(player.account_id),
                    top_heroes=tuple(
                        HeroRecord(
                            experience=item,
                            hero=top_hero_assets.get(item.hero_id),
                        )
                        for item in top_experiences_by_account.get(player.account_id, ())
                    ),
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

    async def stream_live_match(
        self,
        match_id: int,
    ) -> AsyncIterator[LiveMatchSnapshot]:
        if self.live_client is None:
            raise DeadlockAPIError("The Deadlock live parser is not configured.")
        for attempt in range(2):
            broadcast_url = await self._get_broadcast_url(match_id)
            try:
                async for snapshot in self.live_client.stream_match_snapshots(
                    match_id,
                    broadcast_url,
                ):
                    yield await self._enrich_live_snapshot(snapshot)
                return
            except LiveDemoUnavailableError:
                await self.invalidate_broadcast_url(match_id)
                if attempt > 0:
                    raise

    async def invalidate_broadcast_url(self, match_id: int) -> None:
        """Discard a broadcast URL that Valve's CDN could not serve."""

        lock = self._broadcast_url_locks.setdefault(match_id, asyncio.Lock())
        async with lock:
            self._broadcast_url_cache.pop(match_id, None)
            if self.broadcast_urls is not None:
                await self.broadcast_urls.delete(match_id)

    async def scoreboard_item_icons(
        self,
        snapshot: LiveMatchSnapshot,
    ) -> dict[int, bytes]:
        """Return cached icon bytes for the shop items currently on the scoreboard."""
        items = tuple(item for item in snapshot.items if item.icon_url is not None)
        if not items:
            return {}
        resolved = await asyncio.gather(
            *(self._get_item_icon(item) for item in items)
        )
        return {
            item.item_id: payload
            for item, payload in zip(items, resolved, strict=True)
            if payload
        }

    async def scoreboard_hero_icons(
        self,
        snapshot: LiveMatchSnapshot,
    ) -> dict[int, bytes]:
        """Return cached icon bytes for the heroes currently on the scoreboard."""
        heroes = tuple(hero for hero in snapshot.heroes if hero.icon_url is not None)
        if not heroes:
            return {}
        resolved = await asyncio.gather(
            *(
                self._get_asset_icon(
                    hero.hero_id,
                    hero.icon_url,
                    self._hero_icon_cache,
                    self._hero_icon_locks,
                )
                for hero in heroes
            )
        )
        return {
            hero.hero_id: payload
            for hero, payload in zip(heroes, resolved, strict=True)
            if payload
        }

    async def _enrich_live_snapshot(
        self,
        snapshot: LiveMatchSnapshot,
    ) -> LiveMatchSnapshot:
        hero_ids = sorted(
            {player.hero_id for player in snapshot.players if player.hero_id is not None}
        )
        item_ids = sorted(
            {item_id for player in snapshot.players for item_id in player.upgrades}
        )
        heroes_by_id, items_by_id = await asyncio.gather(
            self._get_heroes(hero_ids),
            self._get_items(item_ids),
        )
        return replace(
            snapshot,
            heroes=tuple(
                heroes_by_id[hero_id]
                for hero_id in hero_ids
                if hero_id in heroes_by_id
            ),
            items=tuple(
                items_by_id[item_id]
                for item_id in item_ids
                if item_id in items_by_id
            ),
        )

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
    ) -> tuple[
        tuple[HeroExperience, ...],
        dict[int, int],
        tuple[HeroExperience, ...],
    ]:
        now = time.monotonic()
        account_ids = {account_id for account_id, _ in pairs}
        missing_accounts = {
            account_id
            for account_id in account_ids
            if account_id not in self._total_matches_cache
            or self._total_matches_cache[account_id].expires_at <= now
        }
        if missing_accounts:
            try:
                fetched = await self.client.get_hero_experience(
                    sorted(missing_accounts),
                )
            except DeadlockAPIError:
                fetched = None
            if fetched is not None:
                fetched_by_pair = {
                    (item.account_id, item.hero_id): item for item in fetched
                }
                totals = {account_id: 0 for account_id in missing_accounts}
                for item in fetched:
                    if item.account_id in totals:
                        totals[item.account_id] += item.matches_played
                    self._experience_cache[(item.account_id, item.hero_id)] = _CachedExperience(
                        expires_at=now + 15 * 60,
                        experience=item,
                    )
                for account_id, total in totals.items():
                    self._total_matches_cache[account_id] = _CachedTotalMatches(
                        expires_at=now + 15 * 60,
                        matches=total,
                    )
                for pair in pairs:
                    if pair[0] not in missing_accounts:
                        continue
                    self._experience_cache[pair] = _CachedExperience(
                        expires_at=now + 15 * 60,
                        experience=fetched_by_pair.get(pair),
                    )
        experiences = tuple(
            cached.experience
            for pair in pairs
            if (cached := self._experience_cache.get(pair)) is not None
            and cached.experience is not None
        )
        totals = {
            account_id: cached.matches
            for account_id in account_ids
            if (cached := self._total_matches_cache.get(account_id)) is not None
        }
        all_experiences = tuple(
            cached.experience
            for (account_id, _), cached in self._experience_cache.items()
            if account_id in account_ids
            and cached.expires_at > now
            and cached.experience is not None
        )
        return experiences, totals, all_experiences

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

    async def _get_items(
        self,
        item_ids: list[int] | tuple[int, ...],
    ) -> dict[int, ItemSummary]:
        requested = set(item_ids)
        if not requested:
            return {}
        now = time.monotonic()
        if self._item_cache_expires_at <= now:
            async with self._item_cache_lock:
                now = time.monotonic()
                if self._item_cache_expires_at <= now:
                    try:
                        items = await self.client.get_items()
                    except DeadlockAPIError:
                        items = ()
                    if items:
                        self._item_cache = {
                            item.item_id: item
                            for item in items
                            if item.shopable
                            and item.icon_url is not None
                            and item.slot_type in {"weapon", "vitality", "spirit"}
                        }
                        self._item_cache_expires_at = now + 24 * 60 * 60
        return {
            item_id: self._item_cache[item_id]
            for item_id in requested
            if item_id in self._item_cache
        }

    async def _get_item_icon(self, item: ItemSummary) -> bytes:
        return await self._get_asset_icon(
            item.item_id,
            item.icon_url,
            self._item_icon_cache,
            self._item_icon_locks,
        )

    async def _get_asset_icon(
        self,
        asset_id: int,
        icon_url: str | None,
        cache: dict[int, _CachedAssetIcon],
        locks: dict[int, asyncio.Lock],
    ) -> bytes:
        now = time.monotonic()
        cached = cache.get(asset_id)
        if cached is not None and cached.expires_at > now:
            return cached.payload
        lock = locks.setdefault(asset_id, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            cached = cache.get(asset_id)
            if cached is not None and cached.expires_at > now:
                return cached.payload
            payload = b""
            if icon_url is not None:
                try:
                    async with self._asset_icon_limit:
                        payload = await self.client.get_asset_bytes(icon_url)
                except (DeadlockAPIError, ValueError):
                    payload = b""
            cache[asset_id] = _CachedAssetIcon(
                expires_at=now + (24 * 60 * 60 if payload else 5 * 60),
                payload=payload,
            )
            return payload

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
class _CachedTotalMatches:
    expires_at: float
    matches: int


@dataclass(frozen=True, slots=True)
class _CachedAssetIcon:
    expires_at: float
    payload: bytes


@dataclass(frozen=True, slots=True)
class _CachedBroadcastURL:
    expires_at: float
    url: str
