"""Authorization helpers for interactive bot responses."""

from __future__ import annotations

import discord


def can_control_response(interaction: discord.Interaction, requester_id: int) -> bool:
    """Allow the requester or a server moderator to control a response."""
    if interaction.user.id == requester_id:
        return True

    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(
        interaction.guild is not None
        and permissions is not None
        and permissions.manage_messages
    )
