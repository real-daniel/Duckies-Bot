"""Discord commands for reading MapleStory Quanah screenshots."""

import asyncio
from io import BytesIO
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from ..features.maplestory import STAT_COLORS, InvalidInventoryScreenshot, highlight_sockets
from ..features.maplestory_skills import InvalidSkillScreenshot, label_skill_tiers


class MapleStoryCog(commands.Cog):
    maple = app_commands.Group(name="maple", description="MapleStory tools")

    @maple.command(name="skills", description="Identify the tiers of the three Quanah reroll options")
    @app_commands.describe(screenshot="Original PNG showing the three Obtain Skills options")
    async def skills(self, interaction: discord.Interaction, screenshot: discord.Attachment) -> None:
        image_bytes = await self._read_screenshot(interaction, screenshot)
        if image_bytes is None:
            return

        try:
            preview, tiers = await asyncio.to_thread(label_skill_tiers, image_bytes)
        except InvalidSkillScreenshot:
            await interaction.followup.send(
                "Upload an original PNG showing all three Obtain Skills rows at the game's native UI size."
            )
            return

        lines = [f"**{position}** · {tier}" for position, tier in zip(("Top", "Middle", "Bottom"), tiers)]
        await interaction.followup.send(
            "\n".join(lines), file=discord.File(preview, filename="quanah-skill-tiers.png")
        )

    @maple.command(name="sockets", description="Box matching Quanah jewel sockets in an inventory PNG")
    @app_commands.describe(
        stat="Main stat to match, or All to skip the stat filter",
        screenshot="Original PNG cropped around the expanded 16-by-8 inventory grid",
        allow_prismatic="For stat filters, true allows one and false excludes it. Force always requires one.",
    )
    async def sockets(
        self,
        interaction: discord.Interaction,
        stat: Literal["All", "INT", "STR", "DEX", "LUK"],
        screenshot: discord.Attachment,
        allow_prismatic: Literal["true", "false", "force"] = "true",
    ) -> None:
        image_bytes = await self._read_screenshot(interaction, screenshot)
        if image_bytes is None:
            return

        selected_stat = None if stat == "All" else stat
        prismatic_filter = "force" if allow_prismatic == "force" else allow_prismatic == "true"
        try:
            image, matches = await asyncio.to_thread(
                highlight_sockets,
                image_bytes,
                selected_stat,
                allow_prismatic=prismatic_filter,
            )
        except InvalidInventoryScreenshot:
            await interaction.followup.send(
                "I couldn't locate the expanded 16-by-8 inventory grid. Upload an original PNG "
                "cropped around the full grid at its native size."
            )
            return

        if selected_stat is None:
            summary = f"**All · {len(matches)} three-socket items**"
        else:
            colors = ", ".join(STAT_COLORS[selected_stat])
            if allow_prismatic == "true":
                colors += ", plus up to one Prismatic per item"
            summary = f"**{stat} · {len(matches)} three-socket candidates**\nAllowed colors: {colors}."
        if allow_prismatic == "force":
            summary += "\nExactly one Prismatic socket required per item."
        lines = []
        for match in matches:
            sockets = ", ".join(color or "Unclear" for color in match.colors)
            note = " (check manually)" if None in match.colors else ""
            lines.append(f"Row {match.row}, column {match.column} · {sockets}{note}")
        files = [discord.File(image, filename="quanah-socket-matches.png")]
        if lines:
            details = "\n".join(lines)
            if len(summary) + len(details) + 2 <= 2000:
                summary += "\n\n" + details
            else:
                files.append(discord.File(BytesIO(details.encode("utf-8")), filename="quanah-sockets.txt"))
        await interaction.followup.send(summary, files=files)

    @staticmethod
    async def _read_screenshot(interaction: discord.Interaction, screenshot: discord.Attachment) -> bytes | None:
        if screenshot.size > 8 * 1024 * 1024:
            await interaction.response.send_message("Upload a PNG of 8 MB or smaller.", ephemeral=True)
            return None

        await interaction.response.defer()
        try:
            return await screenshot.read(use_cached=False)
        except discord.HTTPException:
            await interaction.followup.send("I couldn't download the attachment. Try uploading it again.")
            return None
