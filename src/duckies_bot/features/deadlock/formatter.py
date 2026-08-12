"""Discord presentation for Deadlock lookups."""

from __future__ import annotations

import discord

from ..accounts.steam import STEAM_ID64_OFFSET
from .models import LiveLookup, LiveMatchSnapshot, LivePlayer, MatchScout, ScoutedPlayer
from ...presentation import field_name, finish_embed, make_embed


def build_live_embed(display_name: str, lookup: LiveLookup) -> discord.Embed:
    match = lookup.match
    player = lookup.player
    hero_name = lookup.hero.name if lookup.hero else (
        f"Hero {player.hero_id}" if player.hero_id is not None else "Unknown"
    )
    embed = make_embed(
        f"{display_name} is in a Deadlock match",
        f"Match `{match.match_id}` is currently listed in the active Watch feed.",
        tone="success",
    )
    embed.add_field(name=field_name("Hero"), value=hero_name, inline=True)
    embed.add_field(
        name=field_name("Team"),
        value=_clean_enum(player.team_name) or (
            str(player.team) if player.team is not None else "Unknown"
        ),
        inline=True,
    )
    embed.add_field(
        name=field_name("Elapsed"),
        value=_duration(match.duration_seconds),
        inline=True,
    )
    embed.add_field(
        name=field_name("Mode"),
        value=_clean_enum(match.game_mode) or _clean_enum(match.match_mode) or "Unknown",
        inline=True,
    )
    embed.add_field(
        name=field_name("Region"),
        value=_clean_enum(match.region) or "Unknown",
        inline=True,
    )
    embed.add_field(
        name=field_name("Players"),
        value=str(len(match.players)),
        inline=True,
    )
    if match.net_worth_team_0 is not None or match.net_worth_team_1 is not None:
        embed.add_field(
            name=field_name("Team net worth"),
            value=(
                f"Team 0: {_number(match.net_worth_team_0)}\n"
                f"Team 1: {_number(match.net_worth_team_1)}"
            ),
            inline=True,
        )
    if match.match_score is not None:
        embed.add_field(name=field_name("Match score"), value=str(match.match_score), inline=True)
    if match.spectators is not None:
        embed.add_field(name=field_name("Spectators"), value=str(match.spectators), inline=True)
    if player.abandoned:
        embed.add_field(name=field_name("Player state"), value="Marked abandoned", inline=False)
    if lookup.hero and lookup.hero.icon_url:
        embed.set_thumbnail(url=lookup.hero.icon_url)
    finish_embed(embed, "Live lookup", source="api.deadlock-api.com", context="Data")
    return embed


def build_live_match_embed(
    snapshot: LiveMatchSnapshot,
    highlighted_account_id: int | None = None,
) -> discord.Embed:
    embed = make_embed(
        f"Deadlock match {snapshot.match_id}",
        f"Live roster at {_duration_from_float(snapshot.game_time_seconds)} game time.",
        tone="success",
    )
    teams: dict[int | None, list[LivePlayer]] = {}
    for player in snapshot.players:
        teams.setdefault(player.team, []).append(player)

    for team, players in sorted(teams.items(), key=lambda item: item[0] or 99):
        lines = [
            _live_player_line(snapshot, player, player.account_id == highlighted_account_id)
            for player in players
        ]
        embed.add_field(
            name=field_name(f"Team {team}" if team is not None else "Unknown team"),
            value="\n".join(lines),
            inline=False,
        )
    finish_embed(
        embed,
        "Live match lookup",
        source="Deadlock live-events parser",
        context="Data",
    )
    return embed


def build_scout_overview_embeds(
    scout: MatchScout,
    highlighted_account_id: int | None = None,
) -> tuple[discord.Embed, ...]:
    header = make_embed(
        f"Deadlock scouting report — {scout.match_id}",
        (
            f"Roster captured at **{_duration_from_float(scout.game_time_seconds)}** game time.\n"
            "Use the buttons below to open individual player cards."
        ),
        tone="muted",
    )
    teams: dict[int | None, list[ScoutedPlayer]] = {}
    for player in scout.players:
        teams.setdefault(player.player.team, []).append(player)
    embeds: list[discord.Embed] = [header]
    for team, players in sorted(teams.items(), key=lambda item: item[0] or 99):
        wins = sum(player.recent_outcomes.count("W") for player in players)
        losses = sum(player.recent_outcomes.count("L") for player in players)
        team_embed = make_embed(
            _team_name(team),
            f"{len(players)} players · Combined recent form **{wins}–{losses}**",
            tone=_team_tone(team),
        )
        for player in players:
            name = discord.utils.escape_markdown(player.player.steam_name)[:36]
            if player.player.account_id == highlighted_account_id:
                name = f"{name} · You"
            team_embed.add_field(
                name=field_name(name),
                value=_scout_overview_card(player),
                inline=True,
            )
        embeds.append(team_embed)
    finish_embed(
        header,
        "Pre-game scouting",
        source="Deadlock API + live-events parser",
        context="Comfort is an experience estimate",
    )
    return tuple(embeds)


