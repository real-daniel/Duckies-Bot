"""Button navigation for Deadlock scouting reports."""

from __future__ import annotations

import discord

from ..features.deadlock.formatter import (
    build_scout_overview_embeds,
    build_scout_player_embed,
)
from ..features.deadlock.models import MatchScout


class DeadlockScoutView(discord.ui.View):
    def __init__(
        self,
        scout: MatchScout,
        requester_id: int,
        highlighted_account_id: int | None = None,
        *,
        timeout: float = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.scout = scout
        self.requester_id = requester_id
        self.highlighted_account_id = highlighted_account_id
        self.player_index: int | None = None
        self.message: discord.Message | None = None
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the person who requested this scouting report can control it.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Previous player",
        style=discord.ButtonStyle.secondary,
        custom_id="deadlock_scout:previous",
    )
    async def previous_player(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["DeadlockScoutView"],
    ) -> None:
        del button
        if self.scout.players:
            self.player_index = (
                len(self.scout.players) - 1
                if self.player_index is None
                else (self.player_index - 1) % len(self.scout.players)
            )
        await self._render(interaction)

    @discord.ui.button(
        label="Overview",
        style=discord.ButtonStyle.primary,
        custom_id="deadlock_scout:overview",
    )
    async def overview(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["DeadlockScoutView"],
    ) -> None:
        del button
        self.player_index = None
        await self._render(interaction)

    @discord.ui.button(
        label="Next player",
        style=discord.ButtonStyle.secondary,
        custom_id="deadlock_scout:next",
    )
    async def next_player(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["DeadlockScoutView"],
    ) -> None:
        del button
        if self.scout.players:
            self.player_index = (
                0
                if self.player_index is None
                else (self.player_index + 1) % len(self.scout.players)
            )
        await self._render(interaction)

    async def _render(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        if self.player_index is None:
            await interaction.response.edit_message(
                embeds=list(
                    build_scout_overview_embeds(
                        self.scout,
                        self.highlighted_account_id,
                    )
                ),
                view=self,
            )
        else:
            embed = build_scout_player_embed(
                self.scout,
                self.player_index,
                self.highlighted_account_id,
            )
            await interaction.response.edit_message(embed=embed, view=self)

    def _sync_buttons(self) -> None:
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if child.custom_id == "deadlock_scout:overview":
                child.disabled = self.player_index is None
            elif child.custom_id in {
                "deadlock_scout:previous",
                "deadlock_scout:next",
            }:
                child.disabled = not self.scout.players

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
