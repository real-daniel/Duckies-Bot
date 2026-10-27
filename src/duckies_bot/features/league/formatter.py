"""Discord embeds for League player and match lookups."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

import discord

from ...presentation import field_name, finish_embed, make_embed
from .models import LeagueMatch, LeaguePlayerLookup, MatchParticipant, RankedEntry


_QUEUE_NAMES = {
    0: "Custom",
    400: "Normal Draft",
    420: "Ranked Solo/Duo",
    430: "Normal Blind",
    440: "Ranked Flex",
    450: "ARAM",
    480: "Swiftplay",
    490: "Quickplay",
    700: "Clash",
    830: "Co-op vs. AI Intro",
    840: "Co-op vs. AI Beginner",
    850: "Co-op vs. AI Intermediate",
    900: "ARURF",
    1020: "One for All",
    1300: "Nexus Blitz",
    1400: "Ultimate Spellbook",
    1700: "Arena",
    1710: "Arena",
    1810: "Swarm",
    1820: "Swarm",
    1830: "Swarm",
    1840: "Swarm",
    1900: "Pick URF",
}


def queue_name(match: LeagueMatch) -> str:
    known = _QUEUE_NAMES.get(match.queue_id)
    if known is not None:
        return known
    mode = _title_token(match.game_mode, "Unknown mode")
    return f"{mode} · Queue {match.queue_id}"


def build_player_embed(lookup: LeaguePlayerLookup) -> discord.Embed:
    level = f" · Level {lookup.summoner.level:,}" if lookup.summoner else ""
    embed = make_embed(
        f"{lookup.account.riot_id} — League profile",
        f"**{lookup.platform}**{level}\nRecent history includes all modes returned by Riot.",
        tone="info",
    )
    if lookup.summoner and lookup.data_dragon_version:
        embed.set_thumbnail(
            url=(
                "https://ddragon.leagueoflegends.com/cdn/"
                f"{lookup.data_dragon_version}/img/profileicon/"
                f"{lookup.summoner.profile_icon_id}.png"
            )
        )

    if lookup.ranked_entries:
        for entry in lookup.ranked_entries:
            embed.add_field(
                name=field_name(_ranked_queue_name(entry)),
                value=_ranked_entry(entry),
                inline=True,
            )
    else:
        embed.add_field(
            name=field_name("Ranked standings"),
            value="No current ranked placement returned by Riot.",
            inline=False,
        )

    target_matches = [
        (match, match.participant(lookup.account.puuid))
        for match in lookup.matches
    ]
    target_matches = [(match, player) for match, player in target_matches if player is not None]
    if target_matches:
        wins = sum(player.win for _, player in target_matches)
        kills = sum(player.kills for _, player in target_matches)
        deaths = sum(player.deaths for _, player in target_matches)
        assists = sum(player.assists for _, player in target_matches)
        embed.add_field(
            name=field_name(f"Recent form · {len(target_matches)} games"),
            value=(
                f"**{wins}W–{len(target_matches) - wins}L** · {wins / len(target_matches):.0%} WR\n"
                f"`{kills / len(target_matches):.1f}/{deaths / len(target_matches):.1f}/"
                f"{assists / len(target_matches):.1f}` average K/D/A"
            ),
            inline=False,
        )
        lines = [
            _history_line(index, match, player)
            for index, (match, player) in enumerate(target_matches, start=1)
        ]
        embed.add_field(
            name=field_name("Match history"),
            value=_field_lines(lines),
            inline=False,
        )
    else:
        embed.add_field(
            name=field_name("Match history"),
            value="No recent matches were returned by Riot.",
            inline=False,
        )
    finish_embed(
        embed,
        "League player lookup",
        source="Riot Games API",
        context="Ranked standings and all-mode match history",
    )
    return embed


def build_match_embed(lookup: LeaguePlayerLookup, match_index: int) -> discord.Embed:
    match = lookup.matches[match_index]
    player = match.participant(lookup.account.puuid)
    if player is None:
        raise ValueError("The selected player is missing from this match.")
    outcome = _result_label(player)
    tone = "success" if player.win else "danger"
    timestamp = match.started_at_ms // 1000
    position = _title_token(player.team_position, "Unknown role")
    embed = make_embed(
        f"{outcome} · {player.champion_name} · {queue_name(match)}",
        (
            f"{lookup.account.riot_id} · **{position}** · {_duration(match.duration_seconds)}\n"
            f"Played <t:{timestamp}:f> (<t:{timestamp}:R>)"
            if timestamp
            else f"{lookup.account.riot_id} · **{position}** · {_duration(match.duration_seconds)}"
        ),
        tone=tone,
    )
    if lookup.data_dragon_version:
        embed.set_thumbnail(
            url=(
                "https://ddragon.leagueoflegends.com/cdn/"
                f"{lookup.data_dragon_version}/img/champion/{player.champion_name}.png"
            )
        )

    teammates = [item for item in match.participants if item.team_id == player.team_id]
    team_kills = max(sum(item.kills for item in teammates), 1)
    minutes = max(match.duration_seconds / 60, 1 / 60)
    embed.add_field(
        name=field_name("Performance"),
        value=(
            f"K/D/A **{player.kills}/{player.deaths}/{player.assists}** · {player.kda:.2f} KDA"
            f"{' · Placement #' + str(player.placement) if player.placement else ''}\n"
            f"Kill participation **{(player.kills + player.assists) / team_kills:.0%}** · "
            f"Level {player.champion_level}\n"
            f"CS **{player.creep_score:,}** · {player.creep_score / minutes:.1f}/min · "
            f"Gold **{player.gold_earned:,}**"
        ),
        inline=False,
    )
    embed.add_field(
        name=field_name("Combat"),
        value=(
            f"Champion damage **{player.total_damage_to_champions:,}** · "
            f"{player.total_damage_to_champions / minutes:,.0f}/min\n"
            f"Physical {player.physical_damage_to_champions:,} · "
            f"Magic {player.magic_damage_to_champions:,} · "
            f"True {player.true_damage_to_champions:,}\n"
            f"Taken {player.total_damage_taken:,} · Healed {player.total_heal:,} · "
            f"Largest multikill {_multi_kill(player.largest_multi_kill)}"
        ),
        inline=False,
    )
    embed.add_field(
        name=field_name("Vision and objectives"),
        value=(
            f"Vision **{player.vision_score}** · Wards {player.wards_placed} placed / "
            f"{player.wards_killed} cleared · Control wards {player.control_wards_bought}\n"
            f"Objective damage **{player.damage_to_objectives:,}** · "
            f"Turret damage {player.damage_to_turrets:,}\n"
            f"Turrets {player.turret_kills} · Inhibitors {player.inhibitor_kills} · "
            f"CC time {player.total_time_cc_dealt}s"
        ),
        inline=False,
    )
    embed.add_field(
        name=field_name("Final build"),
        value=_item_build(player, lookup.item_names or {}),
        inline=False,
    )

    grouped: dict[int, list[MatchParticipant]] = defaultdict(list)
    for participant in match.participants:
        grouped[participant.team_id].append(participant)
    for team_id, participants in sorted(grouped.items()):
        won = any(item.win for item in participants)
        lines = [_scoreboard_line(item, lookup.account.puuid) for item in participants]
        embed.add_field(
            name=field_name(f"{_team_name(team_id)} · {'Victory' if won else 'Defeat'}"),
            value=_field_lines(lines),
            inline=False,
        )
    finish_embed(
        embed,
        f"Match {match.match_id}",
        source="Riot Games API",
        context=f"Patch {match.game_version}",
    )
    return embed


def _history_line(index: int, match: LeagueMatch, player: MatchParticipant) -> str:
    result = f"#{player.placement}" if player.placement else ("W" if player.win else "L")
    timestamp = match.started_at_ms // 1000
    when = f" · <t:{timestamp}:R>" if timestamp else ""
    return (
        f"`{index:>2}. {result}` **{player.champion_name}** · {queue_name(match)} · "
        f"`{player.kills}/{player.deaths}/{player.assists}` · {player.creep_score} CS{when}"
    )


def _ranked_entry(entry: RankedEntry) -> str:
    win_rate = f"{entry.win_rate:.1%}" if entry.win_rate is not None else "—"
    streak = " · Hot streak" if entry.hot_streak else ""
    return (
        f"**{entry.tier.title()} {entry.rank}** · {entry.league_points} LP\n"
        f"{entry.wins:,}W–{entry.losses:,}L · {win_rate} WR{streak}"
    )


def _ranked_queue_name(entry: RankedEntry) -> str:
    return {
        "RANKED_SOLO_5x5": "Ranked Solo/Duo",
        "RANKED_FLEX_SR": "Ranked Flex",
    }.get(entry.queue_type, _title_token(entry.queue_type, "Ranked"))


def _item_build(player: MatchParticipant, item_names: Mapping[int, str]) -> str:
    if not player.item_ids:
        return "No completed items were recorded."
    return " · ".join(item_names.get(item_id, f"Item {item_id}") for item_id in player.item_ids)


def _scoreboard_line(player: MatchParticipant, target_puuid: str) -> str:
    name = discord.utils.escape_markdown(player.riot_id)[:22]
    marker = "**" if player.puuid == target_puuid else ""
    return (
        f"{marker}{name}{marker} · {player.champion_name} · "
        f"`{player.kills}/{player.deaths}/{player.assists}` · "
        f"{player.creep_score} CS · {_compact(player.total_damage_to_champions)} dmg"
    )


def _team_name(team_id: int) -> str:
    return {100: "Blue team", 200: "Red team"}.get(team_id, f"Team {team_id}")


def _duration(seconds: int) -> str:
    minutes, remainder = divmod(max(seconds, 0), 60)
    return f"{minutes}:{remainder:02d}"


def _compact(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _title_token(value: str, fallback: str) -> str:
    value = value.strip(" _")
    return value.replace("_", " ").title() if value else fallback


def _multi_kill(value: int) -> str:
    return {0: "None", 1: "Single", 2: "Double", 3: "Triple", 4: "Quadra", 5: "Penta"}.get(
        value, str(value)
    )


def _result_label(player: MatchParticipant) -> str:
    if player.placement:
        return f"#{player.placement} Place"
    return "Victory" if player.win else "Defeat"


def _field_lines(lines: list[str]) -> str:
    kept: list[str] = []
    length = 0
    for line in lines:
        added = len(line) + (1 if kept else 0)
        if length + added > 1024:
            break
        kept.append(line)
        length += added
    return "\n".join(kept) or "No data"

