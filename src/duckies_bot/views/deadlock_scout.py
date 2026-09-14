"""Dropdown navigation for graphical Deadlock scouting reports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from io import BytesIO

import discord

from ..features.deadlock.models import MatchScout


SCOUT_GRAPHIC_FILENAME = "deadlock-scout.png"


class _ScoutPageSelect(discord.ui.Select["DeadlockScoutView"]):
    def __init__(self, parent: "DeadlockScoutView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose the lobby overview or a player…",
            min_values=1,
            max_values=1,
            options=parent._page_options(),
            custom_id="deadlock_scout:page",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        self.parent_view.player_index = None if value == "overview" else int(value)
        await self.parent_view.render_interaction(interaction)


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
        self.attachment_renderer: Callable[["DeadlockScoutView"], Awaitable[bytes]] | None = None
        self.message: discord.Message | None = None
        self.page_select = _ScoutPageSelect(self)
        self.add_item(self.page_select)
        self._sync_select()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the person who requested this scouting report can control it.",
            ephemeral=True,
        )
        return False

    async def render_interaction(self, interaction: discord.Interaction) -> None:
        self._sync_select()
        if self.attachment_renderer is None:
            await interaction.response.edit_message(embed=None, view=self)
            return
        await interaction.response.defer()
        graphic = await self.attachment_renderer(self)
        await interaction.edit_original_response(
            embed=None,
            attachments=[discord.File(BytesIO(graphic), filename=SCOUT_GRAPHIC_FILENAME)],
            view=self,
        )

    def _page_options(self) -> list[discord.SelectOption]:
        options = [
            discord.SelectOption(
                label="Lobby overview",
                value="overview",
                description=f"Compare all {len(self.scout.players)} players",
                emoji="📊",
            )
        ]
        for index, player in enumerate(self.scout.players[:24]):
            live = player.player
            hero = player.hero.name if player.hero else (
                f"Hero {live.hero_id}" if live.hero_id is not None else "Unknown hero"
            )
            team = "Amber" if live.team == 2 else "Sapphire" if live.team == 3 else "Unknown team"
            label = live.steam_name.strip() or f"Player {index + 1}"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(index),
                    description=f"{team} · {hero}"[:100],
                )
            )
        return options

    def _sync_select(self) -> None:
        selected = "overview" if self.player_index is None else str(self.player_index)
        for option in self.page_select.options:
            option.default = option.value == selected

    async def on_timeout(self) -> None:
        self.page_select.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
