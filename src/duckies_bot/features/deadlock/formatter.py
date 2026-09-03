"""Discord presentation for Deadlock lookups."""

from __future__ import annotations

from collections.abc import Callable

import discord

from ..accounts.steam import STEAM_ID64_OFFSET
from .models import (
    LiveMatchSnapshot,
    LivePlayer,
    MatchScout,
    PlayerLookup,
    ScoutedPlayer,
    SteamProfile,
)
from ...presentation import field_name, finish_embed, make_embed


def build_live_match_embed(
    snapshot: LiveMatchSnapshot,
    highlighted_account_id: int | None = None,
    *,
    stream_status: str = "live",
) -> discord.Embed:
    status_descriptions = {
        "live": f"Live roster at {_duration_from_float(snapshot.game_time_seconds)} game time.",
        "reconnecting": (
            f"Last update at {_duration_from_float(snapshot.game_time_seconds)} game time.\n"
            "The live feed was interrupted; reconnecting automatically."
        ),
        "unreachable": (
            f"Last update at {_duration_from_float(snapshot.game_time_seconds)} game time.\n"
            "The live feed is currently unreachable after repeated reconnection attempts."
        ),
        "ended": (
            f"Final recorded state at {_duration_from_float(snapshot.game_time_seconds)} game time.\n"
            "The match has ended."
        ),
    }
    status_tones = {
        "live": "success",
        "reconnecting": "warning",
        "unreachable": "danger",
        "ended": "muted",
    }
    if stream_status not in status_descriptions:
        raise ValueError(f"Unknown live stream status: {stream_status}")
    status_titles = {
        "live": f"Deadlock match {snapshot.match_id}",
        "reconnecting": f"Deadlock match {snapshot.match_id} — Reconnecting",
        "unreachable": f"Deadlock match {snapshot.match_id} — Feed unavailable",
        "ended": f"Deadlock match {snapshot.match_id} — Ended",
    }
    embed = make_embed(
        status_titles[stream_status],
        status_descriptions[stream_status],
        tone=status_tones[stream_status],
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
        "Live match watch" if stream_status != "live" else "Live match lookup",
        source="Deadlock live-events parser",
        context="Data",
    )
    return embed


def build_watch_tab_embed(
    snapshot: LiveMatchSnapshot,
    tab: str,
    highlighted_account_id: int | None = None,
    *,
    stream_status: str = "live",
    selected_account_id: int | None = None,
    timeline: tuple[str, ...] = (),
) -> discord.Embed:
    """Render one page of the interactive live-match watch."""
    if tab == "overview":
        return build_live_match_embed(snapshot, highlighted_account_id, stream_status=stream_status)
    if tab == "combat":
        return _build_watch_team_page(snapshot, "Combat", stream_status, _combat_line)
    if tab == "economy":
        return _build_watch_team_page(snapshot, "Economy", stream_status, _economy_line)
    if tab == "builds":
        return _build_watch_team_page(snapshot, "Builds", stream_status, _build_line)
    if tab == "statues":
        return _build_watch_team_page(snapshot, "Golden Statues", stream_status, _statue_line)
    if tab == "timeline":
        embed = _watch_shell(snapshot, "Timeline", stream_status)
        embed.description += "\n\n" + (
            "\n".join(timeline[-15:])
            if timeline
            else "No tracked changes have occurred since this watch started."
        )
        finish_embed(
            embed,
            "Live match watch",
            source="Deadlock live-events parser",
            context="Timeline begins when the Discord watch starts",
        )
        return embed
    if tab == "player":
        player = next(
            (item for item in snapshot.players if item.account_id == selected_account_id),
            snapshot.players[0] if snapshot.players else None,
        )
        return _build_watch_player_page(snapshot, player, highlighted_account_id, stream_status)
    raise ValueError(f"Unknown watch tab: {tab}")