def build_scout_player_embed(
    scout: MatchScout,
    player_index: int,
    highlighted_account_id: int | None = None,
) -> discord.Embed:
    if not 0 <= player_index < len(scout.players):
        raise IndexError("player_index is outside the scouting report")
    player = scout.players[player_index]
    live = player.player
    hero_name = player.hero.name if player.hero else (
        f"Hero {live.hero_id}" if live.hero_id is not None else "Unknown hero"
    )
    linked = " · Your linked account" if live.account_id == highlighted_account_id else ""
    embed = make_embed(
        discord.utils.escape_markdown(live.steam_name),
        (
            f"Player {player_index + 1} of {len(scout.players)} · "
            f"{_team_name(live.team)} · {hero_name}{linked}"
        ),
        tone=_team_tone(live.team),
    )
    embed.add_field(name=field_name("Rank"), value=_scout_rank(player), inline=True)
    embed.add_field(
        name=field_name("Hero comfort"),
        value=_comfort(player),
        inline=True,
    )
    embed.add_field(
        name=field_name("Recent form"),
        value=_recent_form(player.recent_outcomes),
        inline=False,
    )
    steam_id64 = STEAM_ID64_OFFSET + live.account_id
    embed.add_field(
        name=field_name("Steam"),
        value=(
            f"[Open profile](https://steamcommunity.com/profiles/{steam_id64})\n"
            f"Account `{live.account_id}`"
        ),
        inline=False,
    )
    if player.hero and player.hero.icon_url:
        embed.set_thumbnail(url=player.hero.icon_url)
    finish_embed(
        embed,
        f"Match {scout.match_id}",
        source="Deadlock API + live-events parser",
        context="Player scouting",
    )
    return embed


def _live_player_line(
    snapshot: LiveMatchSnapshot,
    player: LivePlayer,
    highlighted: bool,
) -> str:
    hero = snapshot.hero(player.hero_id)
    hero_name = hero.name if hero else (
        f"Hero {player.hero_id}" if player.hero_id is not None else "Unknown hero"
    )
    name = discord.utils.escape_markdown(player.steam_name)[:40]
    if highlighted:
        name = f"**{name}**"
    return (
        f"{name} — {hero_name} — "
        f"{player.kills}/{player.deaths}/{player.assists} — {_number(player.net_worth)} souls"
    )


def _scout_overview_card(player: ScoutedPlayer) -> str:
    hero_name = player.hero.name if player.hero else (
        f"Hero {player.player.hero_id}"
        if player.player.hero_id is not None
        else "Unknown hero"
    )
    experience = player.experience
    if experience is None or experience.matches_played <= 0:
        sample = "No recorded hero games"
    else:
        sample = f"{experience.matches_played} games"
        if experience.win_rate is not None:
            sample += f" · {experience.win_rate:.0%} WR"
    form = " ".join(player.recent_outcomes) if player.recent_outcomes else "No recent form"
    return (
        f"**{hero_name}**\n"
        f"{_scout_rank(player)} · {_comfort_label(player)} comfort\n"
        f"{sample}\n"
        f"`{form}`"
    )


def _scout_rank(player: ScoutedPlayer) -> str:
    if player.rank is None:
        return "Unknown"
    if player.rank.tier == 0:
        return "Unranked"
    name = player.rank_name or f"Tier {player.rank.tier}"
    subrank = ("I", "II", "III", "IV", "V", "VI")
    suffix = subrank[player.rank.subrank - 1] if 1 <= player.rank.subrank <= 6 else str(player.rank.subrank)
    return f"{name} {suffix}"


def _comfort(player: ScoutedPlayer) -> str:
    experience = player.experience
    if experience is None or experience.matches_played <= 0:
        return "Unproven"
    games = experience.matches_played
    label = _comfort_label(player)
    win_rate = experience.win_rate
    rate = f", {win_rate:.0%} WR" if win_rate is not None else ""
    return f"{label} ({games} games{rate})"


def _comfort_label(player: ScoutedPlayer) -> str:
    experience = player.experience
    if experience is None or experience.matches_played <= 0:
        return "Unproven"
    games = experience.matches_played
    if games < 5:
        return "New"
    if games < 15:
        return "Low"
    if games < 40:
        return "Moderate"
    if games < 100:
        return "High"
    return "Very high"


def _recent_form(outcomes: tuple[str, ...]) -> str:
    if not outcomes:
        return "Unknown"
    wins = outcomes.count("W")
    losses = outcomes.count("L")
    return f"{' '.join(outcomes)} ({wins}-{losses})"


def _team_name(team: int | None) -> str:
    if team == 2:
        return "Amber team"
    if team == 3:
        return "Sapphire team"
    return f"Team {team}" if team is not None else "Unknown team"


def _team_tone(team: int | None) -> str:
    if team == 2:
        return "warning"
    if team == 3:
        return "info"
    return "muted"


def _duration(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "Unknown"
    minutes, remaining = divmod(seconds, 60)
    return f"{minutes}:{remaining:02d}"


def _duration_from_float(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "unknown"
    return _duration(int(seconds))


def _number(value: int | None) -> str:
    return f"{value:,}" if value is not None else "Unknown"


def _clean_enum(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.removeprefix("KECitadelGameMode").replace("_", " ")
    return cleaned if cleaned else None
