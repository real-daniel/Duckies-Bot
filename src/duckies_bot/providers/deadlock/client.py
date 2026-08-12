"""Small asynchronous client for the Deadlock endpoints used by the bot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import aiohttp

from ...features.deadlock.models import (
    ActiveMatch,
    ActivePlayer,
    HeroExperience,
    HeroSummary,
    PlayerHistory,
    PlayerRank,
    RankAsset,
)
from .errors import DeadlockAPIError, InvalidDeadlockResponseError


class DeadlockClient:
    def __init__(
        self,
        base_url: str = "https://api.deadlock-api.com",
        timeout_seconds: float = 30.0,
        api_key: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key.strip() if api_key else None
        self._session = session
        self._owns_session = session is None

    async def get_active_match(self, account_id: int) -> ActiveMatch | None:
        matches = await self.get_active_matches((account_id,))
        return next(
            (match for match in matches if match.player(account_id) is not None),
            None,
        )

    async def get_active_matches(
        self,
        account_ids: Sequence[int] | None = None,
    ) -> tuple[ActiveMatch, ...]:
        params = None
        if account_ids:
            params = {"account_ids": ",".join(str(value) for value in account_ids)}
        document = await self._get_json(
            "/v1/matches/active",
            params=params,
        )
        if not isinstance(document, list):
            raise InvalidDeadlockResponseError()
        matches: list[ActiveMatch] = []
        for raw in document:
            if not isinstance(raw, Mapping):
                continue
            matches.append(_parse_active_match(raw))
        return tuple(matches)

    async def get_live_broadcast_url(self, match_id: int) -> str:
        if match_id <= 0:
            raise ValueError("match_id must be positive")
        document = await self._get_json(f"/v1/matches/{match_id}/live/url")
        if not isinstance(document, Mapping):
            raise InvalidDeadlockResponseError()
        broadcast_url = _optional_str(document.get("broadcast_url"))
        if broadcast_url is None or not broadcast_url.startswith(("https://", "http://")):
            raise InvalidDeadlockResponseError()
        return broadcast_url.rstrip("/")

    async def get_hero(self, hero_id: int) -> HeroSummary:
        document = await self._get_json(f"/v1/assets/heroes/{hero_id}")
        if not isinstance(document, Mapping):
            raise InvalidDeadlockResponseError()
        return _parse_hero(document)

    async def get_heroes(self) -> tuple[HeroSummary, ...]:
        document = await self._get_json("/v1/assets/heroes")
        if not isinstance(document, list):
            raise InvalidDeadlockResponseError()
        heroes: list[HeroSummary] = []
        for raw in document:
            if isinstance(raw, Mapping):
                heroes.append(_parse_hero(raw))
        return tuple(heroes)

    async def get_player_rank(self, account_id: int) -> PlayerRank:
        document = await self._get_json(f"/v1/players/{account_id}/rank")
        if not isinstance(document, Mapping):
            raise InvalidDeadlockResponseError()
        tier = _optional_int(document.get("rank"))
        subrank = _optional_int(document.get("subrank"))
        if tier is None or subrank is None:
            raise InvalidDeadlockResponseError()
        return PlayerRank(tier=tier, subrank=subrank)

    async def get_player_history(
        self,
        account_id: int,
        *,
        outcome_limit: int = 5,
    ) -> PlayerHistory:
        document = await self._get_json(f"/v1/players/{account_id}/match-history")
        if not isinstance(document, list):
            raise InvalidDeadlockResponseError()
        ordered = sorted(
            (raw for raw in document if isinstance(raw, Mapping)),
            key=lambda raw: _optional_int(raw.get("start_time")) or 0,
            reverse=True,
        )
        outcomes: list[str] = []
        for raw in ordered:
            outcome = _optional_int(raw.get("player_match_outcome"))
            if outcome == 1:
                outcomes.append("W")
            elif outcome == 2:
                outcomes.append("L")
            if len(outcomes) >= outcome_limit:
                break
        return PlayerHistory(account_id=account_id, outcomes=tuple(outcomes))

    async def get_hero_experience(
        self,
        account_ids: Sequence[int],
        hero_ids: Sequence[int],
    ) -> tuple[HeroExperience, ...]:
        if not account_ids or not hero_ids:
            return ()
        params = [*(('account_ids', str(value)) for value in sorted(set(account_ids)))]
        params.append(("hero_ids", ",".join(str(value) for value in sorted(set(hero_ids)))))
        document = await self._get_json("/v1/players/hero-stats", params=params)
        if not isinstance(document, list):
            raise InvalidDeadlockResponseError()
        results: list[HeroExperience] = []
        for raw in document:
            if not isinstance(raw, Mapping):
                continue
            account_id = _optional_int(raw.get("account_id"))
            hero_id = _optional_int(raw.get("hero_id"))
            matches = _optional_int(raw.get("matches_played"))
            wins = _optional_int(raw.get("wins"))
            last_played = _optional_int(raw.get("last_played"))
            if None in (account_id, hero_id, matches, wins, last_played):
                continue
            results.append(
                HeroExperience(
                    account_id=account_id,
                    hero_id=hero_id,
                    matches_played=matches,
                    wins=wins,
                    last_played=last_played,
                )
            )
        return tuple(results)

    async def get_rank_assets(self) -> tuple[RankAsset, ...]:
        document = await self._get_json("/v1/assets/ranks")
        if not isinstance(document, list):
            raise InvalidDeadlockResponseError()
        results: list[RankAsset] = []
        for raw in document:
            if not isinstance(raw, Mapping):
                continue
            tier = _optional_int(raw.get("tier"))
            name = _optional_str(raw.get("name"))
            if tier is not None and name is not None:
                results.append(RankAsset(tier=tier, name=name))
        return tuple(results)

    async def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> Any:
        session = await self._get_session()
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            async with session.get(
                f"{self.base_url}{path}",
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            ) as response:
                if response.status == 429:
                    raise DeadlockAPIError("The Deadlock API is rate-limiting requests. Try again shortly.")
                if response.status >= 500:
                    raise DeadlockAPIError("The Deadlock API is temporarily unavailable.")
                if response.status >= 400:
                    raise DeadlockAPIError("The Deadlock API rejected that lookup.")
                try:
                    return await response.json()
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise InvalidDeadlockResponseError() from exc
        except TimeoutError as exc:
            raise DeadlockAPIError("The Deadlock API lookup timed out.") from exc
        except aiohttp.ClientError as exc:
            raise DeadlockAPIError("Could not reach the Deadlock API.") from exc

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()


def _parse_hero(document: Mapping[str, Any]) -> HeroSummary:
    name = document.get("name")
    returned_id = _optional_int(document.get("id"))
    if not isinstance(name, str) or returned_id is None:
        raise InvalidDeadlockResponseError()
    images = document.get("images")
    icon_url = None
    if isinstance(images, Mapping):
        icon_url = _optional_str(images.get("icon_image_small_webp")) or _optional_str(
            images.get("icon_image_small")
        )
    return HeroSummary(returned_id, name, icon_url)


def _parse_active_match(raw: Mapping[str, Any]) -> ActiveMatch:
    match_id = _optional_int(raw.get("match_id"))
    if match_id is None:
        raise InvalidDeadlockResponseError()
    raw_players = raw.get("players")
    if not isinstance(raw_players, list):
        raise InvalidDeadlockResponseError()
    players: list[ActivePlayer] = []
    for raw_player in raw_players:
        if not isinstance(raw_player, Mapping):
            continue
        account_id = _optional_int(raw_player.get("account_id"))
        if account_id is None:
            continue
        players.append(
            ActivePlayer(
                account_id=account_id,
                hero_id=_optional_int(raw_player.get("hero_id")),
                team=_optional_int(raw_player.get("team")),
                team_name=_optional_str(raw_player.get("team_parsed")),
                abandoned=raw_player.get("abandoned") is True,
            )
        )
    return ActiveMatch(
        match_id=match_id,
        duration_seconds=_optional_int(raw.get("duration_s")),
        game_mode=_optional_str(raw.get("game_mode_parsed")),
        match_mode=_optional_str(raw.get("match_mode_parsed")),
        region=_optional_str(raw.get("region_mode_parsed")),
        lobby_id=_optional_int(raw.get("lobby_id")),
        match_score=_optional_int(raw.get("match_score")),
        net_worth_team_0=_optional_int(raw.get("net_worth_team_0")),
        net_worth_team_1=_optional_int(raw.get("net_worth_team_1")),
        spectators=_optional_int(raw.get("spectators")),
        open_spectator_slots=_optional_int(raw.get("open_spectator_slots")),
        players=tuple(players),
    )


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
