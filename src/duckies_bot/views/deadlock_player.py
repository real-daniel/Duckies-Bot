"""Interactive disambiguation for Deadlock player-name searches."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from ..features.deadlock.formatter import (
    build_player_hero_embed,
    build_player_lookup_embed,
)
from ..features.deadlock.models import PlayerLookup, SteamProfile
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
        view = DeadlockPlayerLookupView(
            player,
            requester_id=self.parent_view.requester_id,
            service=self.parent_view.service,
        )
        view.message = self.parent_view.message
        await interaction.edit_original_response(
            embed=build_player_lookup_embed(player),
            content=None,
            view=view,
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


class _PlayerHeroSelect(discord.ui.Select["DeadlockPlayerLookupView"]):
    def __init__(self, parent: "DeadlockPlayerLookupView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Open detailed stats for a hero…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(
                        record.hero.name
                        if record.hero
                        else f"Hero {record.experience.hero_id}"
                    )[:100],
                    description=(
                        f"{record.experience.matches_played:,} matches · "
                        f"{record.experience.win_rate:.1%} WR"
                        if record.experience.win_rate is not None
                        else f"{record.experience.matches_played:,} matches"
                    )[:100],
                    value=str(index),
                )
                for index, record in enumerate(
                    parent.player.top_heroes[
                        parent.hero_page * 25 : (parent.hero_page + 1) * 25
                    ],
                    start=parent.hero_page * 25,
                )
            ],
            custom_id="deadlock_player_lookup:hero",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.hero_index = int(self.values[0])
        self.parent_view._sync_components()
        await interaction.response.edit_message(
            embed=build_player_hero_embed(
                self.parent_view.player,
                self.parent_view.hero_index,
            ),
            view=self.parent_view,
        )


class _PlayerHeroPageButton(discord.ui.Button["DeadlockPlayerLookupView"]):
    def __init__(self, *, direction: int) -> None:
        self.direction = direction
        super().__init__(
            label="Previous heroes" if direction < 0 else "More heroes",
            style=discord.ButtonStyle.secondary,
            custom_id=f"deadlock_player_lookup:hero_page:{direction}",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, DeadlockPlayerLookupView)
        view.hero_page += self.direction
        view.hero_index = None
        view._rebuild_components()
        await interaction.response.edit_message(
            embed=build_player_lookup_embed(view.player),
            view=view,
        )


class _PlayerModeButton(discord.ui.Button["DeadlockPlayerLookupView"]):
    def __init__(self, label: str, match_mode: str | None) -> None:
        self.match_mode = match_mode
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"deadlock_player_lookup:mode:{match_mode or 'all'}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, DeadlockPlayerLookupView)
        await interaction.response.defer()
        try:
            player = await view.service.player_lookup(
                view.player.profile.account_id,
                profile=view.player.profile,
                match_mode=self.match_mode,
            )
        except DeadlockAPIError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
            return
        except Exception:
            await interaction.followup.send(
                "Something unexpected went wrong while filtering that player.",
                ephemeral=True,
            )
            return
        view.player = player
        view.hero_index = None
        view.hero_page = 0
        view._rebuild_components()
        await interaction.edit_original_response(
            embed=build_player_lookup_embed(player),
            view=view,
        )


class _PlayerOverviewButton(discord.ui.Button["DeadlockPlayerLookupView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Overview",
            style=discord.ButtonStyle.secondary,
            custom_id="deadlock_player_lookup:overview",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, DeadlockPlayerLookupView)
        view.hero_index = None
        view._sync_components()
        await interaction.response.edit_message(
            embed=build_player_lookup_embed(view.player),
            view=view,
        )


class DeadlockPlayerLookupView(discord.ui.View):
    """Interactive mode filters and per-hero details for a player lookup."""

    def __init__(
        self,
        player: PlayerLookup,
        requester_id: int,
        service: "DeadlockService",
        *,
        timeout: float = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.player = player
        self.requester_id = requester_id
        self.service = service
        self.hero_index: int | None = None
        self.hero_page = 0
        self.message: discord.Message | None = None
        self._rebuild_components()

    def _rebuild_components(self) -> None:
        self.clear_items()
        self.add_item(_PlayerOverviewButton())
        self.add_item(_PlayerModeButton("All", None))
        self.add_item(_PlayerModeButton("Ranked", "ranked"))
        self.add_item(_PlayerModeButton("Standard", "unranked"))
        if self.player.top_heroes:
            self.add_item(_PlayerHeroSelect(self))
        hero_pages = (len(self.player.top_heroes) + 24) // 25
        if hero_pages > 1:
            previous = _PlayerHeroPageButton(direction=-1)
            previous.disabled = self.hero_page == 0
            self.add_item(previous)
            following = _PlayerHeroPageButton(direction=1)
            following.disabled = self.hero_page >= hero_pages - 1
            self.add_item(following)
        self._sync_components()

    def _sync_components(self) -> None:
        for child in self.children:
            if isinstance(child, _PlayerOverviewButton):
                child.style = (
                    discord.ButtonStyle.primary
                    if self.hero_index is None
                    else discord.ButtonStyle.secondary
                )
            elif isinstance(child, _PlayerModeButton):
                child.style = (
                    discord.ButtonStyle.success
                    if child.match_mode == self.player.match_mode
                    else discord.ButtonStyle.secondary
                )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the person who ran this lookup can change its filters.",
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
