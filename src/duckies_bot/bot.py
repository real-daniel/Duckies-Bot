"""Discord bot composition and lifecycle management."""

import logging

import discord
from discord.ext import commands

from .cogs.deadlock import DeadlockCog
from .cogs.steam import SteamCog
from .cogs.tarkov import TarkovCog
from .companion_api import CompanionAPIServer
from .config import Settings, load_settings
from .features.deadlock import DeadlockScreenshotReader, DeadlockService, ScoutTemplateCache
from .features.tarkov.service import TarkovService
from .features.tarkov.quest_log import QuestLogService
from .features.tarkov.profit import ProfitService
from .providers.ocr import RapidOCRProvider
from .providers.deadlock import DeadlockClient, DeadlockLiveClient
from .providers.tarkov.client import TarkovClient
from .storage import (
    BroadcastURLRepository,
    CompanionPairingRepository,
    SteamLinkRepository,
)


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
        self.deadlock_client = DeadlockClient(
            base_url=settings.deadlock_api_url,
            timeout_seconds=settings.http_timeout_seconds,
            api_key=settings.deadlock_api_key,
        )
        self.deadlock_live_client = DeadlockLiveClient(
            base_url=settings.deadlock_live_events_url,
            timeout_seconds=max(settings.http_timeout_seconds, 45.0),
        )
        self.deadlock_broadcast_urls = BroadcastURLRepository(settings.database_path)
        self.deadlock_service = DeadlockService(
            self.deadlock_client,
            self.deadlock_live_client,
            self.deadlock_broadcast_urls,
        )
        self.ocr_provider = RapidOCRProvider()
        self.deadlock_screenshot_reader = DeadlockScreenshotReader(self.ocr_provider)
        self.deadlock_scout_templates = ScoutTemplateCache(
            settings.deadlock_scout_template_path
        )
        self.steam_links = SteamLinkRepository(settings.database_path)
        self.companion_pairings = CompanionPairingRepository(settings.database_path)
        self.companion_api: CompanionAPIServer | None = None
        self.quest_log_service = QuestLogService(
            self.tarkov_client,
            self.ocr_provider,
        )
        self.profit_service = ProfitService(self.tarkov_client)

    async def setup_hook(self) -> None:
        await self.steam_links.initialize()
        await self.deadlock_broadcast_urls.initialize()
        await self.companion_pairings.initialize()
        await self.add_cog(SteamCog(self, self.steam_links))
        deadlock_cog = DeadlockCog(
            self,
            self.deadlock_service,
            self.steam_links,
            self.companion_pairings,
            self.deadlock_screenshot_reader,
            self.deadlock_scout_templates,
        )
        await self.add_cog(deadlock_cog)
        self.companion_api = CompanionAPIServer(
            self.companion_pairings,
            deadlock_cog.start_companion_watch,
            host=self.settings.companion_api_host,
            port=self.settings.companion_api_port,
        )
        await self.companion_api.start()
        await self.add_cog(
            TarkovCog(
                self,
                self.tarkov_service,
                self.quest_log_service,
                self.profit_service,
            )
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
        if self.companion_api is not None:
            await self.companion_api.close()
        await self.tarkov_client.close()
        await self.deadlock_client.close()
        await self.deadlock_live_client.close()
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
