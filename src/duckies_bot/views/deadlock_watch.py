"""Host-controlled navigation for an auto-updating Deadlock match watch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from io import BytesIO

import discord

from ..features.deadlock.formatter import build_live_match_embed, build_watch_tab_embed
from ..features.deadlock.models import LiveMatchSnapshot, LivePlayer


WATCH_SCOREBOARD_FILENAME = "deadlock-scoreboard.png"


class _WatchPlayerSelect(discord.ui.Select["DeadlockWatchView"]):
    def __init__(self, parent: "DeadlockWatchView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Open a player card…",
            min_values=1,
            max_values=1,
            options=parent._player_options(),
            custom_id="deadlock_watch:player",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.selected_account_id = int(self.values[0])
        self.parent_view.tab = "player"
        await self.parent_view.render_interaction(interaction)


class DeadlockWatchView(discord.ui.View):
    def __init__(
        self,
        snapshot: LiveMatchSnapshot,
        requester_id: int,
        highlighted_account_id: int | None = None,
        *,
        embed_scoreboard: bool = True,
        scoreboard_layout: str = "standard",
    ) -> None:
        super().__init__(timeout=None)
        self.snapshot = snapshot
        self.requester_id = requester_id
        self.highlighted_account_id = highlighted_account_id
        self.embed_scoreboard = embed_scoreboard
        self.scoreboard_layout = scoreboard_layout
        self.attachment_renderer: Callable[["DeadlockWatchView"], Awaitable[bytes]] | None = None
        self.tab = "overview"
        self.stream_status = "live"
        self.selected_account_id = (
            highlighted_account_id
            if any(player.account_id == highlighted_account_id for player in snapshot.players)
            else snapshot.players[0].account_id if snapshot.players else None
        )
        self.timeline: list[str] = [
            f"`{_game_time(snapshot)}` Watch started with {len(snapshot.players)} players."
        ]
        self.message: discord.Message | discord.PartialMessage | None = None
        self.player_select = _WatchPlayerSelect(self)
        self.add_item(self.player_select)
        self._sync_controls()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the person who started this match watch can control its views.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Overview",
        style=discord.ButtonStyle.primary,
        custom_id="deadlock_watch:overview",
        row=0,
    )
    async def overview(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["DeadlockWatchView"],
    ) -> None:
        del button
        await self._switch_tab(interaction, "overview")

    @discord.ui.button(
        label="Combat",
        style=discord.ButtonStyle.secondary,
        custom_id="deadlock_watch:combat",
        row=0,
    )
    async def combat(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["DeadlockWatchView"],
    ) -> None:
        del button
        await self._switch_tab(interaction, "combat")

    @discord.ui.button(
        label="Economy",
        style=discord.ButtonStyle.secondary,
        custom_id="deadlock_watch:economy",
        row=0,
    )
    async def economy(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["DeadlockWatchView"],
    ) -> None:
        del button
        await self._switch_tab(interaction, "economy")

    @discord.ui.button(
        label="Builds",
        style=discord.ButtonStyle.secondary,
        custom_id="deadlock_watch:builds",
        row=0,
    )
    async def builds(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["DeadlockWatchView"],
    ) -> None:
        del button
        await self._switch_tab(interaction, "builds")

    @discord.ui.button(
        label="Golden Statues",
        style=discord.ButtonStyle.secondary,
        custom_id="deadlock_watch:statues",
        row=1,
        emoji="🏆",
    )
    async def statues(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["DeadlockWatchView"],
    ) -> None:
        del button
        await self._switch_tab(interaction, "statues")

    @discord.ui.button(
        label="Timeline",
        style=discord.ButtonStyle.secondary,
        custom_id="deadlock_watch:timeline",
        row=0,
    )
    async def timeline_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["DeadlockWatchView"],
    ) -> None:
        del button
        await self._switch_tab(interaction, "timeline")

    def render(self) -> discord.Embed | None:
        if not self.embed_scoreboard:
            return None
        if self.tab == "overview":
            embed = build_live_match_embed(
                self.snapshot,
                self.highlighted_account_id,
                stream_status=self.stream_status,
            )
            embed.clear_fields()
        else:
            embed = build_watch_tab_embed(
                self.snapshot,
                self.tab,
                self.highlighted_account_id,
                stream_status=self.stream_status,
                selected_account_id=self.selected_account_id,
                timeline=tuple(self.timeline),
            )
        embed.set_image(url=f"attachment://{WATCH_SCOREBOARD_FILENAME}")
        return embed

    def update_snapshot(self, snapshot: LiveMatchSnapshot) -> None:
        snapshot = _retain_statue_buffs(self.snapshot, snapshot)
        self._record_changes(self.snapshot, snapshot)
        self.snapshot = snapshot
        self.stream_status = "live"
        self.player_select.options = self._player_options()
        self._sync_controls()

    def set_stream_status(self, status: str) -> None:
        self.stream_status = status

    async def render_interaction(self, interaction: discord.Interaction) -> None:
        self._sync_controls()
        if self.attachment_renderer is None:
            await interaction.response.edit_message(embed=self.render(), view=self)
            return
        await interaction.response.defer()
        scoreboard_png = await self.attachment_renderer(self)
        await interaction.edit_original_response(
            embed=self.render(),
            attachments=[
                discord.File(
                    BytesIO(scoreboard_png),
                    filename=WATCH_SCOREBOARD_FILENAME,
                )
            ],
            view=self,
        )

    async def _switch_tab(self, interaction: discord.Interaction, tab: str) -> None:
        self.tab = tab
        await self.render_interaction(interaction)

    def _sync_controls(self) -> None:
        for child in self.children:
            if not isinstance(child, discord.ui.Button) or child.custom_id is None:
                continue
            child_tab = child.custom_id.rsplit(":", 1)[-1]
            child.style = (
                discord.ButtonStyle.primary
                if child_tab == self.tab
                else discord.ButtonStyle.secondary
            )
        for option in self.player_select.options if hasattr(self, "player_select") else ():
            option.default = (
                self.tab == "player" and int(option.value) == self.selected_account_id
            )

    def _player_options(self) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for player in self.snapshot.players[:25]:
            hero = self.snapshot.hero(player.hero_id)
            hero_name = hero.name if hero else (
                f"Hero {player.hero_id}" if player.hero_id is not None else "Unknown hero"
            )
            options.append(
                discord.SelectOption(
                    label=player.steam_name[:100] or f"Account {player.account_id}",
                    value=str(player.account_id),
                    description=f"{hero_name} · {_team_label(player.team)}"[:100],
                    default=(
                        self.tab == "player" and player.account_id == self.selected_account_id
                    ),
                )
            )
        if not options:
            options.append(discord.SelectOption(label="No players reported", value="0"))
        return options

    def _record_changes(
        self,
        previous: LiveMatchSnapshot,
        current: LiveMatchSnapshot,
    ) -> None:
        old_players = {player.account_id: player for player in previous.players}
        current_players = {player.account_id: player for player in current.players}
        stamp = f"`{_game_time(current)}`"
        for kill in current.kill_events:
            event_stamp = (
                f"`{_seconds_time(kill.game_time_seconds)}`"
                if kill.game_time_seconds is not None
                else stamp
            )
            attacker = current_players.get(kill.attacker_account_id)
            victim = current_players.get(kill.victim_account_id)
            attacker_name = f"**{_name(attacker)}**" if attacker is not None else None
            victim_name = f"**{_name(victim)}**" if victim is not None else "**Unknown player**"
            if attacker_name is None:
                message = f"{event_stamp} {victim_name} died."
            elif kill.attacker_account_id == kill.victim_account_id:
                message = f"{event_stamp} {victim_name} died to themselves."
            else:
                message = f"{event_stamp} {attacker_name} killed {victim_name}."
            assister_names = [
                f"**{_name(player)}**"
                for account_id in kill.assister_account_ids
                if (player := current_players.get(account_id)) is not None
                and account_id != kill.attacker_account_id
            ]
            if assister_names:
                message = f"{message[:-1]} (assisted by {', '.join(assister_names)})."
            self.timeline.append(message)
        for player in current.players:
            old = old_players.get(player.account_id)
            if old is None:
                self.timeline.append(f"{stamp} **{_name(player)}** joined the reported roster.")
                continue
            if player.upgrades != old.upgrades:
                self.timeline.extend(
                    _inventory_change_events(stamp, old, player, previous, current)
                )
        del self.timeline[:-50]


def _inventory_change_events(
    stamp: str,
    previous_player: LivePlayer,
    current_player: LivePlayer,
    previous: LiveMatchSnapshot,
    current: LiveMatchSnapshot,
) -> list[str]:
    """Describe the exact inventory IDs added and removed by a player update."""
    old_ids = set(previous_player.upgrades)
    new_ids = set(current_player.upgrades)
    added = [item_id for item_id in current_player.upgrades if item_id not in old_ids]
    removed = [item_id for item_id in previous_player.upgrades if item_id not in new_ids]
    item_names = {
        item.item_id: item.name
        for item in (*previous.items, *current.items)
    }

    def names(item_ids: list[int]) -> str:
        return ", ".join(
            f"**{item_names.get(item_id, f'Item {item_id}')}**" for item_id in item_ids
        )

    player_name = f"**{_name(current_player)}**"
    if len(added) == 1 and len(removed) == 1:
        return [
            f"{stamp} {player_name} replaced {names(removed)} with {names(added)}."
        ]

    events: list[str] = []
    if added:
        events.append(f"{stamp} {player_name} added {names(added)}.")
    if removed:
        events.append(f"{stamp} {player_name} removed {names(removed)}.")
    return events


def _retain_statue_buffs(
    previous: LiveMatchSnapshot,
    current: LiveMatchSnapshot,
) -> LiveMatchSnapshot:
    """Keep permanent buffs when a reconnect/end snapshot no longer contains them."""
    previous_players = {player.account_id: player for player in previous.players}
    players = tuple(
        replace(player, statue_buffs=old.statue_buffs)
        if (old := previous_players.get(player.account_id)) is not None
        and len(player.statue_buffs) < len(old.statue_buffs)
        else player
        for player in current.players
    )
    return replace(current, players=players) if players != current.players else current


def _seconds_time(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}:{remainder:02d}"


def _game_time(snapshot: LiveMatchSnapshot) -> str:
    if snapshot.game_time_seconds is None or snapshot.game_time_seconds < 0:
        return "?:??"
    minutes, seconds = divmod(int(snapshot.game_time_seconds), 60)
    return f"{minutes}:{seconds:02d}"


def _name(player: LivePlayer) -> str:
    return discord.utils.escape_markdown(player.steam_name)[:32]


def _team_label(team: int | None) -> str:
    if team == 2:
        return "Amber"
    if team == 3:
        return "Sapphire"
    return f"Team {team}" if team is not None else "Unknown team"
