"""Discord presentation helpers for PvE Tarkov data."""

from urllib.parse import quote
from datetime import datetime

import discord

from .models import (
    QuestLogSummary,
    TarkovServerStatus,
    TarkovItem,
    TarkovTask,
    TaskItemRequirement,
    TaskRewards,
)


def format_roubles(value: int | None) -> str:
    return "Unavailable" if value is None else f"{value:,} ₽"


def _format_number(value: float) -> str:
    return f"{value:g}"


def _truncate(value: str, limit: int = 1024) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _bullets(lines: list[str], empty: str = "None") -> str:
    return _truncate("\n".join(f"• {line}" for line in lines) if lines else empty)


def _line_chunks(lines: list[str], limit: int = 1024) -> list[str]:
    if not lines:
        return ["None"]
    chunks: list[str] = []
    current = ""
    for line in lines:
        rendered = f"• {line}"
        if len(rendered) > limit:
            rendered = _truncate(rendered, limit)
        candidate = rendered if not current else f"{current}\n{rendered}"
        if len(candidate) > limit:
            chunks.append(current)
            current = rendered
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_item_embed(item: TarkovItem) -> discord.Embed:
    description = "PvE Tarkov item"
    if item.short_name and item.short_name.casefold() != item.name.casefold():
        description += f" • {item.short_name}"

    embed = discord.Embed(
        title=item.name[:256],
        description=description[:4096],
        color=discord.Color.green(),
    )
    if item.icon_url:
        embed.set_thumbnail(url=item.icon_url)

    embed.add_field(name="24h average", value=format_roubles(item.avg24h_price), inline=True)
    embed.add_field(
        name="24h range",
        value=f"{format_roubles(item.low24h_price)} – {format_roubles(item.high24h_price)}",
        inline=True,
    )
    embed.add_field(name="Latest low", value=format_roubles(item.last_low_price), inline=True)

    trader = item.best_trader_offer
    trader_value = (
        f"{trader.vendor_name}: {format_roubles(trader.price_rub)}"
        if trader is not None
        else "No trader offer available"
    )
    embed.add_field(name="Best trader", value=trader_value[:1024], inline=False)

    if item.task_names:
        visible_tasks = item.task_names[:5]
        task_value = "\n".join(f"• {name}" for name in visible_tasks)
        remaining = len(item.task_names) - len(visible_tasks)
        if remaining:
            task_value += f"\n…and {remaining} more"
    else:
        task_value = "Not currently required for a task"
    embed.add_field(name="Needed for tasks", value=task_value[:1024], inline=False)
    embed.set_footer(text="PvE data from tarkov.dev")
    return embed


def _format_requirement(requirement: TaskItemRequirement) -> str:
    if len(requirement.item_names) <= 4:
        names = " / ".join(requirement.item_names)
        text = f"{_format_number(requirement.count)}× {names}"
    else:
        text = requirement.description
    if requirement.found_in_raid:
        text += " (Found in Raid)"
    return text


def _map_links(task: TarkovTask) -> list[str]:
    return [
        f"[{task_map.name}](https://tarkov.dev/map/{quote(task_map.normalized_name)})"
        for task_map in task.maps
    ]


def _overview_embed(task: TarkovTask, detailed: bool) -> discord.Embed:
    level = f"Level {task.min_player_level}" if task.min_player_level is not None else "Any level"
    description = f"PvE task • {task.trader_name} • {level}"
    embed = discord.Embed(
        title=task.name[:256],
        url=task.wiki_url,
        description=description[:4096],
        color=discord.Color.orange(),
    )
    if task.image_url:
        embed.set_thumbnail(url=task.image_url)

    embed.add_field(name="Maps", value=_bullets(_map_links(task), "No specific map"), inline=False)
    embed.add_field(
        name="Bring / collect",
        value=_bullets([_format_requirement(req) for req in task.required_items]),
        inline=False,
    )
    embed.add_field(name="Required keys", value=_bullets(list(task.required_keys)), inline=False)

    objective_lines = [
        ("(Optional) " if objective.optional else "") + objective.description
        for objective in task.objectives
    ]
    visible = objective_lines if detailed else objective_lines[:8]
    if not detailed and len(objective_lines) > len(visible):
        visible.append(f"…and {len(objective_lines) - len(visible)} more; use detailed mode")
    embed.add_field(name="Raid plan", value=_bullets(visible), inline=False)
    embed.add_field(
        name="Prerequisites",
        value=_bullets(list(task.prerequisite_names)),
        inline=False,
    )

    rewards = []
    if task.experience:
        rewards.append(f"{task.experience:,} XP")
    rewards.extend(
        f"{_format_number(item.count)}× {item.item_name}"
        for item in task.finish_rewards.items[:5]
    )
    embed.add_field(name="Completion rewards", value=_bullets(rewards), inline=False)

    flags = ["PvE data from tarkov.dev"]
    if task.kappa_required:
        flags.append("Required for Kappa")
    if task.lightkeeper_required:
        flags.append("Required for Lightkeeper")
    if task.faction_name and task.faction_name.casefold() != "any":
        flags.append(task.faction_name)
    embed.set_footer(text=" • ".join(flags))
    return embed


