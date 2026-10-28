"""Discord commands for League of Legends player information."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..features.league import LeagueService
from ..features.league.formatter import build_player_embed
from ..providers.riot import PLATFORM_REGIONS, RiotAPIError
from ..views.league_player import LeaguePlayerView


LOGGER = logging.getLogger(__name__)

_REGION_NAMES = {
    "BR1": "Brazil",
    "EUN1": "Europe Nordic & East",
    "EUW1": "Europe West",
    "JP1": "Japan",
    "KR": "Korea",
    "LA1": "Latin America North",
    "LA2": "Latin America South",
    "ME1": "Middle East",
    "NA1": "North America",
    "OC1": "Oceania",
    "PH2": "Philippines",
    "RU": "Russia",
    "SG2": "Singapore",
    "TH2": "Thailand",
    "TR1": "Türkiye",
    "TW2": "Taiwan",
    "VN2": "Vietnam",
}


class LeagueCog(commands.Cog):
    league = app_commands.Group(
        name="league",
        description="Look up League of Legends information",
    )

    def __init__(self, bot: commands.Bot, service: LeagueService) -> None:
        self.bot = bot
        self.service = service

    @league.command(name="player", description="View ranked stats and all-mode match history")
    @app_commands.describe(
        riot_id="Exact Riot ID in the form Game Name#TAG",
        region="League platform where this account plays",
        matches="Number of recent matches to load from every mode (5–15)",
    )
    @app_commands.choices(
        region=[
            app_commands.Choice(name=f"{_REGION_NAMES[value]} ({value})", value=value)
            for value in PLATFORM_REGIONS
        ]
    )
    async def player(
        self,
        interaction: discord.Interaction,
        riot_id: str,
        region: str = "NA1",
        matches: app_commands.Range[int, 5, 15] = 10,
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            lookup = await self.service.player_lookup(
                riot_id,
                region,
                match_count=matches,
            )
        except (ValueError, RiotAPIError) as exc:
            message = exc.user_message if isinstance(exc, RiotAPIError) else str(exc)
            await interaction.followup.send(message, ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unexpected error during League player lookup")
            await interaction.followup.send(
                "Something unexpected went wrong while looking up that League player.",
                ephemeral=True,
            )
            return

        view = LeaguePlayerView(lookup, requester_id=interaction.user.id)
        view.message = await interaction.followup.send(
            embed=build_player_embed(lookup),
            view=view,
            wait=True,
        )

