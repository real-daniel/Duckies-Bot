"""Discord bot composition and lifecycle management."""

import logging

import discord
from discord.ext import commands

from .cogs.tarkov import TarkovCog
from .config import Settings, load_settings
from .features.tarkov.service import TarkovService
from .features.tarkov.quest_log import QuestLogService
from .providers.ocr import RapidOCRProvider
from .providers.tarkov.client import TarkovClient


LOGGER = logging.getLogger(__name__)


class DuckiesBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.settings = settings
        self.tarkov_client = TarkovClient(
            base_url=settings.tarkov_api_url,
            timeout_seconds=settings.http_timeout_seconds,
        )
        self.tarkov_service = TarkovService(self.tarkov_client)
        self.quest_log_service = QuestLogService(
            self.tarkov_client,
            RapidOCRProvider(),
        )

    async def setup_hook(self) -> None:
        await self.add_cog(
            TarkovCog(self, self.tarkov_service, self.quest_log_service)
        )
        if self.settings.discord_guild_id is not None:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            LOGGER.info("Synced %d command(s) to guild %d", len(synced), guild.id)
        else:
            synced = await self.tree.sync()
            LOGGER.info("Synced %d global command(s)", len(synced))

    async def close(self) -> None:
        await self.tarkov_client.close()
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.info("Connected as %s", self.user)


def create_bot(settings: Settings | None = None) -> DuckiesBot:
    return DuckiesBot(settings or load_settings())


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = create_bot()
    bot.run(bot.settings.discord_token, log_handler=None)
