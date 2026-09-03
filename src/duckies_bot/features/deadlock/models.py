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
class ItemSummary:
    item_id: int
    name: str
    icon_url: str | None
    slot_type: str | None
    tier: int | None
    cost: int | None
    shopable: bool


@dataclass(frozen=True, slots=True)
class StatueBuff:
    """One permanent golden-statue modifier observed in the live demo."""

    stat: str
    tier: int
    modifier_subclass: int
    serial_number: int | None = None
    entry_id: int | None = None


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
    assigned_lane: int | None = None
    denies: int = 0
    last_hits: int = 0
    hero_healing: int = 0
    self_healing: int = 0
    hero_damage: int = 0
    objective_damage: int = 0
    health_regen: float | None = None
    ultimate_trained: bool | None = None
    ultimate_cooldown_end: float | None = None
    upgrades: tuple[int, ...] = ()
    statue_buffs: tuple[StatueBuff, ...] = ()

    @property
    def statue_buff_count(self) -> int:
        return len(self.statue_buffs)

    def statue_tiers(self, stat: str) -> tuple[int, int, int]:
        """Return this player's tier 1/2/3 pickup counts for one stat family."""
        counts = tuple(
            sum(buff.stat == stat and buff.tier == tier for buff in self.statue_buffs)
            for tier in (1, 2, 3)
        )
        return counts[0], counts[1], counts[2]


@dataclass(frozen=True, slots=True)
class LiveChatMessage:
    account_id: int | None
    steam_name: str
    text: str
    game_time_seconds: float | None
    all_chat: bool | None


@dataclass(frozen=True, slots=True)
class LiveKillEvent:
    tick: int | None
    game_time_seconds: float | None
    attacker_account_id: int | None
    victim_account_id: int | None
    assister_account_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveMatchSnapshot:
    match_id: int
    game_time_seconds: float | None
    players: tuple[LivePlayer, ...]
    heroes: tuple[HeroSummary, ...] = ()
    items: tuple[ItemSummary, ...] = ()
    kill_events: tuple[LiveKillEvent, ...] = ()

    def hero(self, hero_id: int | None) -> HeroSummary | None:
        if hero_id is None:
            return None
        return next((hero for hero in self.heroes if hero.hero_id == hero_id), None)

    def item(self, item_id: int) -> ItemSummary | None:
        return next((item for item in self.items if item.item_id == item_id), None)

    def inventory(self, player: LivePlayer) -> tuple[ItemSummary, ...]:
        return tuple(
            item
            for item_id in player.upgrades
            if (item := self.item(item_id)) is not None and item.shopable
        )


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
class HeroRecord:
    experience: HeroExperience
    hero: HeroSummary | None


@dataclass(frozen=True, slots=True)
class ScoutedPlayer:
    player: LivePlayer
    hero: HeroSummary | None
    rank: PlayerRank | None
    rank_name: str | None
    experience: HeroExperience | None
    recent_outcomes: tuple[str, ...]
    total_matches: int | None = None
    top_heroes: tuple[HeroRecord, ...] = ()

    @property
    def hero_match_share(self) -> float | None:
        if (
            self.experience is None
            or self.total_matches is None
            or self.total_matches <= 0
        ):
            return None
        return self.experience.matches_played / self.total_matches


@dataclass(frozen=True, slots=True)
class MatchScout:
    match_id: int
    game_time_seconds: float | None
    players: tuple[ScoutedPlayer, ...]