def _reward_lines(rewards: TaskRewards) -> list[str]:
    lines = [f"{_format_number(item.count)}× {item.item_name}" for item in rewards.items]
    lines.extend(rewards.trader_standing)
    lines.extend(rewards.skills)
    lines.extend(rewards.unlocks)
    return lines


def build_task_embeds(task: TarkovTask, detailed: bool = False) -> tuple[discord.Embed, ...]:
    """Build a raid-prep summary and, optionally, objective/reward details."""

    embeds: list[discord.Embed] = [_overview_embed(task, detailed)]
    if not detailed:
        return tuple(embeds)

    for offset in range(0, len(task.objectives), 20):
        page = discord.Embed(
            title=f"{task.name} — Objectives"[:256],
            color=discord.Color.orange(),
        )
        for index, objective in enumerate(task.objectives[offset : offset + 20], offset + 1):
            details: list[str] = [objective.description]
            if objective.maps:
                details.append("Maps: " + ", ".join(map_.name for map_ in objective.maps))
            if objective.required_keys:
                details.append("Keys: " + ", ".join(objective.required_keys))
            if objective.optional:
                details.append("Optional")
            page.add_field(
                name=f"{index}. {objective.objective_type}"[:256],
                value=_truncate("\n".join(details)),
                inline=False,
            )
        embeds.append(page)

    reward_embed = discord.Embed(
        title=f"{task.name} — Rewards"[:256],
        description=f"Experience: {task.experience:,} XP",
        color=discord.Color.gold(),
    )
    reward_embed.add_field(
        name="On start",
        value=_bullets(_reward_lines(task.start_rewards)),
        inline=False,
    )
    reward_embed.add_field(
        name="On completion",
        value=_bullets(_reward_lines(task.finish_rewards)),
        inline=False,
    )
    reward_embed.add_field(
        name="On failure",
        value=_bullets(_reward_lines(task.failure_rewards)),
        inline=False,
    )
    embeds.append(reward_embed)
    return tuple(embeds)


def _field_pages(
    title: str,
    fields: list[tuple[str, str]],
    color: discord.Color,
) -> list[discord.Embed]:
    pages: list[discord.Embed] = []
    page = discord.Embed(title=title[:256], color=color)
    character_count = len(title)
    for name, value in fields:
        field_size = len(name) + len(value)
        if len(page.fields) >= 20 or character_count + field_size > 5500:
            pages.append(page)
            page = discord.Embed(title=title[:256], color=color)
            character_count = len(title)
        page.add_field(name=name[:256], value=_truncate(value), inline=False)
        character_count += field_size
    if page.fields or not pages:
        pages.append(page)
    return pages


