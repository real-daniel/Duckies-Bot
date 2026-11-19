"""Discord command for filtering MapleStory Quanah jewel sockets."""

import asyncio
from io import BytesIO
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from ..features.maplestory import STAT_COLORS, InvalidInventoryScreenshot, highlight_sockets


class MapleStoryCog(commands.Cog):
    @app_commands.command(name="sockets", description="Box matching Quanah jewel sockets in an inventory PNG")
    @app_commands.describe(
        stat="Main stat to match across three sockets",
        screenshot="Original PNG cropped around the expanded 16-by-8 inventory grid",
        allow_prismatic="Allow one Prismatic socket per item (default: yes)",
    )
    async def sockets(
        self,
        interaction: discord.Interaction,
        stat: Literal["INT", "STR", "DEX", "LUK"],
        screenshot: discord.Attachment,
        allow_prismatic: bool = True,
    ) -> None:
        if screenshot.size > 8 * 1024 * 1024:
            await interaction.response.send_message("Upload a PNG of 8 MB or smaller.", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            image_bytes = await screenshot.read(use_cached=True)
        except discord.HTTPException:
            await interaction.followup.send("I couldn't download the attachment. Try uploading it again.")
            return

        try:
            image, matches = await asyncio.to_thread(
                highlight_sockets, image_bytes, stat, allow_prismatic=allow_prismatic
            )
        except InvalidInventoryScreenshot:
            await interaction.followup.send(
                "I couldn't locate the expanded 16-by-8 inventory grid. Upload an original PNG "
                "cropped around the full grid at its native size."
            )
            return

        colors = ", ".join(STAT_COLORS[stat])
        if allow_prismatic:
            colors += ", plus up to one Prismatic per item"
        summary = f"**{stat} · {len(matches)} three-socket candidates**\nAllowed colors: {colors}."
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
