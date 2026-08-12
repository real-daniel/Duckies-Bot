"""Discord commands for reusable Steam account links."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..features.accounts import SteamIDError, parse_steam_id
from ..presentation import finish_embed, make_embed
from ..storage import AccountAlreadyLinkedError, SteamLinkRepository


class SteamCog(commands.Cog):
    steam = app_commands.Group(name="steam", description="Manage your linked Steam account")

    def __init__(self, bot: commands.Bot, links: SteamLinkRepository) -> None:
        self.bot = bot
        self.links = links

    @steam.command(name="link", description="Link your Discord user to a Steam account")
    @app_commands.describe(steam_id="SteamID64, SteamID3, SteamID2, or numeric profile URL")
    async def link(self, interaction: discord.Interaction, steam_id: str) -> None:
        try:
            account = parse_steam_id(steam_id)
            await self.links.link(interaction.user.id, account)
        except (SteamIDError, AccountAlreadyLinkedError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        embed = make_embed(
            "Steam account linked",
            "Commands that accept a Steam account will now use this account by default.",
            tone="success",
            url=account.profile_url,
        )
        embed.add_field(name="SteamID64", value=str(account.steam_id64), inline=True)
        embed.add_field(name="Account ID", value=str(account.account_id), inline=True)
        finish_embed(embed, "Steam account", source="Steam Community", context="Profile")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @steam.command(name="show", description="Show a linked Steam account")
    @app_commands.describe(user="Discord user; defaults to you")
    async def show(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        account = await self.links.get(target.id)
        if account is None:
            await interaction.response.send_message(
                f"{target.mention} has not linked a Steam account.", ephemeral=True
            )
            return

        embed = make_embed(
            f"Steam account for {target.display_name}",
            url=account.profile_url,
            tone="info",
        )
        embed.add_field(name="SteamID64", value=str(account.steam_id64), inline=True)
        embed.add_field(name="Account ID", value=str(account.account_id), inline=True)
        finish_embed(embed, "Steam account", source="Steam Community", context="Profile")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @steam.command(name="unlink", description="Remove your linked Steam account")
    async def unlink(self, interaction: discord.Interaction) -> None:
        removed = await self.links.unlink(interaction.user.id)
        message = (
            "Your Steam account link was removed."
            if removed
            else "You do not have a linked Steam account."
        )
        await interaction.response.send_message(message, ephemeral=True)
