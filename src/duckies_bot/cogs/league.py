"""Discord commands for League of Legends player information."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..features.ask_conversations import AskConversationStore
from ..features.item_questions import QuestionClarificationError
from ..features.league import LeagueService
from ..features.league.formatter import build_analysis_embed, build_player_embed
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
        self._ask_conversations = AskConversationStore()

    def cog_unload(self) -> None:
        self._ask_conversations.clear()

    @league.command(name="ask", description="Ask about meta, matchups, synergies, champions, or items")
    @app_commands.describe(
        question="Example: Top mids Emerald, who counters Smolder, or best second items on Smolder?",
        region="League platform to sample",
    )
    @app_commands.choices(
        region=[
            app_commands.Choice(name=f"{_REGION_NAMES[value]} ({value})", value=value)
            for value in PLATFORM_REGIONS
        ]
    )
    async def ask(
        self,
        interaction: discord.Interaction,
        question: str,
        region: str = "NA1",
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            analysis = await self.service.answer_question(question, region)
        except QuestionClarificationError as exc:
            response = await interaction.followup.send(str(exc), wait=True)
            self._ask_conversations.register(
                response.id,
                user_id=interaction.user.id,
                channel_id=interaction.channel_id,
                turns=(question,),
                metadata=region,
            )
            return
        except (ValueError, RiotAPIError) as exc:
            message = exc.user_message if isinstance(exc, RiotAPIError) else str(exc)
            await interaction.followup.send(message, ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unexpected error during League analytics")
            await interaction.followup.send(
                "Something unexpected went wrong while analyzing that League question.",
                ephemeral=True,
            )
            return
        response = await interaction.followup.send(
            embed=build_analysis_embed(analysis),
            wait=True,
        )
        self._ask_conversations.register(
            response.id,
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            turns=(question,),
            metadata=region,
        )

    @commands.Cog.listener("on_message")
    async def continue_ask_conversation(self, message: discord.Message) -> None:
        if message.author.bot or not message.content.strip() or message.reference is None:
            return
        referenced_id = message.reference.message_id
        if referenced_id is None:
            return
        session = self._ask_conversations.claim(
            referenced_id,
            user_id=message.author.id,
            channel_id=message.channel.id,
        )
        if session is None:
            return
        follow_up = message.content.strip()
        query = session.follow_up_query(follow_up)
        region = session.metadata or "NA1"
        async with message.channel.typing():
            try:
                analysis = await self.service.answer_question(query, region)
            except QuestionClarificationError as exc:
                response = await message.reply(str(exc), mention_author=False)
            except (ValueError, RiotAPIError) as exc:
                error = exc.user_message if isinstance(exc, RiotAPIError) else str(exc)
                await message.reply(error, mention_author=False)
                return
            except Exception:
                LOGGER.exception("Unexpected error continuing League analytics")
                await message.reply(
                    "Something unexpected went wrong while continuing that League question.",
                    mention_author=False,
                )
                return
            else:
                response = await message.reply(
                    embed=build_analysis_embed(analysis),
                    mention_author=False,
                )
        self._ask_conversations.continue_from(response.id, session, follow_up)

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

