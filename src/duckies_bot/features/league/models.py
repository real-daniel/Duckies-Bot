"""Typed League of Legends player and match data used by Discord views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class RiotAccount:
    puuid: str
    game_name: str
    tag_line: str

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"


@dataclass(frozen=True, slots=True)
class SummonerProfile:
    level: int
    profile_icon_id: int


@dataclass(frozen=True, slots=True)
class RankedEntry:
    queue_type: str
    tier: str
    rank: str
    league_points: int
    wins: int
    losses: int
    hot_streak: bool = False

    @property
    def games(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.games if self.games else None


@dataclass(frozen=True, slots=True)
class MatchParticipant:
    puuid: str
    riot_id: str
    champion_name: str
    team_id: int
    team_position: str
    win: bool
    kills: int
    deaths: int
    assists: int
    champion_level: int
    gold_earned: int
    minions_killed: int
    neutral_minions_killed: int
    vision_score: int
    wards_placed: int
    wards_killed: int
    control_wards_bought: int
    total_damage_to_champions: int
    physical_damage_to_champions: int
    magic_damage_to_champions: int
    true_damage_to_champions: int
    total_damage_taken: int
    total_heal: int
    damage_to_objectives: int
    damage_to_turrets: int
    turret_kills: int
    inhibitor_kills: int
    total_time_cc_dealt: int
    largest_multi_kill: int
    placement: int | None
    subteam_id: int | None
    item_ids: tuple[int, ...]

    @property
    def creep_score(self) -> int:
        return self.minions_killed + self.neutral_minions_killed

    @property
    def kda(self) -> float:
        return (self.kills + self.assists) / max(self.deaths, 1)


@dataclass(frozen=True, slots=True)
class LeagueMatch:
    match_id: str
    queue_id: int
    game_mode: str
    game_type: str
    game_version: str
    started_at_ms: int
    duration_seconds: int
    participants: tuple[MatchParticipant, ...]

    def participant(self, puuid: str) -> MatchParticipant | None:
        return next((item for item in self.participants if item.puuid == puuid), None)


@dataclass(frozen=True, slots=True)
class LeaguePlayerLookup:
    account: RiotAccount
    platform: str
    summoner: SummonerProfile | None
    ranked_entries: tuple[RankedEntry, ...]
    matches: tuple[LeagueMatch, ...]
    data_dragon_version: str | None = None
    item_names: Mapping[int, str] | None = None