def _build_watch_team_page(
    snapshot: LiveMatchSnapshot,
    page_name: str,
    stream_status: str,
    line_builder: Callable[[LiveMatchSnapshot, LivePlayer, list[LivePlayer]], str],
) -> discord.Embed:
    embed = _watch_shell(snapshot, page_name, stream_status)
    teams: dict[int | None, list[LivePlayer]] = {}
    for player in snapshot.players:
        teams.setdefault(player.team, []).append(player)
    for team, players in sorted(teams.items(), key=lambda item: item[0] or 99):
        lines = [line_builder(snapshot, player, players) for player in players]
        embed.add_field(
            name=field_name(_team_name(team)),
            value="\n".join(lines) or "No player data",
            inline=False,
        )
    context = {
        "Combat": "Damage and healing reported by the live controller feed",
        "Economy": "Souls, lane farm, and rates from the current snapshot",
        "Builds": "Raw live upgrade IDs; names can be added from the cached item asset catalog",
        "Golden Statues": "Permanent statue pickups from the live ActiveModifiers table; counts are tier 1/2/3",
    }[page_name]
    finish_embed(
        embed,
        "Live match watch",
        source="Deadlock live-events parser",
        context=context,
    )
    return embed


def _watch_shell(
    snapshot: LiveMatchSnapshot,
    page_name: str,
    stream_status: str,
) -> discord.Embed:
    state = {
        "live": ("success", "Live"),
        "reconnecting": ("warning", "Reconnecting"),
        "unreachable": ("danger", "Feed unavailable"),
        "ended": ("muted", "Ended"),
    }
    if stream_status not in state:
        raise ValueError(f"Unknown live stream status: {stream_status}")
    tone, label = state[stream_status]
    detail = {
        "live": "Receiving live match updates.",
        "reconnecting": "The feed was interrupted; reconnecting automatically.",
        "unreachable": "The live feed is currently unreachable.",
        "ended": "The match has ended; showing the final recorded state.",
    }[stream_status]
    status_suffix = "" if stream_status == "live" else f" · {label}"
    return make_embed(
        f"Deadlock match {snapshot.match_id} — {page_name}{status_suffix}",
        (
            f"**{label}** · {_duration_from_float(snapshot.game_time_seconds)} game time\n"
            f"{detail}"
        ),
        tone=tone,
    )


def _combat_line(
    snapshot: LiveMatchSnapshot,
    player: LivePlayer,
    teammates: list[LivePlayer],
) -> str:
    del snapshot
    team_kills = max(sum(item.kills for item in teammates), 1)
    participation = min((player.kills + player.assists) / team_kills, 1.0)
    name = discord.utils.escape_markdown(player.steam_name)[:24]
    return (
        f"**{name}** · `{player.kills}/{player.deaths}/{player.assists}` · "
        f"KP {participation:.0%}\n"
        f"DMG {_compact_number(player.hero_damage)} · OBJ {_compact_number(player.objective_damage)} · "
        f"HEAL {_compact_number(player.hero_healing)}"
    )


def _economy_line(
    snapshot: LiveMatchSnapshot,
    player: LivePlayer,
    teammates: list[LivePlayer],
) -> str:
    del teammates
    minutes = max((snapshot.game_time_seconds or 0) / 60, 1 / 60)
    name = discord.utils.escape_markdown(player.steam_name)[:24]
    return (
        f"**{name}** · {_number(player.net_worth)} souls · "
        f"{player.net_worth / minutes:,.0f}/min\n"
        f"LH {player.last_hits:,} · Denies {player.denies:,} · Lane {player.assigned_lane or '—'}"
    )


def _build_line(
    snapshot: LiveMatchSnapshot,
    player: LivePlayer,
    teammates: list[LivePlayer],
) -> str:
    del snapshot, teammates
    name = discord.utils.escape_markdown(player.steam_name)[:24]
    allocation = _upgrade_allocation(player.upgrades, limit=100)
    return f"**{name}** · Upgrades `{allocation}`"


def _statue_line(
    snapshot: LiveMatchSnapshot,
    player: LivePlayer,
    teammates: list[LivePlayer],
) -> str:
    del snapshot, teammates
    name = discord.utils.escape_markdown(player.steam_name)[:24]
    stats = (
        ("HP", "health"),
        ("WP", "weapon_power"),
        ("Spirit", "spirit"),
        ("Fire rate", "fire_rate"),
        ("Ammo", "ammo"),
        ("Cooldown", "cooldown"),
    )
    details = " · ".join(
        f"{label} `{tier1}/{tier2}/{tier3}`"
        for label, stat in stats
        for tier1, tier2, tier3 in (player.statue_tiers(stat),)
    )
    return f"**{name}** · 🏆 **{player.statue_buff_count}**\n{details}"


