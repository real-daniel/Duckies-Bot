"""Interactive disambiguation for Deadlock player-name searches."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from ..features.deadlock.formatter import build_player_lookup_embed
from ..features.deadlock.models import SteamProfile
from ..providers.deadlock import DeadlockAPIError

if TYPE_CHECKING:
    from ..features.deadlock.service import DeadlockService


class _PlayerResultSelect(discord.ui.Select["DeadlockPlayerSearchView"]):
    def __init__(self, parent: "DeadlockPlayerSearchView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose the player matching the avatar…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"{index}. {profile.personaname}"[:100],
                    description=f"Account {profile.account_id}"[:100],
                    value=str(index - 1),
                )
                for index, profile in enumerate(parent.profiles, start=1)
            ],
            custom_id="deadlock_player_search:result",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        profile = self.parent_view.profiles[int(self.values[0])]
        await interaction.response.defer()
        try:
            player = await self.parent_view.service.player_lookup(
                profile.account_id,
                profile=profile,
            )
        except DeadlockAPIError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
            return
        except Exception:
            await interaction.followup.send(
                "Something unexpected went wrong while looking up that player.",
                ephemeral=True,
            )
            return
        self.parent_view.stop()
        await interaction.edit_original_response(
            embed=build_player_lookup_embed(player),
            view=None,
        )


class DeadlockPlayerSearchView(discord.ui.View):
    def __init__(
        self,
        profiles: tuple[SteamProfile, ...],
        requester_id: int,
        service: "DeadlockService",
        *,
        timeout: float = 180,
    ) -> None:
        if not profiles:
            raise ValueError("profiles cannot be empty")
        if len(profiles) > 10:
            raise ValueError("at most 10 profiles can be displayed")
        super().__init__(timeout=timeout)
        self.profiles = profiles
        self.requester_id = requester_id
        self.service = service
        self.message: discord.Message | None = None
        self.add_item(_PlayerResultSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the person who searched can choose a player.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
