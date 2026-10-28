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
    level = f"Level {lookup.summoner.level:,}" if lookup.summoner else "Level unavailable"
    embed = make_embed(
        f"League profile · {lookup.account.riot_id}",
        f"**{lookup.platform}**  •  {level}\nRanked standings and recent games across every mode.",
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
                name=field_name(f"🏆 {_ranked_queue_name(entry)}"),
                value=_ranked_entry(entry),
                inline=True,
            )
    else:
        embed.add_field(
            name=field_name("🏆 Ranked standings"),
            value="*No current ranked placement returned by Riot.*",
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
        losses = len(target_matches) - wins
        aggregate_kda = (kills + assists) / max(deaths, 1)
        embed.add_field(
            name=field_name(f"Recent form · {len(target_matches)} games"),
            value=(
                f"🟩 **{wins} wins**  •  🟥 **{losses} losses**  •  "
                f"**{wins / len(target_matches):.0%}** win rate\n"
                f"`{kills / len(target_matches):.1f} / "
                f"{deaths / len(target_matches):.1f} / "
                f"{assists / len(target_matches):.1f}` average  •  "
                f"**{aggregate_kda:.2f}** KDA"
            ),
            inline=False,
        )
        lines = [
            _history_line(index, match, player)
            for index, (match, player) in enumerate(target_matches, start=1)
        ]
        _add_history_fields(embed, lines)
    else:
        embed.add_field(
            name=field_name("Recent matches"),
            value="*No recent matches were returned by Riot.*",
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
    result_marker = _result_marker(player)
    tone = "warning" if player.placement else ("success" if player.win else "danger")
    timestamp = match.started_at_ms // 1000
    position = _title_token(player.team_position, "Unknown role")
    embed = make_embed(
        f"{result_marker} {outcome} · {player.champion_name}",
        (
            f"**{queue_name(match)}**  •  {position}  •  {_duration(match.duration_seconds)}\n"
            f"{lookup.account.riot_id}  •  Played <t:{timestamp}:f> (<t:{timestamp}:R>)"
            if timestamp
            else (
                f"**{queue_name(match)}**  •  {position}  •  "
                f"{_duration(match.duration_seconds)}\n{lookup.account.riot_id}"
            )
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
        name=field_name("KDA & participation"),
        value=(
            f"**{player.kills} / {player.deaths} / {player.assists}**\n"
            f"{player.kda:.2f} KDA  •  "
            f"{(player.kills + player.assists) / team_kills:.0%} KP"
            f"{'  •  #' + str(player.placement) if player.placement else ''}"
        ),
        inline=True,
    )
    embed.add_field(
        name=field_name("Farm & economy"),
        value=(
            f"**{player.creep_score:,} CS**  •  {player.creep_score / minutes:.1f}/min\n"
            f"{player.gold_earned:,} gold  •  Level {player.champion_level}"
        ),
        inline=True,
    )
    embed.add_field(
        name=field_name("Vision"),
        value=(
            f"**{player.vision_score} score**\n"
            f"{player.wards_placed} placed  •  {player.wards_killed} cleared  •  "
            f"{player.control_wards_bought} control"
        ),
        inline=True,
    )
    embed.add_field(
        name=field_name("Damage profile"),
        value=(
            f"**{player.total_damage_to_champions:,}** champion damage  •  "
            f"{player.total_damage_to_champions / minutes:,.0f}/min\n"
            f"`{player.physical_damage_to_champions:,}` physical  •  "
            f"`{player.magic_damage_to_champions:,}` magic  •  "
            f"`{player.true_damage_to_champions:,}` true\n"
            f"{player.total_damage_taken:,} taken  •  {player.total_heal:,} healed  •  "
            f"{_multi_kill(player.largest_multi_kill)} multikill"
        ),
        inline=False,
    )
    embed.add_field(
        name=field_name("Objectives & control"),
        value=(
            f"**{player.damage_to_objectives:,}** objective damage  •  "
            f"{player.damage_to_turrets:,} turret damage\n"
            f"{player.turret_kills} turrets  •  {player.inhibitor_kills} inhibitors  •  "
            f"{player.total_time_cc_dealt}s crowd control"
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
            name=field_name(
                f"{_team_marker(team_id)} {_team_name(team_id)}  •  "
                f"{'🟩 Victory' if won else '🟥 Defeat'}"
            ),
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
    timestamp = match.started_at_ms // 1000
    when = f"  •  <t:{timestamp}:R>" if timestamp else ""
    return (
        f"{_result_marker(player)} **{index}. {player.champion_name}**  —  {queue_name(match)}\n"
        f"└ `{player.kills} / {player.deaths} / {player.assists}`  •  "
        f"{player.creep_score} CS  •  {_duration(match.duration_seconds)}{when}"
    )


def _ranked_entry(entry: RankedEntry) -> str:
    win_rate = f"{entry.win_rate:.1%}" if entry.win_rate is not None else "—"
    streak = " · Hot streak" if entry.hot_streak else ""
    return (
        f"**{entry.tier.title()} {entry.rank}**  •  {entry.league_points} LP\n"
        f"🟩 {entry.wins:,}W  •  🟥 {entry.losses:,}L  •  **{win_rate}** WR{streak}"
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
    selected = player.puuid == target_puuid
    marker = "**" if selected else ""
    pointer = "▸" if selected else "•"
    return (
        f"{pointer} {marker}{name}{marker} — {player.champion_name}\n"
        f"  `{player.kills}/{player.deaths}/{player.assists}`  •  "
        f"{player.creep_score} CS  •  {_compact(player.total_damage_to_champions)} dmg"
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


def _result_marker(player: MatchParticipant) -> str:
    if player.placement:
        return "🟨"
    return "🟩" if player.win else "🟥"


def _team_marker(team_id: int) -> str:
    return {100: "🟦", 200: "🟥"}.get(team_id, "⬜")


def _add_history_fields(embed: discord.Embed, lines: list[str]) -> None:
    remaining = list(lines)
    page = 0
    while remaining:
        page += 1
        kept: list[str] = []
        length = 0
        while remaining:
            line = remaining[0]
            added = len(line) + (2 if kept else 0)
            if kept and (length + added > 1024 or len(kept) >= 6):
                break
            kept.append(remaining.pop(0))
            length += added
        name = "Recent matches" if page == 1 else "Recent matches · continued"
        embed.add_field(name=field_name(name), value="\n\n".join(kept), inline=False)


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

