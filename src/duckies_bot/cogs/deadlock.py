"""Discord commands for Deadlock information."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from io import BytesIO

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
    build_live_match_embed,
    build_scout_overview_embeds,
)
from ..features.deadlock.models import LiveChatMessage, LiveMatchSnapshot, MatchScout
from ..features.deadlock.scoreboard import (
    render_discord_scoreboard_page,
    render_live_scoreboard,
)
from ..providers.deadlock import DeadlockAPIError
from ..storage import SteamLinkRepository
from ..views import DeadlockScoutView, DeadlockWatchView, WATCH_SCOREBOARD_FILENAME


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


@dataclass(slots=True)
class _LiveWatch:
    message: discord.Message
    requester_id: int
    task: asyncio.Task[None]
    view: DeadlockWatchView


_WATCH_REFRESH_SECONDS = 60.0
_WATCH_RECONNECT_DELAYS = (3.0, 8.0, 15.0, 30.0)
_WATCH_IDLE_SECONDS = 45.0
_WATCH_END_CONFIRMATIONS = 2


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
        self._live_watches: dict[tuple[int, int], _LiveWatch] = {}

    def cog_unload(self) -> None:
        for relay in self._chat_relays.values():
            relay.task.cancel()
        self._chat_relays.clear()
        for watch in self._live_watches.values():
            watch.task.cancel()
        self._live_watches.clear()

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
            resolved_match_id = await self._resolve_match_input(match_id, screenshot)
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
    @app_commands.describe(
        match_id="Numeric match ID or top-200",
        screenshot="Game screenshot with the match ID in the bottom-right",
    )
    @app_commands.autocomplete(match_id=_match_id_autocomplete)
    async def chat(
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
            resolved_match_id = await self._resolve_match_input(match_id, screenshot)
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

    @deadlock.command(name="watch", description="Watch your linked account or a supplied match")
    @app_commands.describe(
        match_id="Numeric match ID or top-200; omit to find your linked account",
        screenshot="Game screenshot with the match ID in the bottom-right; optional",
    )
    @app_commands.autocomplete(match_id=_match_id_autocomplete)
    async def watch(
        self,
        interaction: discord.Interaction,
        match_id: str | None = None,
        screenshot: discord.Attachment | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use this command in a server.", ephemeral=True)
            return
        if match_id is not None and screenshot is not None:
            await interaction.response.send_message(
                "Provide a match ID or a screenshot, but not both.",
                ephemeral=True,
            )
            return
        active_in_guild = sum(
            not watch.task.done()
            for watch_key, watch in self._live_watches.items()
            if watch_key[0] == interaction.guild.id
        )
        if active_in_guild >= 3:
            await interaction.response.send_message(
                "This server already has three active Deadlock match watches.",
                ephemeral=True,
            )
            return

        account = await self.links.get(interaction.user.id)
        if match_id is None and screenshot is None and account is None:
            await interaction.response.send_message(
                "Link your Steam account with `/steam link`, or provide a match ID or screenshot.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        stream: AsyncIterator[LiveMatchSnapshot] | None = None
        try:
            if match_id is None and screenshot is None:
                assert account is not None
                linked_match_id = await self.service.active_match_id(account.account_id)
                if linked_match_id is None:
                    await interaction.followup.send(
                        "Your linked Steam account was not found in Deadlock's active Watch feed. "
                        "The feed only contains the top 200 watch-listed matches, so this does not "
                        "guarantee you are offline.",
                        ephemeral=True,
                    )
                    return
                resolved_match_id = linked_match_id
            else:
                resolved_match_id = await self._resolve_match_input(match_id, screenshot)
            key = (interaction.guild.id, resolved_match_id)
            existing = self._live_watches.get(key)
            if existing is not None and not existing.task.done():
                await interaction.followup.send(
                    f"That match is already being watched: {existing.message.jump_url}",
                    ephemeral=True,
                )
                return
            stream = self.service.stream_live_match(resolved_match_id)
            async with asyncio.timeout(45):
                first_snapshot = await anext(stream)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except TimeoutError:
            if stream is not None:
                await stream.aclose()
            await interaction.followup.send(
                "The live broadcast did not produce player data before timing out.",
                ephemeral=True,
            )
            return
        except (DeadlockAPIError, StopAsyncIteration) as exc:
            if stream is not None:
                await stream.aclose()
            message = (
                exc.user_message
                if isinstance(exc, DeadlockAPIError)
                else "The live broadcast ended without returning player data."
            )
            await interaction.followup.send(message, ephemeral=True)
            return
        except Exception:
            if stream is not None:
                await stream.aclose()
            LOGGER.exception("Unexpected error while starting live match watch")
            await interaction.followup.send(
                "Something unexpected went wrong while starting the match watch.",
                ephemeral=True,
            )
            return

        highlighted_id = account.account_id if account is not None else None
        view = DeadlockWatchView(
            first_snapshot,
            requester_id=interaction.user.id,
            highlighted_account_id=highlighted_id,
            embed_scoreboard=False,
            scoreboard_layout="discord",
        )
        view.attachment_renderer = self._render_watch_scoreboard
        scoreboard_png = await self._render_watch_scoreboard(view)
        message = await interaction.followup.send(
            embed=view.render(),
            file=discord.File(
                BytesIO(scoreboard_png),
                filename=WATCH_SCOREBOARD_FILENAME,
            ),
            view=view,
            wait=True,
        )
        view.message = message
        assert stream is not None
        task = asyncio.create_task(
            self._update_live_watch(
                key,
                stream,
                message,
                view,
                first_snapshot,
            ),
            name=f"deadlock-watch-{interaction.guild.id}-{resolved_match_id}",
        )
        self._live_watches[key] = _LiveWatch(message, interaction.user.id, task, view)

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

    async def _resolve_match_input(
        self,
        match_id: str | None,
        screenshot: discord.Attachment | None,
    ) -> int:
        if (match_id is None) == (screenshot is None):
            raise ValueError("Provide either a match ID or one screenshot, but not both.")
        if screenshot is not None:
            image = await _read_deadlock_screenshot(screenshot)
            return await self.screenshot_reader.extract_match_id(image)
        assert match_id is not None
        return await self._resolve_match_id(match_id)

    async def _update_live_watch(
        self,
        key: tuple[int, int],
        snapshots: AsyncIterator[LiveMatchSnapshot],
        message: discord.Message,
        view: DeadlockWatchView | int | None,
        initial_snapshot: LiveMatchSnapshot,
    ) -> None:
        message_view = view if isinstance(view, DeadlockWatchView) else None
        if message_view is None:
            view = DeadlockWatchView(
                initial_snapshot,
                requester_id=0,
                highlighted_account_id=view if isinstance(view, int) else None,
            )
        latest = initial_snapshot
        last_edit = time.monotonic()
        reconnect_attempt = 0
        no_progress_connections = 0
        saw_connection_error = False
        try:
            while True:
                connection_advanced = False
                connection_error: DeadlockAPIError | None = None
                connection_stalled = False
                try:
                    while True:
                        try:
                            async with asyncio.timeout(_WATCH_IDLE_SECONDS):
                                snapshot = await anext(snapshots)
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            connection_stalled = True
                            LOGGER.info(
                                "Live match watch %d received no updates for %.0f seconds",
                                key[1],
                                _WATCH_IDLE_SECONDS,
                            )
                            break
                        if not _newer_live_snapshot(snapshot, latest):
                            continue
                        latest = snapshot
                        view.update_snapshot(snapshot)
                        connection_advanced = True
                        reconnect_attempt = 0
                        no_progress_connections = 0
                        saw_connection_error = False
                        if time.monotonic() - last_edit < _WATCH_REFRESH_SECONDS:
                            continue
                        await self._edit_watch_message(message, view, message_view)
                        last_edit = time.monotonic()
                except DeadlockAPIError as exc:
                    connection_error = exc
                    saw_connection_error = True
                    LOGGER.info(
                        "Live match watch %d connection failed: %s",
                        key[1],
                        exc.user_message,
                    )
                finally:
                    await snapshots.aclose()

                # A recovered connection can later drop again. Start a fresh retry
                # budget whenever it delivered genuinely newer match state.
                if connection_advanced:
                    reconnect_attempt = 0
                    no_progress_connections = 0
                elif connection_error is None:
                    no_progress_connections += 1
                reconnect_attempt += 1

                # A match ending may close the SSE response, but some parser
                # versions leave it open without producing another event. Confirm
                # that condition once with a fresh connection before declaring it.
                if no_progress_connections >= _WATCH_END_CONFIRMATIONS:
                    view.set_stream_status("ended")
                    await self._edit_watch_message(message, view, message_view)
                    LOGGER.info(
                        "Live match watch %d ended after %d connections produced no newer state",
                        key[1],
                        no_progress_connections,
                    )
                    return
                if reconnect_attempt > len(_WATCH_RECONNECT_DELAYS):
                    final_status = "unreachable" if saw_connection_error else "ended"
                    view.set_stream_status(final_status)
                    await self._edit_watch_message(message, view, message_view)
                    return

                view.set_stream_status("reconnecting")
                await self._edit_watch_message(message, view, message_view)
                delay = _WATCH_RECONNECT_DELAYS[reconnect_attempt - 1]
                LOGGER.info(
                    "Reconnecting live match watch %d in %.0f seconds (attempt %d/%d)%s",
                    key[1],
                    delay,
                    reconnect_attempt,
                    len(_WATCH_RECONNECT_DELAYS),
                    (
                        f": {connection_error.user_message}"
                        if connection_error
                        else " after an idle feed" if connection_stalled else ""
                    ),
                )
                await asyncio.sleep(delay)
                snapshots = self.service.stream_live_match(key[1])
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Unexpected failure in live match watch %d", key[1])
            view.set_stream_status("unreachable")
            await self._edit_watch_message(message, view, message_view)
        finally:
            await snapshots.aclose()
            current = self._live_watches.get(key)
            if current is not None and current.task is asyncio.current_task():
                self._live_watches.pop(key, None)

    async def _render_watch_scoreboard(self, view: DeadlockWatchView) -> bytes:
        icon_loader = getattr(self.service, "scoreboard_item_icons", None)
        hero_loader = getattr(self.service, "scoreboard_hero_icons", None)
        item_icons, hero_icons = await asyncio.gather(
            icon_loader(view.snapshot)
            if callable(icon_loader)
            else asyncio.sleep(0, result={}),
            hero_loader(view.snapshot)
            if callable(hero_loader)
            else asyncio.sleep(0, result={}),
        )
        renderer = (
            render_discord_scoreboard_page
            if view.scoreboard_layout == "discord"
            else render_live_scoreboard
        )
        if view.scoreboard_layout == "discord":
            return await asyncio.to_thread(
                renderer,
                view.snapshot,
                view.tab,
                view.highlighted_account_id,
                stream_status=view.stream_status,
                selected_account_id=view.selected_account_id,
                timeline=tuple(view.timeline),
                item_icons=item_icons,
                hero_icons=hero_icons,
            )
        return await asyncio.to_thread(
            renderer,
            view.snapshot,
            view.highlighted_account_id,
            stream_status=view.stream_status,
            item_icons=item_icons,
            hero_icons=hero_icons,
        )

    async def _edit_watch_message(
        self,
        message: discord.Message,
        view: DeadlockWatchView,
        message_view: discord.ui.View | None,
    ) -> None:
        scoreboard_png = await self._render_watch_scoreboard(view)
        await _safe_watch_edit(
            message,
            view.render(),
            message_view,
            scoreboard_png=scoreboard_png,
        )

    @deadlock.command(
        name="watch-stop",
        description="Stop a match watch, defaulting to the most recently started one",
    )
    @app_commands.describe(
        match_id="Match ID whose watch should stop",
        screenshot="Game screenshot with the match ID in the bottom-right",
    )
    async def watch_stop(
        self,
        interaction: discord.Interaction,
        match_id: str | None = None,
        screenshot: discord.Attachment | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command in the server where the watch is running.",
                ephemeral=True,
            )
            return
        if match_id is not None and screenshot is not None:
            await interaction.response.send_message(
                "Provide a match ID or one screenshot, but not both.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        if match_id is None and screenshot is None:
            recent = self._most_recent_live_watch(interaction.guild.id)
            if recent is None:
                await interaction.followup.send(
                    "There are no active match watches in this server.",
                    ephemeral=True,
                )
                return
            resolved_match_id, watch = recent
        else:
            try:
                resolved_match_id = await self._resolve_match_input(match_id, screenshot)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            except DeadlockAPIError as exc:
                await interaction.followup.send(exc.user_message, ephemeral=True)
                return
            watch = self._live_watches.get((interaction.guild.id, resolved_match_id))
        if watch is None or watch.task.done():
            await interaction.followup.send(
                "No active match watch was found for that match.",
                ephemeral=True,
            )
            return
        can_manage = (
            interaction.user.id == watch.requester_id
            or isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_messages
        )
        if not can_manage:
            await interaction.followup.send(
                "Only the watch starter or a moderator can stop it.",
                ephemeral=True,
            )
            return
        watch.task.cancel()
        await interaction.followup.send(
            f"Stopping the watch for match `{resolved_match_id}`.",
            ephemeral=True,
        )

    def _most_recent_live_watch(self, guild_id: int) -> tuple[int, _LiveWatch] | None:
        active = (
            (match_id, watch)
            for (watch_guild_id, match_id), watch in self._live_watches.items()
            if watch_guild_id == guild_id and not watch.task.done()
        )
        return max(active, key=lambda item: item[1].message.id, default=None)

    @deadlock.command(name="chat-stop", description="Stop a live Deadlock chat relay")
    @app_commands.describe(
        match_id="Match ID whose relay should stop",
        screenshot="Game screenshot with the match ID in the bottom-right",
    )
    async def chat_stop(
        self,
        interaction: discord.Interaction,
        match_id: str | None = None,
        screenshot: discord.Attachment | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this command in the server where the relay is running.",
                ephemeral=True,
            )
            return
        if (match_id is None) == (screenshot is None):
            await interaction.response.send_message(
                "Provide either a match ID or one screenshot, but not both.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            resolved_match_id = await self._resolve_match_input(match_id, screenshot)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except DeadlockAPIError as exc:
            await interaction.followup.send(exc.user_message, ephemeral=True)
            return
        relay = self._chat_relays.get((interaction.guild.id, resolved_match_id))
        if relay is None or relay.task.done():
            await interaction.followup.send(
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
            await interaction.followup.send(
                "Only the relay starter or a moderator with Manage Threads can stop it.",
                ephemeral=True,
            )
            return
        relay.task.cancel()
        await interaction.followup.send(
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


def _newer_live_snapshot(
    candidate: LiveMatchSnapshot,
    current: LiveMatchSnapshot,
) -> bool:
    candidate_time = candidate.game_time_seconds
    current_time = current.game_time_seconds
    if candidate_time is not None and current_time is not None:
        if candidate_time < current_time:
            return False
        if candidate_time > current_time:
            return True
    elif candidate_time is not None:
        return True
    return (
        candidate.players != current.players
        or bool(candidate.kill_events and candidate.kill_events != current.kill_events)
    )


async def _safe_watch_edit(
    message: discord.Message,
    embed: discord.Embed | None,
    view: discord.ui.View | None = None,
    *,
    scoreboard_png: bytes | None = None,
) -> None:
    try:
        kwargs: dict[str, object] = {"embed": embed}
        if view is not None:
            kwargs["view"] = view
        if scoreboard_png is not None:
            kwargs["attachments"] = [
                discord.File(
                    BytesIO(scoreboard_png),
                    filename=WATCH_SCOREBOARD_FILENAME,
                )
            ]
        await message.edit(**kwargs)
    except (discord.NotFound, discord.Forbidden):
        raise
    except discord.HTTPException:
        LOGGER.warning(
            "Could not edit live match watch message %d; a later update will retry",
            message.id,
            exc_info=True,
        )
