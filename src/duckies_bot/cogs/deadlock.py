"""Discord commands for Deadlock information."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from ..features.deadlock import (
    DeadlockScreenshotReader,
    DeadlockService,
    ScoutTemplateCache,
    sample_scout_template,
)
from ..features.deadlock.formatter import (
    build_live_embed,
    build_live_match_embed,
    build_scout_overview_embeds,
)
from ..features.deadlock.models import LiveChatMessage, MatchScout
from ..providers.deadlock import DeadlockAPIError
from ..storage import SteamLinkRepository
from ..views import DeadlockScoutView


LOGGER = logging.getLogger(__name__)


async def _match_id_autocomplete(
    _interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    token = "top-200"
    if not current or token.startswith(current.strip().casefold()):
        return [
            app_commands.Choice(
                name="Random match from the top-200 Watch feed",
                value=token,
            )
        ]
    return []


@dataclass(slots=True)
class _ChatRelay:
    thread: discord.Thread
    requester_id: int
    task: asyncio.Task[None]


class DeadlockCog(commands.Cog):
    deadlock = app_commands.Group(name="deadlock", description="Look up Deadlock information")

    def __init__(
        self,
        bot: commands.Bot,
        service: DeadlockService,
        links: SteamLinkRepository,
        screenshot_reader: DeadlockScreenshotReader,
        scout_templates: ScoutTemplateCache,
    ) -> None:
        self.bot = bot
        self.service = service
        self.links = links
        self.screenshot_reader = screenshot_reader
        self.scout_templates = scout_templates
        self._chat_relays: dict[tuple[int, int], _ChatRelay] = {}

    def cog_unload(self) -> None:
        for relay in self._chat_relays.values():
            relay.task.cancel()
        self._chat_relays.clear()

    @deadlock.command(name="live", description="Look up a live Deadlock match or linked player")
    @app_commands.describe(
        match_id="Numeric match ID or top-200; omit to search your linked account",
        user="Discord user; defaults to you",
    )
    @app_commands.autocomplete(match_id=_match_id_autocomplete)
    async def live(
        self,
        interaction: discord.Interaction,
        match_id: str | None = None,
        user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        account = await self.links.get(target.id)

        if match_id is not None:
            await interaction.response.defer(thinking=True)
            try:
                resolved_match_id = await self._resolve_match_id(match_id)
                snapshot = await self.service.live_match(resolved_match_id)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            except DeadlockAPIError as exc:
                await interaction.followup.send(exc.user_message, ephemeral=True)
                return
            except Exception:
                LOGGER.exception("Unexpected error during manual Deadlock live lookup")
                await interaction.followup.send(
                    "Something unexpected went wrong during the live lookup.", ephemeral=True
                )
                return
            highlighted_id = account.account_id if account is not None else None
            await interaction.followup.send(
                embed=build_live_match_embed(snapshot, highlighted_id)
            )
            return

        if account is None:
            message = (
                "Link your Steam account first with `/steam link`."
                if target.id == interaction.user.id
                else f"{target.mention} has not linked a Steam account."
            )
            await interaction.response.send_message(message, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            lookup = await self.service.live_lookup(account.account_id)
        except DeadlockAPIError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unexpected error during Deadlock live lookup")
            await interaction.followup.send(
                "Something unexpected went wrong during the live lookup.", ephemeral=True
            )
            return

        if lookup is None:
            await interaction.followup.send(
                f"{target.mention} was not found in Deadlock's active Watch feed. "
                "The feed only contains the top 200 watch-listed matches, so this does not "
                "guarantee they are offline.",
            )
            return
        await interaction.followup.send(embed=build_live_embed(target.display_name, lookup))

    @deadlock.command(name="scout", description="Scout a new match by ID or screenshot")
    @app_commands.describe(
        match_id="Numeric match ID or top-200",
        screenshot="Full game screenshot with the match ID in the bottom-right",
    )
    @app_commands.autocomplete(match_id=_match_id_autocomplete)
    async def scout(
        self,
        interaction: discord.Interaction,
        match_id: str | None = None,
        screenshot: discord.Attachment | None = None,
    ) -> None:
        if (match_id is None) == (screenshot is None):
            await interaction.response.send_message(
                "Provide either a match ID or one screenshot, but not both.",
                ephemeral=True,
            )
            return
        account = await self.links.get(interaction.user.id)
        await interaction.response.defer(thinking=True)
        try:
            if screenshot is not None:
                image = await _read_deadlock_screenshot(screenshot)
                resolved_match_id = await self.screenshot_reader.extract_match_id(image)
            else:
                assert match_id is not None
                resolved_match_id = await self._resolve_match_id(match_id)
            report = await self.service.scout_match(resolved_match_id)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except DeadlockAPIError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unexpected error during Deadlock scouting lookup")
            await interaction.followup.send(
                "Something unexpected went wrong while building the scouting report.",
                ephemeral=True,
            )
            return
        try:
            await self.scout_templates.save(report)
        except Exception:
            LOGGER.warning("Could not refresh the local scouting template", exc_info=True)
        highlighted_id = account.account_id if account is not None else None
        await self._send_scout_report(interaction, report, highlighted_id)

    @deadlock.command(
        name="scout-preview",
        description="Preview the cached scouting layout without live API calls",
    )
    async def scout_preview(self, interaction: discord.Interaction) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only the bot owner can open the local scouting preview.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            report = await self.scout_templates.load()
        except Exception:
            LOGGER.warning("Could not load the local scouting template", exc_info=True)
            report = None
        if report is None:
            report = sample_scout_template()
        account = await self.links.get(interaction.user.id)
        highlighted_id = account.account_id if account is not None else None
        await self._send_scout_report(
            interaction,
            report,
            highlighted_id,
            ephemeral=True,
        )

    @deadlock.command(name="chat", description="Relay a live match's chat into a new thread")
    @app_commands.describe(match_id="Numeric match ID or top-200")
    @app_commands.autocomplete(match_id=_match_id_autocomplete)
    async def chat(self, interaction: discord.Interaction, match_id: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command in a server.",
                ephemeral=True,
            )
            return
        active_in_guild = sum(
            not relay.task.done()
            for relay_key, relay in self._chat_relays.items()
            if relay_key[0] == interaction.guild.id
        )
        if active_in_guild >= 3:
            await interaction.response.send_message(
                "This server already has three active Deadlock chat relays.",
                ephemeral=True,
            )
            return
        target_channel = _thread_parent(interaction.channel)
        if target_channel is None:
            await interaction.response.send_message(
                "Start the chat relay from a standard server text channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            resolved_match_id = await self._resolve_match_id(match_id)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except DeadlockAPIError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
            return
        key = (interaction.guild.id, resolved_match_id)
        existing = self._chat_relays.get(key)
        if existing is not None and not existing.task.done():
            await interaction.followup.send(
                f"That match is already being relayed in {existing.thread.mention}.",
                ephemeral=True,
            )
            return
        try:
            anchor = await target_channel.send(
                f"Deadlock live chat · Match `{resolved_match_id}`",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            thread = await anchor.create_thread(
                name=f"Deadlock chat {resolved_match_id}",
                auto_archive_duration=60,
                reason=f"Live chat relay requested by {interaction.user}",
            )
            await thread.send(
                "Connecting to the delayed Valve broadcast. All available chat messages will appear here when available.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception(
                "Could not create a Discord thread for match %d",
                resolved_match_id,
            )
            await interaction.followup.send(
                "I couldn't create the chat thread. Check my Send Messages and "
                "Create Public Threads permissions.",
                ephemeral=True,
            )
            return

        task = asyncio.create_task(
            self._relay_match_chat(key, resolved_match_id, thread),
            name=f"deadlock-chat-{interaction.guild.id}-{resolved_match_id}",
        )
        self._chat_relays[key] = _ChatRelay(thread, interaction.user.id, task)
        await interaction.followup.send(
            f"Started the live chat relay in {thread.mention}.",
            ephemeral=True,
        )

    async def _resolve_match_id(self, value: str) -> int:
        normalized = value.strip().casefold()
        if normalized == "top-200":
            return await self.service.random_top_200_match_id()
        try:
            match_id = int(normalized)
        except ValueError as exc:
            raise ValueError(
                "Enter a positive numeric match ID or `top-200`."
            ) from exc
        if match_id <= 0:
            raise ValueError("Enter a positive numeric match ID or `top-200`.")
        return match_id

    @deadlock.command(name="chat-stop", description="Stop a live Deadlock chat relay")
    @app_commands.describe(match_id="Match ID whose relay should stop")
    async def chat_stop(self, interaction: discord.Interaction, match_id: int) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command in the server where the relay is running.",
                ephemeral=True,
            )
            return
        relay = self._chat_relays.get((interaction.guild.id, match_id))
        if relay is None or relay.task.done():
            await interaction.response.send_message(
                "No active chat relay was found for that match.",
                ephemeral=True,
            )
            return
        can_manage = (
            interaction.user.id == relay.requester_id
            or isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_threads
        )
        if not can_manage:
            await interaction.response.send_message(
                "Only the relay starter or a moderator with Manage Threads can stop it.",
                ephemeral=True,
            )
            return
        relay.task.cancel()
        await interaction.response.send_message(
            f"Stopping the chat relay in {relay.thread.mention}.",
            ephemeral=True,
        )

    async def _relay_match_chat(
        self,
        key: tuple[int, int],
        match_id: int,
        thread: discord.Thread,
    ) -> None:
        try:
            async for chat in self.service.stream_chat_messages(match_id):
                await thread.send(
                    _format_chat_message(chat),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            await _safe_thread_send(thread, "The match chat stream has ended.")
        except asyncio.CancelledError:
            await _safe_thread_send(thread, "The live chat relay was stopped.")
            raise
        except DeadlockAPIError as exc:
            await _safe_thread_send(thread, f"Chat relay ended: {exc.user_message}")
        except discord.HTTPException:
            LOGGER.info("Chat relay thread %d is no longer writable", thread.id)
        except Exception:
            LOGGER.exception("Unexpected failure in chat relay for match %d", match_id)
            await _safe_thread_send(thread, "The chat relay stopped after an unexpected error.")
        finally:
            current = self._chat_relays.get(key)
            if current is not None and current.task is asyncio.current_task():
                self._chat_relays.pop(key, None)

    async def _send_scout_report(
        self,
        interaction: discord.Interaction,
        report: MatchScout,
        highlighted_id: int | None,
        *,
        ephemeral: bool = False,
    ) -> None:
        view = DeadlockScoutView(
            report,
            requester_id=interaction.user.id,
            highlighted_account_id=highlighted_id,
        )
        message = await interaction.followup.send(
            embeds=list(build_scout_overview_embeds(report, highlighted_id)),
            view=view,
            wait=True,
            ephemeral=ephemeral,
        )
        view.message = message


async def _read_deadlock_screenshot(attachment: discord.Attachment) -> bytes:
    from ..providers.deadlock.errors import InvalidDeadlockScreenshotError

    allowed_extensions = (".png", ".jpg", ".jpeg", ".webp")
    content_type = (attachment.content_type or "").casefold()
    if not content_type.startswith("image/") and not attachment.filename.casefold().endswith(
        allowed_extensions
    ):
        raise InvalidDeadlockScreenshotError()
    if attachment.size > 12 * 1024 * 1024:
        raise InvalidDeadlockScreenshotError("The screenshot must be 12 MB or smaller.")
    image = await attachment.read(use_cached=True)
    if not image:
        raise InvalidDeadlockScreenshotError("The uploaded screenshot was empty.")
    return image


def _thread_parent(
    channel: discord.abc.Messageable | None,
) -> discord.TextChannel | None:
    if isinstance(channel, discord.TextChannel):
        return channel
    if isinstance(channel, discord.Thread) and isinstance(channel.parent, discord.TextChannel):
        return channel.parent
    return None


def _format_chat_message(chat: LiveChatMessage) -> str:
    name = _safe_discord_text(chat.steam_name, limit=80)
    message = _safe_discord_text(chat.text, limit=1_700)
    if chat.game_time_seconds is None or chat.game_time_seconds < 0:
        timestamp = "--:--"
    else:
        minutes, seconds = divmod(int(chat.game_time_seconds), 60)
        timestamp = f"{minutes}:{seconds:02d}"
    if chat.all_chat is True:
        scope = "All"
    elif chat.all_chat is False:
        scope = "Team"
    else:
        scope = "Chat"
    return f"`[{timestamp}]` **{name}** · {scope}\n{message or '*empty message*'}"


def _safe_discord_text(value: str, *, limit: int) -> str:
    collapsed = " ".join(value.split())
    escaped = discord.utils.escape_mentions(collapsed)
    escaped = discord.utils.escape_markdown(escaped)
    return escaped[:limit]


async def _safe_thread_send(thread: discord.Thread, message: str) -> None:
    try:
        await thread.send(
            message,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        pass
