"""Shared Discord presentation system for Duckies Bot features."""

from __future__ import annotations

import discord


class Palette:
    """Muted Tarkov-inspired colors used consistently across bot responses."""

    OLIVE = discord.Color(0x8F9B67)
    GOLD = discord.Color(0xC7A86B)
    TEAL = discord.Color(0x4E9F91)
    BLUE = discord.Color(0x628DB5)
    ORANGE = discord.Color(0xC9824B)
    RED = discord.Color(0xB85C5C)
    SLATE = discord.Color(0x68727D)


_TONE_COLORS = {
    "primary": Palette.OLIVE,
    "market": Palette.GOLD,
    "info": Palette.BLUE,
    "success": Palette.TEAL,
    "warning": Palette.ORANGE,
    "danger": Palette.RED,
    "muted": Palette.SLATE,
}


def make_embed(
    title: str,
    description: str | None = None,
    *,
    tone: str = "primary",
    url: str | None = None,
) -> discord.Embed:
    """Create a themed embed. New features should start with this helper."""

    embed = discord.Embed(
        title=title[:256],
        description=description[:4096] if description else None,
        url=url,
        color=_TONE_COLORS.get(tone, Palette.OLIVE),
    )
    return embed


def finish_embed(
    embed: discord.Embed,
    section: str,
    *,
    source: str = "json.tarkov.dev",
    context: str = "PvE data",
) -> discord.Embed:
    """Apply the canonical footer shared by bot responses."""

    embed.set_footer(text=f"{section}  ·  {context} from {source}")
    return embed


def field_name(label: str) -> str:
    """Format a plain, consistent section label."""

    return label
