"""Reusable retry control for failed Discord lookups."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias

import discord

from .access import can_control_response


RetryCallback: TypeAlias = Callable[
    [discord.Interaction, "LookupRetryView"],
    Awaitable[None],
]


class LookupRetryView(discord.ui.View):
    """Let the original requester repeat a failed lookup in place."""

    def __init__(
        self,
        requester_id: int,
        retry: RetryCallback,
        *,
        timeout: float = 180,
    ) -> None:
        super().__init__(timeout=timeout)
        self.requester_id = requester_id
        self.retry = retry
        self.message: discord.Message | None = None

    @discord.ui.button(
        label="Retry",
        emoji="↻",
        style=discord.ButtonStyle.primary,
        custom_id="lookup_retry:retry",
    )
    async def retry_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["LookupRetryView"],
    ) -> None:
        button.disabled = True
        await interaction.response.edit_message(
            content="Retrying lookup…",
            view=self,
        )
        await self.retry(interaction, self)

    async def show_error(
        self,
        interaction: discord.Interaction,
        message: str,
    ) -> None:
        """Restore the button after another failed attempt."""
        self.retry_button.disabled = False
        await interaction.edit_original_response(content=message, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if can_control_response(interaction, self.requester_id):
            return True
        await interaction.response.send_message(
            "Only the person who ran this lookup or a server moderator can retry it.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        self.retry_button.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
