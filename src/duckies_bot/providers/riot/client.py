"""Asynchronous client for the Riot APIs used by League player lookups."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import aiohttp

from ...features.league.models import (
    LeagueMatch,
    MatchParticipant,
    RankedEntry,
    RiotAccount,
    SummonerProfile,
)
from .errors import InvalidRiotResponseError, RiotAPIError


PLATFORM_REGIONS = (
    "BR1",
    "EUN1",
    "EUW1",
    "JP1",
    "KR",
    "LA1",
    "LA2",
    "ME1",
    "NA1",
    "OC1",
    "PH2",
    "RU",
    "SG2",
    "TH2",
    "TR1",
    "TW2",
    "VN2",
)

_AMERICAS = frozenset({"BR1", "LA1", "LA2", "NA1"})
_EUROPE = frozenset({"EUN1", "EUW1", "ME1", "RU", "TR1"})
_ASIA = frozenset({"JP1", "KR"})
_SEA = frozenset({"OC1", "PH2", "SG2", "TH2", "TW2", "VN2"})


def regional_route(platform: str) -> str:
    platform = platform.strip().upper()
    if platform in _AMERICAS:
        return "americas"
    if platform in _EUROPE:
        return "europe"
    if platform in _ASIA:
        return "asia"
    if platform in _SEA:
        return "sea"
    raise ValueError(f"Unsupported League region: {platform}")


class RiotClient:
    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: float = 30.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.api_key = api_key.strip() if api_key else None
        self.timeout_seconds = timeout_seconds
        self._session = session
        self._owns_session = session is None

    async def get_account(
        self,
        game_name: str,
        tag_line: str,
        platform: str,
    ) -> RiotAccount:
        route = regional_route(platform)
        document = await self._riot_json(
            route,
            f"/riot/account/v1/accounts/by-riot-id/{_path(game_name)}/{_path(tag_line)}",
            not_found="No Riot account was found with that Riot ID in the selected region.",
        )
        if not isinstance(document, Mapping):
            raise InvalidRiotResponseError()
        puuid = _text(document.get("puuid"))
        returned_name = _text(document.get("gameName")) or game_name
        returned_tag = _text(document.get("tagLine")) or tag_line
        if puuid is None:
            raise InvalidRiotResponseError()
        return RiotAccount(puuid=puuid, game_name=returned_name, tag_line=returned_tag)

    async def get_summoner(self, puuid: str, platform: str) -> SummonerProfile:
        document = await self._riot_json(
            _platform(platform),
            f"/lol/summoner/v4/summoners/by-puuid/{_path(puuid)}",
            not_found="That Riot account does not have a League profile in this region.",
        )
        if not isinstance(document, Mapping):
            raise InvalidRiotResponseError()
        return SummonerProfile(
            level=_integer(document.get("summonerLevel")),
            profile_icon_id=_integer(document.get("profileIconId")),
        )

    async def get_ranked_entries(
        self,
        puuid: str,
        platform: str,
    ) -> tuple[RankedEntry, ...]:
        document = await self._riot_json(
            _platform(platform),
            f"/lol/league/v4/entries/by-puuid/{_path(puuid)}",
        )
        if not isinstance(document, list):
            raise InvalidRiotResponseError()
        entries: list[RankedEntry] = []
        for raw in document:
            if not isinstance(raw, Mapping):
                continue
            queue_type = _text(raw.get("queueType"))
            tier = _text(raw.get("tier"))
            rank = _text(raw.get("rank"))
            if queue_type is None or tier is None or rank is None:
                continue
            entries.append(
                RankedEntry(
                    queue_type=queue_type,
                    tier=tier,
                    rank=rank,
                    league_points=_integer(raw.get("leaguePoints")),
                    wins=_integer(raw.get("wins")),
                    losses=_integer(raw.get("losses")),
                    hot_streak=raw.get("hotStreak") is True,
                )
            )
        return tuple(sorted(entries, key=_ranked_sort_key))

    async def get_match_ids(
        self,
        puuid: str,
        platform: str,
        *,
        count: int = 10,
    ) -> tuple[str, ...]:
        if not 1 <= count <= 100:
            raise ValueError("count must be between 1 and 100")
        document = await self._riot_json(
            regional_route(platform),
            f"/lol/match/v5/matches/by-puuid/{_path(puuid)}/ids",
            params={"start": "0", "count": str(count)},
        )
        if not isinstance(document, list):
            raise InvalidRiotResponseError()
        return tuple(item for item in document if isinstance(item, str) and item)

    async def get_match(self, match_id: str, platform: str) -> LeagueMatch:
        document = await self._riot_json(
            regional_route(platform),
            f"/lol/match/v5/matches/{_path(match_id)}",
            not_found="That match is no longer available from Riot.",
        )
        if not isinstance(document, Mapping):
            raise InvalidRiotResponseError()
        return _parse_match(document, fallback_match_id=match_id)

    async def get_data_dragon_catalog(self) -> tuple[str | None, dict[int, str]]:
        """Return the current Data Dragon version and item names without using the API key."""
        try:
            versions = await self._public_json(
                "https://ddragon.leagueoflegends.com/api/versions.json"
            )
            if not isinstance(versions, list) or not versions or not isinstance(versions[0], str):
                return None, {}
            version = versions[0]
            document = await self._public_json(
                f"https://ddragon.leagueoflegends.com/cdn/{_path(version)}/data/en_US/item.json"
            )
            if not isinstance(document, Mapping) or not isinstance(document.get("data"), Mapping):
                return version, {}
            names: dict[int, str] = {}
            for raw_id, raw in document["data"].items():
                if not isinstance(raw_id, str) or not isinstance(raw, Mapping):
                    continue
                name = _text(raw.get("name"))
                if raw_id.isdigit() and name is not None:
                    names[int(raw_id)] = name
            return version, names
        except RiotAPIError:
            return None, {}

    async def _riot_json(
        self,
        route: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        not_found: str = "Riot could not find the requested League data.",
    ) -> Any:
        if not self.api_key:
            raise RiotAPIError(
                "League lookups are not configured. Add RIOT_API_KEY to the bot environment."
            )
        return await self._get_json(
            f"https://{route}.api.riotgames.com{path}",
            params=params,
            headers={"Accept": "application/json", "X-Riot-Token": self.api_key},
            not_found=not_found,
        )

    async def _public_json(self, url: str) -> Any:
        return await self._get_json(
            url,
            headers={"Accept": "application/json"},
            not_found="Riot's static League data is temporarily unavailable.",
        )

    async def _get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        not_found: str,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        session = await self._get_session()
        try:
            async with session.get(
                url,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            ) as response:
                if response.status == 401:
                    raise RiotAPIError("Riot rejected the API key. Check or renew RIOT_API_KEY.")
                if response.status == 403:
                    raise RiotAPIError(
                        "Riot denied this lookup. The API key may be expired or lack access."
                    )
                if response.status == 404:
                    raise RiotAPIError(not_found)
                if response.status == 429:
                    retry_after = response.headers.get("Retry-After")
                    detail = (
                        f" Try again in {retry_after} seconds."
                        if retry_after
                        else " Try again shortly."
                    )
                    raise RiotAPIError(f"The Riot API rate limit was reached.{detail}")
                if response.status >= 500:
                    raise RiotAPIError("The Riot API is temporarily unavailable.")
                if response.status >= 400:
                    raise RiotAPIError("Riot rejected that League lookup.")
                try:
                    return await response.json()
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise InvalidRiotResponseError() from exc
        except TimeoutError as exc:
            raise RiotAPIError("The Riot API lookup timed out.") from exc
        except aiohttp.ClientError as exc:
            raise RiotAPIError("Could not reach the Riot API.") from exc

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()


def _parse_match(document: Mapping[str, Any], *, fallback_match_id: str) -> LeagueMatch:
    metadata = document.get("metadata")
    info = document.get("info")
    if not isinstance(info, Mapping):
        raise InvalidRiotResponseError()
    match_id = fallback_match_id
    if isinstance(metadata, Mapping):
        match_id = _text(metadata.get("matchId")) or match_id
    raw_participants = info.get("participants")
    if not isinstance(raw_participants, list):
        raise InvalidRiotResponseError()
    participants = tuple(
        _parse_participant(raw)
        for raw in raw_participants
        if isinstance(raw, Mapping)
    )
    if not participants:
        raise InvalidRiotResponseError()
    duration = _integer(info.get("gameDuration"))
    # Some older records represent duration in milliseconds.
    if duration > 100_000:
        duration //= 1000
    return LeagueMatch(
        match_id=match_id,
        queue_id=_integer(info.get("queueId")),
        game_mode=_text(info.get("gameMode")) or "Unknown",
        game_type=_text(info.get("gameType")) or "Unknown",
        game_version=_text(info.get("gameVersion")) or "Unknown",
        started_at_ms=_integer(info.get("gameStartTimestamp")),
        duration_seconds=duration,
        participants=participants,
    )


def _parse_participant(raw: Mapping[str, Any]) -> MatchParticipant:
    game_name = _text(raw.get("riotIdGameName")) or _text(raw.get("summonerName")) or "Unknown"
    tag_line = _text(raw.get("riotIdTagline")) or _text(raw.get("riotIdTagLine"))
    riot_id = f"{game_name}#{tag_line}" if tag_line else game_name
    return MatchParticipant(
        puuid=_text(raw.get("puuid")) or "",
        riot_id=riot_id,
        champion_name=_text(raw.get("championName")) or "Unknown",
        team_id=_integer(raw.get("teamId")),
        team_position=_text(raw.get("teamPosition")) or _text(raw.get("individualPosition")) or "",
        win=raw.get("win") is True,
        kills=_integer(raw.get("kills")),
        deaths=_integer(raw.get("deaths")),
        assists=_integer(raw.get("assists")),
        champion_level=_integer(raw.get("champLevel")),
        gold_earned=_integer(raw.get("goldEarned")),
        minions_killed=_integer(raw.get("totalMinionsKilled")),
        neutral_minions_killed=_integer(raw.get("neutralMinionsKilled")),
        vision_score=_integer(raw.get("visionScore")),
        wards_placed=_integer(raw.get("wardsPlaced")),
        wards_killed=_integer(raw.get("wardsKilled")),
        control_wards_bought=_integer(raw.get("visionWardsBoughtInGame")),
        total_damage_to_champions=_integer(raw.get("totalDamageDealtToChampions")),
        physical_damage_to_champions=_integer(raw.get("physicalDamageDealtToChampions")),
        magic_damage_to_champions=_integer(raw.get("magicDamageDealtToChampions")),
        true_damage_to_champions=_integer(raw.get("trueDamageDealtToChampions")),
        total_damage_taken=_integer(raw.get("totalDamageTaken")),
        total_heal=_integer(raw.get("totalHeal")),
        damage_to_objectives=_integer(raw.get("damageDealtToObjectives")),
        damage_to_turrets=_integer(raw.get("damageDealtToTurrets")),
        turret_kills=_integer(raw.get("turretKills")),
        inhibitor_kills=_integer(raw.get("inhibitorKills")),
        total_time_cc_dealt=_integer(raw.get("totalTimeCCDealt")),
        largest_multi_kill=_integer(raw.get("largestMultiKill")),
        placement=_optional_integer(raw.get("placement")),
        subteam_id=_optional_integer(raw.get("playerSubteamId")),
        item_ids=tuple(
            item_id
            for index in range(7)
            for item_id in (_integer(raw.get(f"item{index}")),)
            if item_id > 0
        ),
    )


def _ranked_sort_key(entry: RankedEntry) -> tuple[int, str]:
    order = {"RANKED_SOLO_5x5": 0, "RANKED_FLEX_SR": 1}
    return order.get(entry.queue_type, 2), entry.queue_type


def _platform(value: str) -> str:
    value = value.strip().upper()
    if value not in PLATFORM_REGIONS:
        raise ValueError(f"Unsupported League region: {value}")
    return value.lower()


def _path(value: str) -> str:
    return quote(value, safe="")


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _integer(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _optional_integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None