def build_quest_log_embeds(
    summary: QuestLogSummary,
    detailed: bool = False,
) -> tuple[discord.Embed, ...]:
    """Render recognized screenshot tasks as one consolidated raid plan."""

    overview = discord.Embed(
        title="Quest Log Summary",
        description=(
            f"Recognized **{len(summary.matches)}** task"
            f"{'s' if len(summary.matches) != 1 else ''} from the uploaded screenshot(s)."
        ),
        color=discord.Color.teal(),
    )
    match_lines = [
        f"{match.task.name} — {match.confidence:.0%} confidence"
        for match in summary.matches
    ]
    for index, chunk in enumerate(_line_chunks(match_lines), 1):
        suffix = f" ({index})" if len(_line_chunks(match_lines)) > 1 else ""
        overview.add_field(name=f"Recognized tasks{suffix}", value=chunk, inline=False)
    if summary.unmatched_lines:
        overview.add_field(
            name="Could not match confidently",
            value=_bullets(list(summary.unmatched_lines)),
            inline=False,
        )
    overview.set_footer(
        text="Review uncertain matches before relying on the checklist • PvE data from tarkov.dev"
    )
    embeds: list[discord.Embed] = [overview]

    prep_fields: list[tuple[str, str]] = []
    item_lines = []
    for requirement in summary.item_requirements:
        names = " / ".join(requirement.item_names)
        fir = " (Found in Raid)" if requirement.found_in_raid else ""
        tasks = ", ".join(requirement.task_names)
        item_lines.append(f"{_format_number(requirement.count)}× {names}{fir} — {tasks}")
    item_chunks = _line_chunks(item_lines)
    prep_fields.extend(
        (f"Items{f' ({index})' if len(item_chunks) > 1 else ''}", chunk)
        for index, chunk in enumerate(item_chunks, 1)
    )
    key_lines = [
        f"{requirement.key_name} — {', '.join(requirement.task_names)}"
        for requirement in summary.required_keys
    ]
    key_chunks = _line_chunks(key_lines)
    prep_fields.extend(
        (f"Keys{f' ({index})' if len(key_chunks) > 1 else ''}", chunk)
        for index, chunk in enumerate(key_chunks, 1)
    )
    embeds.extend(_field_pages("Combined Preparation", prep_fields, discord.Color.green()))

    map_fields: list[tuple[str, str]] = []
    for group in summary.map_groups:
        link = f"https://tarkov.dev/map/{quote(group.task_map.normalized_name)}"
        map_fields.append(
            (f"{group.task_map.name} — Map", f"[Open interactive map]({link})\n{_bullets(list(group.task_names))}")
        )
    if summary.tasks_without_map:
        map_fields.append(("Any / unspecified map", _bullets(list(summary.tasks_without_map))))
    embeds.extend(_field_pages("Tasks by Map", map_fields, discord.Color.blue()))

    if detailed:
        detail_fields: list[tuple[str, str]] = []
        for match in summary.matches:
            objectives = [
                ("(Optional) " if objective.optional else "") + objective.description
                for objective in match.task.objectives
            ]
            detail_fields.append((match.task.name, _bullets(objectives)))
        embeds.extend(
            _field_pages("Quest Objectives", detail_fields, discord.Color.orange())
        )

    return tuple(embeds)


_STATUS_EMOJI = {
    "ok": "🟢",
    "updating": "🔵",
    "unstable": "🟠",
    "down": "🔴",
}


def _status_emoji(status_code: str) -> str:
    return _STATUS_EMOJI.get(status_code.casefold(), "⚪")


def _discord_time(value: str) -> str:
    try:
        timestamp = int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return value
    return f"<t:{timestamp}:R>"


def build_server_status_embed(server_status: TarkovServerStatus) -> discord.Embed:
    general = server_status.general_status
    color_by_code = {
        "ok": discord.Color.green(),
        "updating": discord.Color.blue(),
        "unstable": discord.Color.orange(),
        "down": discord.Color.red(),
    }
    embed = discord.Embed(
        title="Escape from Tarkov Server Status",
        description=(
            f"{_status_emoji(general.status_code)} **{general.status_code}**"
            + (f" — {general.message}" if general.message else "")
        ),
        color=color_by_code.get(general.status_code.casefold(), discord.Color.light_grey()),
    )
    component_lines = [
        f"{_status_emoji(component.status_code)} **{component.name}:** "
        f"{component.status_code}"
        + (f" — {component.message}" if component.message else "")
        for component in server_status.components
    ]
    for index, chunk in enumerate(_line_chunks(component_lines), 1):
        suffix = f" ({index})" if len(_line_chunks(component_lines)) > 1 else ""
        embed.add_field(name=f"Components{suffix}", value=chunk, inline=False)

    active_messages = [message for message in server_status.messages if message.is_active]
    visible_messages = active_messages or list(server_status.messages[:3])
    if visible_messages:
        lines = []
        for message in visible_messages:
            content = message.content or "No additional details"
            state = "Active" if message.is_active else "Resolved"
            lines.append(
                f"{_status_emoji(message.status_code)} {state} {_discord_time(message.time)}: {content}"
            )
        embed.add_field(
            name="Active incidents" if active_messages else "Recent notices",
            value=_bullets(lines),
            inline=False,
        )
    else:
        embed.add_field(name="Incidents", value="No incidents reported", inline=False)

    embed.set_footer(
        text="No load, latency, player-count, or queue metrics are provided • json.tarkov.dev"
    )
    return embed
