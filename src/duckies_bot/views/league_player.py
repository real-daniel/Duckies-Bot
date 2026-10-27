"""Interactive recent-match browser for League player lookups."""

from __future__ import annotations

import discord

from ..features.league.formatter import build_match_embed, build_player_embed, queue_name
from ..features.league.models import LeaguePlayerLookup
from .access import can_control_response


class _MatchSelect(discord.ui.Select["LeaguePlayerView"]):
    def __init__(self, parent: "LeaguePlayerView") -> None:
        self.parent_view = parent
        options: list[discord.SelectOption] = []
        for index, match in enumerate(parent.lookup.matches):
            player = match.participant(parent.lookup.account.puuid)
            if player is None:
                continue
            result = f"#{player.placement}" if player.placement else ("W" if player.win else "L")
            options.append(
                discord.SelectOption(
                    label=(
                        f"{index + 1}. {result} · {player.champion_name} · "
                        f"{queue_name(match)}"
                    )[:100],
                    description=(
                        f"{player.kills}/{player.deaths}/{player.assists} · "
                        f"{player.creep_score} CS · {match.duration_seconds // 60}:"
                        f"{match.duration_seconds % 60:02d}"
                    )[:100],
                    value=str(index),
                )
            )
        super().__init__(
            placeholder="Open detailed stats for a recent match…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="league_player:match",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.match_index = int(self.values[0])
        self.parent_view._sync_components()
        await interaction.response.edit_message(
            embed=build_match_embed(
                self.parent_view.lookup,
                self.parent_view.match_index,
            ),
            view=self.parent_view,
        )


class _OverviewButton(discord.ui.Button["LeaguePlayerView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Player overview",
            style=discord.ButtonStyle.primary,
            custom_id="league_player:overview",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, LeaguePlayerView)
        view.match_index = None
        view._sync_components()
        await interaction.response.edit_message(
            embed=build_player_embed(view.lookup),
            view=view,
        )


class LeaguePlayerView(discord.ui.View):
    def __init__(
        self,
        lookup: LeaguePlayerLookup,
        requester_id: int,
        *,
        timeout: float = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.lookup = lookup
        self.requester_id = requester_id
        self.match_index: int | None = None
        self.message: discord.Message | None = None
        self.add_item(_OverviewButton())
        if any(match.participant(lookup.account.puuid) is not None for match in lookup.matches):
            self.add_item(_MatchSelect(self))
        self._sync_components()

    def _sync_components(self) -> None:
        for child in self.children:
            if isinstance(child, _OverviewButton):
                child.style = (
                    discord.ButtonStyle.primary
                    if self.match_index is None
                    else discord.ButtonStyle.secondary
                )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if can_control_response(interaction, self.requester_id):
            return True
        await interaction.response.send_message(
            "Only the person who ran this lookup or a server moderator can browse its matches.",
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