def _build_watch_player_page(
    snapshot: LiveMatchSnapshot,
    player: LivePlayer | None,
    highlighted_account_id: int | None,
    stream_status: str,
) -> discord.Embed:
    if player is None:
        embed = _watch_shell(snapshot, "Player", stream_status)
        embed.description += "\n\nNo players have been reported."
        return embed
    hero = snapshot.hero(player.hero_id)
    hero_name = hero.name if hero else (
        f"Hero {player.hero_id}" if player.hero_id is not None else "Unknown hero"
    )
    name = discord.utils.escape_markdown(player.steam_name)
    if player.account_id == highlighted_account_id:
        name += " · You"
    embed = _watch_shell(snapshot, name, stream_status)
    embed.description += f"\n**{hero_name}** · {_team_name(player.team)} · Lane {player.assigned_lane or '—'}"
    minutes = max((snapshot.game_time_seconds or 0) / 60, 1 / 60)
    embed.add_field(
        name=field_name("Score"),
        value=f"`{player.kills}/{player.deaths}/{player.assists}`",
        inline=True,
    )
    embed.add_field(
        name=field_name("Economy"),
        value=f"{_number(player.net_worth)} souls\n{player.net_worth / minutes:,.0f}/min",
        inline=True,
    )
    embed.add_field(
        name=field_name("Farm"),
        value=f"{player.last_hits:,} last hits\n{player.denies:,} denies",
        inline=True,
    )
    embed.add_field(
        name=field_name("Damage"),
        value=f"{_number(player.hero_damage)} hero\n{_number(player.objective_damage)} objective",
        inline=True,
    )
    embed.add_field(
        name=field_name("Sustain"),
        value=(
            f"{_number(player.hero_healing)} ally healing\n"
            f"{_number(player.self_healing)} self healing"
        ),
        inline=True,
    )
    embed.add_field(
        name=field_name("Live upgrade IDs"),
        value=_upgrade_allocation(player.upgrades),
        inline=False,
    )
    embed.add_field(
        name=field_name("Golden statues"),
        value=_statue_line(snapshot, player, []),
        inline=False,
    )
    if hero and hero.icon_url:
        embed.set_thumbnail(url=hero.icon_url)
    finish_embed(
        embed,
        "Live match watch",
        source="Deadlock live-events parser",
        context="Player detail",
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
        context="Game totals use recorded Deadlock API history",
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
        name=field_name("Hero history"),
        value=_hero_history(player),
        inline=True,
    )
    embed.add_field(
        name=field_name("Recent form"),
        value=_recent_form(player.recent_outcomes),
        inline=False,
    )
    embed.add_field(
        name=field_name("Top heroes"),
        value=_top_heroes(player),
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


def build_player_lookup_embed(player: PlayerLookup) -> discord.Embed:
    """Render a standalone Deadlock player summary."""
    profile = player.profile
    name = discord.utils.escape_markdown(profile.personaname)
    embed = make_embed(
        name,
        f"Deadlock player overview · Account `{profile.account_id}`",
        tone="info",
        url=profile.profile_url,
    )
    embed.add_field(
        name=field_name("Rank"),
        value=_player_lookup_rank(player),
        inline=True,
    )
    win_rate = (
        f"{player.win_rate:.0%}" if player.win_rate is not None else "No recorded games"
    )
    embed.add_field(
        name=field_name("Recorded history"),
        value=(
            f"**{player.total_matches:,}** games · **{win_rate}** win rate\n"
            f"{profile.matches_played_last_30_days:,} in the last 30 days"
        ),
        inline=True,
    )
    embed.add_field(
        name=field_name("Recent form"),
        value=_recent_form(player.recent_outcomes),
        inline=False,
    )
    top_heroes = []
    for record in player.top_heroes:
        experience = record.experience
        hero_name = record.hero.name if record.hero else f"Hero {experience.hero_id}"
        hero_rate = (
            f"{experience.win_rate:.0%}" if experience.win_rate is not None else "—"
        )
        top_heroes.append(
            f"**{hero_name}** · {experience.matches_played:,} games · {hero_rate} WR"
        )
    embed.add_field(
        name=field_name("Top heroes"),
        value="\n".join(top_heroes) or "No recorded hero history",
        inline=False,
    )
    steam_id64 = STEAM_ID64_OFFSET + profile.account_id
    embed.add_field(
        name=field_name("Steam"),
        value=(
            f"[Open profile]({profile.profile_url})\n"
            f"SteamID64 `{steam_id64}`"
        ),
        inline=False,
    )
    if profile.avatar_url:
        embed.set_thumbnail(url=profile.avatar_url)
    finish_embed(
        embed,
        "Player lookup",
        source="Deadlock API + Steam Community",
        context="Totals use recorded Deadlock API history",
    )
    return embed


def build_player_search_embeds(
    profiles: tuple[SteamProfile, ...],
) -> tuple[discord.Embed, ...]:
    """Render Steam name matches as visual identity cards."""
    embeds: list[discord.Embed] = []
    for index, profile in enumerate(profiles[:10], start=1):
        name = discord.utils.escape_markdown(profile.personaname)
        embed = make_embed(
            f"{index}. {name}",
            (
                f"Account `{profile.account_id}` · "
                f"{profile.matches_played_last_30_days:,} matches in the last 30 days\n"
                f"[Open Steam profile]({profile.profile_url})"
            ),
            tone="muted",
            url=profile.profile_url,
        )
        if profile.avatar_url:
            embed.set_thumbnail(url=profile.avatar_url)
        finish_embed(
            embed,
            "Player search",
            source="Deadlock API + Steam Community",
            context="Choose a numbered result below",
        )
        embeds.append(embed)
    return tuple(embeds)


def _player_lookup_rank(player: PlayerLookup) -> str:
    if player.rank is None:
        return "Unknown"
    if player.rank.tier == 0:
        return "Unranked"
    name = player.rank_name or f"Tier {player.rank.tier}"
    subranks = ("I", "II", "III", "IV", "V", "VI")
    suffix = (
        subranks[player.rank.subrank - 1]
        if 1 <= player.rank.subrank <= len(subranks)
        else str(player.rank.subrank)
    )
    return f"{name} {suffix}"


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
    form = " ".join(player.recent_outcomes) if player.recent_outcomes else "No recent form"
    return (
        f"**{hero_name}**\n"
        f"{_scout_rank(player)} · {_total_games(player)}\n"
        f"{_hero_history(player)}\n"
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


def _total_games(player: ScoutedPlayer) -> str:
    if player.total_matches is None:
        return "Unknown total games"
    return f"{player.total_matches} total games"


def _hero_history(player: ScoutedPlayer) -> str:
    experience = player.experience
    if experience is None or experience.matches_played <= 0:
        if player.total_matches is not None and player.total_matches > 0:
            return "0 (0%) hero games"
        return "No recorded hero games"
    share = player.hero_match_share
    games = (
        f"{experience.matches_played} ({share:.0%}) hero games"
        if share is not None
        else f"{experience.matches_played} hero games"
    )
    parts = [games]
    win_rate = experience.win_rate
    if win_rate is not None:
        parts.append(f"{win_rate:.0%} WR")
    return " · ".join(parts)


def _top_heroes(player: ScoutedPlayer) -> str:
    if not player.top_heroes:
        return "No recorded hero statistics"
    lines: list[str] = []
    for index, record in enumerate(player.top_heroes, start=1):
        experience = record.experience
        hero_name = (
            record.hero.name if record.hero is not None else f"Hero {experience.hero_id}"
        )
        hero_name = discord.utils.escape_markdown(hero_name)[:40]
        stats = [f"{experience.matches_played} games"]
        if experience.win_rate is not None:
            stats.append(f"{experience.win_rate:.0%} WR")
        if player.total_matches is not None and player.total_matches > 0:
            stats.append(f"{experience.matches_played / player.total_matches:.0%} played")
        lines.append(f"`{index}.` **{hero_name}** — {' · '.join(stats)}")
    return "\n".join(lines)


def _recent_form(outcomes: tuple[str, ...]) -> str:
    if not outcomes:
        return "Unknown"
    wins = outcomes.count("W")
    losses = outcomes.count("L")
    return f"{' '.join(outcomes)} ({wins}-{losses})"


def _team_name(team: int | None) -> str:
    if team == 2:
        return "Amber"
    if team == 3:
        return "Sapphire"
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


def _compact_number(value: int) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _upgrade_allocation(upgrades: tuple[int, ...], *, limit: int = 1_000) -> str:
    allocation = " · ".join(map(str, upgrades)) if upgrades else "Not reported"
    if len(allocation) > limit:
        return allocation[: limit - 1] + "…"
    return allocation
