"""Discord commands for PvE Tarkov information."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..features.tarkov.formatter import (
    build_item_embed,
    build_quest_log_embeds,
    build_server_status_embed,
    build_task_embeds,
)
from ..features.tarkov.quest_log import QuestLogService
from ..features.tarkov.service import TarkovService
from ..providers.tarkov.errors import InvalidQuestLogImageError, TarkovAPIError


LOGGER = logging.getLogger(__name__)


class TarkovCog(commands.Cog):
    tarkov = app_commands.Group(name="tarkov", description="Look up PvE Tarkov information")

    def __init__(
        self,
        bot: commands.Bot,
        service: TarkovService,
        quest_log_service: QuestLogService,
    ) -> None:
        self.bot = bot
        self.service = service
        self.quest_log_service = quest_log_service

    @tarkov.command(name="item", description="Look up an item using PvE prices")
    @app_commands.describe(name="Full or partial item name")
    async def item(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(thinking=True)
        try:
            item = await self.service.lookup_item(name)
        except TarkovAPIError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unexpected error while looking up Tarkov item %r", name)
            await interaction.followup.send(
                "Something unexpected went wrong while looking up that item.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(embed=build_item_embed(item))

    @tarkov.command(name="task", description="Look up a PvE task and prepare for the raid")
    @app_commands.describe(
        name="Full or partial task name",
        detailed="Include every objective and all rewards",
    )
    async def task(
        self,
        interaction: discord.Interaction,
        name: str,
        detailed: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            task = await self.service.lookup_task(name)
        except TarkovAPIError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unexpected error while looking up Tarkov task %r", name)
            await interaction.followup.send(
                "Something unexpected went wrong while looking up that task.",
                ephemeral=True,
            )
            return

        embeds = build_task_embeds(task, detailed=detailed)
        for index in range(0, len(embeds), 10):
            await interaction.followup.send(embeds=list(embeds[index : index + 10]))

    @tarkov.command(
        name="quest-log",
        description="Read quest-log screenshots and build one combined raid checklist",
    )
    @app_commands.describe(
        screenshot="Screenshot with visible quest titles",
        screenshot_2="Optional additional quest-log screenshot",
        screenshot_3="Optional additional quest-log screenshot",
        screenshot_4="Optional additional quest-log screenshot",
        detailed="Include the objectives for every recognized task",
    )
    async def quest_log(
        self,
        interaction: discord.Interaction,
        screenshot: discord.Attachment,
        screenshot_2: discord.Attachment | None = None,
        screenshot_3: discord.Attachment | None = None,
        screenshot_4: discord.Attachment | None = None,
        detailed: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)
        attachments = [
            attachment
            for attachment in (screenshot, screenshot_2, screenshot_3, screenshot_4)
            if attachment is not None
        ]
        try:
            images = [await _read_screenshot(attachment) for attachment in attachments]
            summary = await self.quest_log_service.analyze(images)
        except TarkovAPIError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unexpected error while reading a Tarkov quest log")
            await interaction.followup.send(
                "Something unexpected went wrong while reading that quest log.",
                ephemeral=True,
            )
            return

        embeds = build_quest_log_embeds(summary, detailed=detailed)
        for index in range(0, len(embeds), 10):
            await interaction.followup.send(embeds=list(embeds[index : index + 10]))

    @tarkov.command(name="status", description="Check all reported EFT server statuses")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            server_status = await self.service.get_server_status()
        except TarkovAPIError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unexpected error while checking Tarkov server status")
            await interaction.followup.send(
                "Something unexpected went wrong while checking server status.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(embed=build_server_status_embed(server_status))


async def _read_screenshot(attachment: discord.Attachment) -> bytes:
    allowed_extensions = (".png", ".jpg", ".jpeg", ".webp")
    content_type = (attachment.content_type or "").casefold()
    if not content_type.startswith("image/") and not attachment.filename.casefold().endswith(
        allowed_extensions
    ):
        raise InvalidQuestLogImageError()
    if attachment.size > 12 * 1024 * 1024:
        raise InvalidQuestLogImageError("Each screenshot must be 12 MB or smaller.")
    image = await attachment.read(use_cached=True)
    if not image:
        raise InvalidQuestLogImageError("One of the uploaded screenshots was empty.")
    return image
