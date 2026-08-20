"""Client for the self-hosted Deadlock live-events SSE parser."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import aiohttp

from ...features.deadlock.models import (
    LiveChatMessage,
    LiveKillEvent,
    LiveMatchSnapshot,
    LivePlayer,
)
from .errors import (
    DeadlockAPIError,
    InvalidDeadlockResponseError,
    LiveDemoUnavailableError,
)


class DeadlockLiveClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:3000",
        timeout_seconds: float = 45.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._session = session
        self._owns_session = session is None

    async def get_match_players(
        self,
        match_id: int,
        broadcast_url: str,
    ) -> LiveMatchSnapshot:
        snapshots = self.stream_match_snapshots(match_id, broadcast_url)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await anext(snapshots)
        except TimeoutError as exc:
            raise DeadlockAPIError(
                "The live broadcast did not produce player data before timing out."
            ) from exc
        except StopAsyncIteration as exc:
            raise DeadlockAPIError(
                "The live broadcast ended without returning player data."
            ) from exc
        finally:
            await snapshots.aclose()

    async def stream_match_snapshots(
        self,
        match_id: int,
        broadcast_url: str,
    ) -> AsyncIterator[LiveMatchSnapshot]:
        if match_id <= 0:
            raise ValueError("match_id must be positive")
        if not broadcast_url:
            raise ValueError("broadcast_url cannot be blank")

        session = await self._get_session()
        url = f"{self.base_url}/v1/live/demo/events"
        timeout = aiohttp.ClientTimeout(
            total=None,
            sock_connect=self.timeout_seconds,
            sock_read=None,
        )
        players: dict[int, LivePlayer] = {}
        pawn_accounts: dict[int, int] = {}
        controller_accounts: dict[int, int] = {}
        game_time: float | None = None
        roster_ready = False
        last_snapshot: LiveMatchSnapshot | None = None

        try:
            async with session.get(
                url,
                params={
                    "broadcast_url": broadcast_url,
                    "subscribed_entities": "player_controller",
                },
                headers={"Accept": "text/event-stream"},
                timeout=timeout,
            ) as response:
                if response.status >= 500:
                    detail = _clean_error_detail(await response.text())
                    if "demo not available" in detail.casefold():
                        raise LiveDemoUnavailableError()
                    if detail:
                        raise DeadlockAPIError(f"The live broadcast could not be read: {detail}")
                    raise DeadlockAPIError("The Deadlock live parser is temporarily unavailable.")
                if response.status >= 400:
                    detail = (await response.text()).strip()
                    if detail:
                        raise DeadlockAPIError(f"The live broadcast could not be read: {detail[:180]}")
                    raise DeadlockAPIError("The live broadcast could not be read yet.")

                async for event_name, data in _iter_sse(response.content):
                    if event_name == "end":
                        return
                    if event_name == "hero_killed":
                        try:
                            raw = json.loads(data)
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise InvalidDeadlockResponseError() from exc
                        if not isinstance(raw, Mapping) or not roster_ready:
                            continue
                        kill_event = _parse_live_kill_event(
                            raw,
                            pawn_accounts,
                            controller_accounts,
                        )
                        if kill_event is None:
                            continue
                        snapshot = LiveMatchSnapshot(
                            match_id=match_id,
                            game_time_seconds=_event_game_time(raw, game_time),
                            players=tuple(sorted(players.values(), key=_player_sort_key)),
                            kill_events=(kill_event,),
                        )
                        if snapshot != last_snapshot:
                            last_snapshot = snapshot
                            yield snapshot
                        continue
                    if event_name == "tick_end":
                        if players and not roster_ready:
                            roster_ready = True
                            snapshot = LiveMatchSnapshot(
                                match_id=match_id,
                                game_time_seconds=game_time,
                                players=tuple(sorted(players.values(), key=_player_sort_key)),
                            )
                            if snapshot != last_snapshot:
                                last_snapshot = snapshot
                                yield snapshot
                        continue
                    if not event_name.startswith("player_controller_entity_"):
                        continue
                    try:
                        raw = json.loads(data)
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise InvalidDeadlockResponseError() from exc
                    if not isinstance(raw, Mapping):
                        continue
                    account_id = _optional_int(raw.get("steam_id"))
                    player = _parse_live_player(
                        raw,
                        players.get(account_id) if account_id is not None else None,
                    )
                    if player is None:
                        continue
                    players[player.account_id] = player
                    pawn_index = _optional_int(raw.get("pawn"))
                    if pawn_index is not None and pawn_index >= 0:
                        pawn_accounts[pawn_index] = player.account_id
                    controller_index = _optional_int(raw.get("entity_index"))
                    if controller_index is not None and controller_index >= 0:
                        controller_accounts[controller_index] = player.account_id
                    raw_game_time = raw.get("game_time")
                    if isinstance(raw_game_time, (int, float)) and not isinstance(raw_game_time, bool):
                        game_time = float(raw_game_time)

                    slots = {item.player_slot for item in players.values()}
                    roster_ready = roster_ready or set(range(1, 13)).issubset(slots)
                    if not roster_ready:
                        continue
                    snapshot = LiveMatchSnapshot(
                        match_id=match_id,
                        game_time_seconds=game_time,
                        players=tuple(sorted(players.values(), key=_player_sort_key)),
                    )
                    if snapshot != last_snapshot:
                        last_snapshot = snapshot
                        yield snapshot
        except aiohttp.ClientError as exc:
            raise DeadlockAPIError(
                "Could not reach the Deadlock live parser. Make sure its Docker service is running."
            ) from exc

    async def stream_chat_messages(
        self,
        match_id: int,
    ) -> AsyncIterator[LiveChatMessage]:
        if match_id <= 0:
            raise ValueError("match_id must be positive")
        session = await self._get_session()
        # The parser's direct-broadcast route currently cannot deserialize its
        # flattened boolean chat flag. The match route uses the same Valve stream
        # parser without that upstream query bug.
        url = f"{self.base_url}/v1/matches/{match_id}/live/demo/events"
        timeout = aiohttp.ClientTimeout(
            total=None,
            sock_connect=self.timeout_seconds,
            sock_read=None,
        )
        try:
            async with session.get(
                url,
                params={
                    "subscribed_entities": "player_controller",
                    "subscribed_chat_messages": "true",
                },
                headers={"Accept": "text/event-stream"},
                timeout=timeout,
            ) as response:
                if response.status >= 400:
                    detail = _clean_error_detail(await response.text())
                    if detail:
                        raise DeadlockAPIError(
                            f"The live chat stream could not be opened: {detail}"
                        )
                    raise DeadlockAPIError("The live chat stream could not be opened.")
                async for event_name, data in _iter_sse(response.content):
                    if event_name == "end":
                        return
                    if event_name != "chat_message":
                        continue
                    try:
                        raw = json.loads(data)
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise InvalidDeadlockResponseError() from exc
                    if not isinstance(raw, Mapping):
                        continue
                    chat = _parse_chat_message(raw)
                    if chat is not None:
                        yield chat
        except TimeoutError as exc:
            raise DeadlockAPIError("The Deadlock live chat connection timed out.") from exc
        except aiohttp.ClientError as exc:
            raise DeadlockAPIError(
                "Could not reach the Deadlock live parser for chat messages."
            ) from exc

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()


def _parse_live_kill_event(
    raw: Mapping[str, Any],
    pawn_accounts: Mapping[int, int],
    controller_accounts: Mapping[int, int],
) -> LiveKillEvent | None:
    victim_entity = _optional_int(raw.get("entindex_victim"))
    if victim_entity is None or victim_entity < 0:
        return None

    def account(entity_index: int | None) -> int | None:
        if entity_index is None or entity_index < 0:
            return None
        return pawn_accounts.get(entity_index) or controller_accounts.get(entity_index)

    scorer = account(_optional_int(raw.get("entindex_scorer")))
    attacker = account(_optional_int(raw.get("entindex_attacker")))
    assister_entities = raw.get("entindex_assisters")
    assisters = (
        tuple(
            account_id
            for entity_index in assister_entities
            if (account_id := account(_optional_int(entity_index))) is not None
        )
        if isinstance(assister_entities, list)
        else ()
    )
    return LiveKillEvent(
        tick=_optional_int(raw.get("tick")),
        game_time_seconds=_event_game_time(raw, None),
        attacker_account_id=scorer or attacker,
        victim_account_id=account(victim_entity),
        assister_account_ids=assisters,
    )


def _event_game_time(
    raw: Mapping[str, Any],
    fallback: float | None,
) -> float | None:
    value = raw.get("game_time")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return fallback


async def _iter_sse(content: Any) -> AsyncIterator[tuple[str, str]]:
    event_name = "message"
    data_lines: list[str] = []
    async for raw_line in content:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = "message"
            data_lines.clear()
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event_name, "\n".join(data_lines)


def _parse_live_player(
    raw: Mapping[str, Any],
    previous: LivePlayer | None = None,
) -> LivePlayer | None:
    account_id = _optional_int(raw.get("steam_id"))
    steam_name = raw.get("steam_name")
    if account_id is None or account_id <= 0:
        # The parser also emits a steam_id=0 SourceTV controller.
        return None
    if not isinstance(steam_name, str):
        steam_name = previous.steam_name if previous is not None else None
    if steam_name is None:
        return None

    def current_int(key: str, fallback: int | None = None) -> int | None:
        value = _optional_int(raw.get(key))
        return value if value is not None else fallback

    def current_number(key: str, fallback: float | None = None) -> float | None:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return fallback

    def current_bool(key: str, fallback: bool | None = None) -> bool | None:
        value = raw.get(key)
        return value if isinstance(value, bool) else fallback

    if "upgrades" in raw:
        # The live feed sends the complete current inventory, not a delta. An
        # explicitly empty list therefore clears the inventory, while an
        # omitted field means this partial entity update did not change it.
        upgrades = _int_tuple(raw.get("upgrades"))
    else:
        upgrades = previous.upgrades if previous is not None else ()

    return LivePlayer(
        account_id=account_id,
        steam_name=steam_name or f"Account {account_id}",
        hero_id=current_int("hero_id", previous.hero_id if previous else None),
        team=current_int("team", previous.team if previous else None),
        player_slot=current_int("player_slot", previous.player_slot if previous else None),
        kills=current_int("kills", previous.kills if previous else 0) or 0,
        deaths=current_int("deaths", previous.deaths if previous else 0) or 0,
        assists=current_int("assists", previous.assists if previous else 0) or 0,
        net_worth=current_int("net_worth", previous.net_worth if previous else 0) or 0,
        assigned_lane=current_int(
            "assigned_lane", previous.assigned_lane if previous else None
        ),
        denies=current_int("denies", previous.denies if previous else 0) or 0,
        last_hits=current_int("last_hits", previous.last_hits if previous else 0) or 0,
        hero_healing=current_int(
            "hero_healing", previous.hero_healing if previous else 0
        ) or 0,
        self_healing=current_int(
            "self_healing", previous.self_healing if previous else 0
        ) or 0,
        hero_damage=current_int(
            "hero_damage", previous.hero_damage if previous else 0
        ) or 0,
        objective_damage=current_int(
            "objective_damage", previous.objective_damage if previous else 0
        ) or 0,
        health_regen=current_number(
            "health_regen", previous.health_regen if previous else None
        ),
        ultimate_trained=current_bool(
            "ultimate_trained", previous.ultimate_trained if previous else None
        ),
        ultimate_cooldown_end=current_number(
            "ultimate_cooldown_end",
            previous.ultimate_cooldown_end if previous else None,
        ),
        upgrades=upgrades,
    )


def _parse_chat_message(raw: Mapping[str, Any]) -> LiveChatMessage | None:
    steam_name = raw.get("steam_name")
    message = raw.get("text")
    if not isinstance(steam_name, str) or not isinstance(message, str):
        return None
    game_time = raw.get("game_time")
    return LiveChatMessage(
        account_id=_optional_int(raw.get("steam_id")),
        steam_name=steam_name or "Unknown player",
        text=message,
        game_time_seconds=(
            float(game_time)
            if isinstance(game_time, (int, float)) and not isinstance(game_time, bool)
            else None
        ),
        all_chat=raw.get("all_chat") if isinstance(raw.get("all_chat"), bool) else None,
    )


def _player_sort_key(player: LivePlayer) -> tuple[int, int, str]:
    return (player.team if player.team is not None else 99, player.player_slot or 99, player.steam_name)


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _int_tuple(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, int) and not isinstance(item, bool))


def _clean_error_detail(text: str) -> str:
    detail = text.strip()
    if not detail:
        return ""
    try:
        document = json.loads(detail)
    except json.JSONDecodeError:
        return detail[:180]
    if isinstance(document, str):
        return document[:180]
    if isinstance(document, Mapping):
        for key in ("detail", "message", "error"):
            value = document.get(key)
            if isinstance(value, str):
                return value[:180]
    return ""
