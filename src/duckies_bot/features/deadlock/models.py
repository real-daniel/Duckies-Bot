"""Typed data used by Deadlock commands."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActivePlayer:
    account_id: int
    hero_id: int | None
    team: int | None
    team_name: str | None
    abandoned: bool


@dataclass(frozen=True, slots=True)
class ActiveMatch:
    match_id: int
    duration_seconds: int | None
    game_mode: str | None
    match_mode: str | None
    region: str | None
    lobby_id: int | None
    match_score: int | None
    net_worth_team_0: int | None
    net_worth_team_1: int | None
    spectators: int | None
    open_spectator_slots: int | None
    players: tuple[ActivePlayer, ...]

    def player(self, account_id: int) -> ActivePlayer | None:
        return next((player for player in self.players if player.account_id == account_id), None)


@dataclass(frozen=True, slots=True)
class HeroSummary:
    hero_id: int
    name: str
    icon_url: str | None


@dataclass(frozen=True, slots=True)
class LiveLookup:
    match: ActiveMatch
    player: ActivePlayer
    hero: HeroSummary | None


@dataclass(frozen=True, slots=True)
class LivePlayer:
    account_id: int
    steam_name: str
    hero_id: int | None
    team: int | None
    player_slot: int | None
    kills: int
    deaths: int
    assists: int
    net_worth: int


@dataclass(frozen=True, slots=True)
class LiveChatMessage:
    account_id: int | None
    steam_name: str
    text: str
    game_time_seconds: float | None
    all_chat: bool | None


@dataclass(frozen=True, slots=True)
class LiveMatchSnapshot:
    match_id: int
    game_time_seconds: float | None
    players: tuple[LivePlayer, ...]
    heroes: tuple[HeroSummary, ...] = ()

    def hero(self, hero_id: int | None) -> HeroSummary | None:
        if hero_id is None:
            return None
        return next((hero for hero in self.heroes if hero.hero_id == hero_id), None)


@dataclass(frozen=True, slots=True)
class PlayerRank:
    tier: int
    subrank: int


@dataclass(frozen=True, slots=True)
class RankAsset:
    tier: int
    name: str


@dataclass(frozen=True, slots=True)
class HeroExperience:
    account_id: int
    hero_id: int
    matches_played: int
    wins: int
    last_played: int

    @property
    def win_rate(self) -> float | None:
        if self.matches_played <= 0:
            return None
        return self.wins / self.matches_played


@dataclass(frozen=True, slots=True)
class PlayerHistory:
    account_id: int
    outcomes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScoutedPlayer:
    player: LivePlayer
    hero: HeroSummary | None
    rank: PlayerRank | None
    rank_name: str | None
    experience: HeroExperience | None
    recent_outcomes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MatchScout:
    match_id: int
    game_time_seconds: float | None
    players: tuple[ScoutedPlayer, ...]
