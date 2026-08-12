"""Discord presentation helpers for PvE Tarkov data."""

from datetime import datetime
from urllib.parse import quote

import discord

from ...presentation import field_name, finish_embed, make_embed

from .models import (
    QuestLogSummary,
    ProfitResult,
    TarkovServerStatus,
    TarkovItem,
    TarkovTask,
    TraderFlipResult,
    TaskItemRequirement,
    TaskRewards,
)


def format_roubles(value: int | None) -> str:
    return "Unavailable" if value is None else f"{value:,} ₽"


def _format_signed_roubles(value: int) -> str:
    return f"{value:+,} ₽"


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
    description = "**PvE market snapshot**"
    if item.short_name and item.short_name.casefold() != item.name.casefold():
        description += f"  •  `{item.short_name}`"

    embed = make_embed(item.name, description, tone="market")
    if item.icon_url:
        embed.set_thumbnail(url=item.icon_url)

    embed.add_field(
        name=field_name("24h average"),
        value=f"**{format_roubles(item.avg24h_price)}**",
        inline=True,
    )
    embed.add_field(
        name=field_name("24h range"),
        value=f"{format_roubles(item.low24h_price)} – {format_roubles(item.high24h_price)}",
        inline=True,
    )
    embed.add_field(
        name=field_name("Latest low"),
        value=f"**{format_roubles(item.last_low_price)}**",
        inline=True,
    )

    trader = item.best_trader_offer
    trader_value = (
        f"**{trader.vendor_name}**  •  {format_roubles(trader.price_rub)}"
        if trader is not None
        else "No trader offer available"
    )
    embed.add_field(
        name=field_name("Best trader sale"),
        value=trader_value[:1024],
        inline=False,
    )

    if item.task_names:
        visible_tasks = item.task_names[:5]
        task_value = "\n".join(f"• {name}" for name in visible_tasks)
        remaining = len(item.task_names) - len(visible_tasks)
        if remaining:
            task_value += f"\n…and {remaining} more"
    else:
        task_value = "Not currently required for a task"
    embed.add_field(
        name=field_name("Task uses"),
        value=task_value[:1024],
        inline=False,
    )
    return finish_embed(embed, "ITEM LOOKUP")


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
    badges = []
    if task.kappa_required:
        badges.append("`KAPPA REQUIRED`")
    if task.lightkeeper_required:
        badges.append("`LIGHTKEEPER`")
    if task.faction_name and task.faction_name.casefold() != "any":
        badges.append(f"`{task.faction_name.upper()}`")
    description = f"> **{task.trader_name}**  •  {level}  •  PvE"
    if badges:
        description += "\n" + "  ".join(badges)
    embed = make_embed(task.name, description, tone="primary", url=task.wiki_url)
    if task.image_url:
        embed.set_thumbnail(url=task.image_url)

    embed.add_field(
        name=field_name("Maps"),
        value=_bullets(_map_links(task), "No specific map"),
        inline=False,
    )
    embed.add_field(
        name=field_name("Bring / collect"),
        value=_bullets([_format_requirement(req) for req in task.required_items]),
        inline=False,
    )
    embed.add_field(
        name=field_name("Required keys"),
        value=_bullets(list(task.required_keys)),
        inline=False,
    )

    objective_lines = [
        ("(Optional) " if objective.optional else "") + objective.description
        for objective in task.objectives
    ]
    visible = objective_lines if detailed else objective_lines[:8]
    if not detailed and len(objective_lines) > len(visible):
        visible.append(f"…and {len(objective_lines) - len(visible)} more; use detailed mode")
    embed.add_field(name=field_name("Raid plan"), value=_bullets(visible), inline=False)
    embed.add_field(
        name=field_name("Prerequisites"),
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
    embed.add_field(
        name=field_name("Completion rewards"),
        value=_bullets(rewards),
        inline=False,
    )
    return finish_embed(embed, "TASK INTEL")


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
        page = make_embed(
            f"{task.name} · Objectives",
            "Detailed objective breakdown",
            tone="primary",
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
                name=(
                    f"{index:02d} · "
                    f"{objective.objective_type.replace('Item', ' Item').title()}"
                )[:256],
                value=_truncate("\n".join(details)),
                inline=False,
            )
        embeds.append(finish_embed(page, "TASK OBJECTIVES"))

    reward_embed = make_embed(
        f"{task.name} · Rewards",
        f"**{task.experience:,} XP** on completion",
        tone="market",
    )
    reward_embed.add_field(
        name=field_name("On start"),
        value=_bullets(_reward_lines(task.start_rewards)),
        inline=False,
    )
    reward_embed.add_field(
        name=field_name("On completion"),
        value=_bullets(_reward_lines(task.finish_rewards)),
        inline=False,
    )
    reward_embed.add_field(
        name=field_name("On failure"),
        value=_bullets(_reward_lines(task.failure_rewards)),
        inline=False,
    )
    embeds.append(finish_embed(reward_embed, "TASK REWARDS"))
    return tuple(embeds)


def _field_pages(
    title: str,
    fields: list[tuple[str, str]],
    tone: str,
    section: str,
    description: str | None = None,
) -> list[discord.Embed]:
    pages: list[discord.Embed] = []
    page_number = 1
    page = make_embed(title, description, tone=tone)
    character_count = len(title) + len(description or "")
    for name, value in fields:
        field_size = len(name) + len(value)
        if len(page.fields) >= 20 or character_count + field_size > 5500:
            pages.append(finish_embed(page, f"{section} • PAGE {page_number}"))
            page_number += 1
            page = make_embed(f"{title}  •  Page {page_number}", description, tone=tone)
            character_count = len(title) + len(description or "")
        page.add_field(name=name[:256], value=_truncate(value), inline=False)
        character_count += field_size
    if page.fields or not pages:
        footer = f"{section} • PAGE {page_number}" if page_number > 1 else section
        pages.append(finish_embed(page, footer))
    return pages


def build_quest_log_embeds(
    summary: QuestLogSummary,
    detailed: bool = False,
) -> tuple[discord.Embed, ...]:
    """Render recognized screenshot tasks as one consolidated raid plan."""

    overview = make_embed(
        "Quest Log Summary",
        (
            f"> Recognized **{len(summary.matches)} task"
            f"{'s' if len(summary.matches) != 1 else ''}** from your screenshot"
            f"{'s' if len(summary.matches) != 1 else ''}."
        ),
        tone="success",
    )
    match_lines = [
        f"{match.task.name} — {match.confidence:.0%} confidence"
        for match in summary.matches
    ]
    for index, chunk in enumerate(_line_chunks(match_lines), 1):
        suffix = f" ({index})" if len(_line_chunks(match_lines)) > 1 else ""
        overview.add_field(
            name=field_name(f"Recognized tasks{suffix}"),
            value=chunk,
            inline=False,
        )
    if summary.unmatched_lines:
        overview.add_field(
            name=field_name("Needs review"),
            value=_bullets(list(summary.unmatched_lines)),
            inline=False,
        )
    embeds: list[discord.Embed] = [finish_embed(overview, "QUEST LOG OCR")]

    prep_fields: list[tuple[str, str]] = []
    item_lines = []
    for requirement in summary.item_requirements:
        names = " / ".join(requirement.item_names)
        fir = " (Found in Raid)" if requirement.found_in_raid else ""
        tasks = ", ".join(requirement.task_names)
        item_lines.append(f"{_format_number(requirement.count)}× {names}{fir} — {tasks}")
    item_chunks = _line_chunks(item_lines)
    prep_fields.extend(
        (field_name(f"Items{f' ({index})' if len(item_chunks) > 1 else ''}"), chunk)
        for index, chunk in enumerate(item_chunks, 1)
    )
    key_lines = [
        f"{requirement.key_name} — {', '.join(requirement.task_names)}"
        for requirement in summary.required_keys
    ]
    key_chunks = _line_chunks(key_lines)
    prep_fields.extend(
        (field_name(f"Keys{f' ({index})' if len(key_chunks) > 1 else ''}"), chunk)
        for index, chunk in enumerate(key_chunks, 1)
    )
    embeds.extend(
        _field_pages(
            "Combined Preparation",
            prep_fields,
            "primary",
            "RAID PREP",
            "Everything required across the recognized tasks.",
        )
    )

    map_fields: list[tuple[str, str]] = []
    for group in summary.map_groups:
        link = f"https://tarkov.dev/map/{quote(group.task_map.normalized_name)}"
        map_fields.append(
            (
                group.task_map.name,
                f"[Open interactive map ↗]({link})\n{_bullets(list(group.task_names))}",
            )
        )
    if summary.tasks_without_map:
        map_fields.append(
            (field_name("Any / unspecified map"), _bullets(list(summary.tasks_without_map)))
        )
    embeds.extend(
        _field_pages(
            "Tasks by Map",
            map_fields,
            "info",
            "MAP PLAN",
            "Group compatible objectives before choosing a raid.",
        )
    )

    if detailed:
        detail_fields: list[tuple[str, str]] = []
        for match in summary.matches:
            objectives = [
                ("(Optional) " if objective.optional else "") + objective.description
                for objective in match.task.objectives
            ]
            detail_fields.append((match.task.name, _bullets(objectives)))
        embeds.extend(
            _field_pages(
                "Quest Objectives",
                detail_fields,
                "warning",
                "QUEST OBJECTIVES",
                "Detailed checklist for each recognized task.",
            )
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
    tone_by_code = {
        "ok": "success",
        "updating": "info",
        "unstable": "warning",
        "down": "danger",
    }
    embed = make_embed(
        "EFT Service Status",
        (
            f"> {_status_emoji(general.status_code)}  **GLOBAL • {general.status_code.upper()}**"
            + (f" — {general.message}" if general.message else "")
        ),
        tone=tone_by_code.get(general.status_code.casefold(), "muted"),
    )
    component_lines = [
        f"{_status_emoji(component.status_code)} **{component.name}:** "
        f"{component.status_code}"
        + (f" — {component.message}" if component.message else "")
        for component in server_status.components
    ]
    for index, chunk in enumerate(_line_chunks(component_lines), 1):
        suffix = f" ({index})" if len(_line_chunks(component_lines)) > 1 else ""
        embed.add_field(
            name=field_name(f"Services{suffix}"),
            value=chunk,
            inline=False,
        )

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
            name=field_name("Active incidents" if active_messages else "Recent notices"),
            value=_bullets(lines),
            inline=False,
        )
    else:
        embed.add_field(
            name=field_name("Incidents"),
            value="No incidents reported",
            inline=False,
        )

    embed.add_field(
        name=field_name("Coverage"),
        value="The source does not expose load, latency, player count, or queue metrics.",
        inline=False,
    )
    return finish_embed(embed, "LIVE STATUS")


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "Unknown"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def build_profit_embeds(
    results: tuple[ProfitResult, ...],
    recipe_type: str,
    sort_by: str = "profit",
) -> tuple[discord.Embed, ...]:
    label = "Craft" if recipe_type == "craft" else "Barter"
    if not results:
        empty = make_embed(
            f"PvE {label} Profit",
            "No recipes had enough pricing data to rank.",
            tone="warning",
        )
        return (finish_embed(empty, f"{label.upper()} PROFIT"),)

    fields: list[tuple[str, str]] = []
    for rank, result in enumerate(results, 1):
        source = result.source_name
        if result.source_level is not None:
            source += f" LL{result.source_level}" if recipe_type == "barter" else f" {result.source_level}"
        lines = [
            f"**{_format_signed_roubles(result.gross_profit)} gross**",
            f"`COST` {format_roubles(result.material_cost)}  →  "
            f"`VALUE` {format_roubles(result.result_value)}",
            f"**{source}**  •  Sold via {result.result_price_source}",
        ]
        if result.roi_percent is not None:
            lines.append(f"`ROI` {result.roi_percent:.1f}%")
        if recipe_type == "craft":
            lines.append(
                f"`TIME` {_format_duration(result.duration_seconds)}  •  "
                f"`PER HOUR` {format_roubles(result.profit_per_hour)}"
            )
            if result.tool_capital:
                lines.append(f"`TOOLS` {format_roubles(result.tool_capital)} reusable capital")
        elif result.buy_limit is not None:
            lines.append(f"`LIMIT` {result.buy_limit} per restock")

        visible_inputs = result.ingredients[:4]
        input_text = ", ".join(
            f"{_format_number(item.count)}× {item.item_name}"
            + (" (tool)" if item.reusable else "")
            for item in visible_inputs
        )
        if len(result.ingredients) > len(visible_inputs):
            input_text += f", +{len(result.ingredients) - len(visible_inputs)} more"
        lines.append(f"**Inputs**  {input_text}")
        if result.task_unlock_name:
            lines.append(f"🔒 **Task unlock**  {result.task_unlock_name}")
        if result.warnings:
            lines.append("*Note: " + "; ".join(result.warnings) + "*")
        fields.append(
            (
                f"#{rank} · "
                f"{_format_number(result.result_count)}× {result.result_item_name}",
                "\n".join(lines),
            )
        )

    sort_label = "profit/hour" if sort_by == "hourly" else "gross profit"
    embeds = _field_pages(
        f"Top PvE {label} Profit",
        fields,
        "market",
        f"{label.upper()} PROFIT",
        (
            f"Ranked by **{sort_label}** using recent median flea floors.\n"
            "*Gross estimates exclude flea fees, fuel, and personal trader restrictions.*"
        ),
    )
    return tuple(embeds)


def build_flip_embeds(
    results: tuple[TraderFlipResult, ...],
    include_task_locked: bool = False,
    pmc_level: int | None = None,
    high_liquidity_only: bool = False,
) -> tuple[discord.Embed, ...]:
    filters = ["task locks included" if include_task_locked else "task locks excluded"]
    if pmc_level is not None:
        filters.append(f"PMC {pmc_level}")
    if high_liquidity_only:
        filters.append("high liquidity")
    filter_summary = " · ".join(filters)
    if not results:
        empty = make_embed(
            "Top PvE Trader Flips",
            f"No positive flips matched the selected filters.\n`{filter_summary.upper()}`",
            tone="warning",
        )
        return (finish_embed(empty, "TRADER FLIPS"),)

    fields: list[tuple[str, str]] = []
    for rank, result in enumerate(results, 1):
        trader = result.trader_name
        if result.min_trader_level is not None:
            trader += f" LL{result.min_trader_level}"
        if result.required_player_level is not None:
            trader += f" (PMC {result.required_player_level}+)"
        lines = [
            f"**{_format_signed_roubles(result.gross_profit_each)} gross each**",
            f"`BUY` {format_roubles(result.trader_price)} from **{trader}**  →  "
            f"`FLEA` {format_roubles(result.flea_price)}",
            f"`ROI` {result.roi_percent:.1f}%  ·  `{result.price_source.upper()}`",
        ]
        if result.buy_limit is not None:
            lines.append(
                f"`LIMIT` {result.buy_limit} per restock  ·  "
                f"`MAX GROSS` {format_roubles(result.gross_profit_each * result.buy_limit)}"
            )
        elif result.restock_amount is not None:
            lines.append(f"`REPORTED STOCK` {result.restock_amount:,}")
        if result.task_unlock_name:
            lines.append(f"**Task unlock:** {result.task_unlock_name}")
        notes = []
        if result.low_liquidity:
            notes.append("low flea liquidity")
        if notes:
            lines.append("*Note: " + "; ".join(notes) + "*")
        fields.append((f"#{rank} · {result.item_name}", "\n".join(lines)))

    return tuple(
        _field_pages(
            "Top PvE Trader Flips",
            fields,
            "market",
            "TRADER FLIPS",
            (
                f"`{filter_summary.upper()}`\n"
                "Buy from a trader and relist on the PvE flea market.\n"
                "*PMC filtering estimates loyalty from level only; reputation and spend "
                "are not checked. Gross estimates exclude flea fees.*"
            ),
        )
    )
