"""Client for the self-hosted Deadlock live-events SSE parser."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import aiohttp

from ...features.deadlock.models import LiveChatMessage, LiveMatchSnapshot, LivePlayer
from .errors import DeadlockAPIError, InvalidDeadlockResponseError


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
        if match_id <= 0:
            raise ValueError("match_id must be positive")
        if not broadcast_url:
            raise ValueError("broadcast_url cannot be blank")

        session = await self._get_session()
        url = f"{self.base_url}/v1/live/demo/events"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        players: dict[int, LivePlayer] = {}
        game_time: float | None = None

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
                    if detail:
                        raise DeadlockAPIError(f"The live broadcast could not be read: {detail}")
                    raise DeadlockAPIError("The Deadlock live parser is temporarily unavailable.")
                if response.status >= 400:
                    detail = (await response.text()).strip()
                    if detail:
                        raise DeadlockAPIError(f"The live broadcast could not be read: {detail[:180]}")
                    raise DeadlockAPIError("The live broadcast could not be read yet.")

                async for event_name, data in _iter_sse(response.content):
                    if not event_name.startswith("player_controller_entity_"):
                        continue
                    try:
                        raw = json.loads(data)
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise InvalidDeadlockResponseError() from exc
                    if not isinstance(raw, Mapping):
                        continue
                    player = _parse_live_player(raw)
                    if player is None:
                        continue
                    players[player.account_id] = player
                    raw_game_time = raw.get("game_time")
                    if isinstance(raw_game_time, (int, float)) and not isinstance(raw_game_time, bool):
                        game_time = float(raw_game_time)

                    # Standard Deadlock matches have player slots 1-12. Waiting for
                    # all slots avoids returning a partially received initial snapshot.
                    slots = {item.player_slot for item in players.values()}
                    if set(range(1, 13)).issubset(slots):
                        return LiveMatchSnapshot(
                            match_id=match_id,
                            game_time_seconds=game_time,
                            players=tuple(sorted(players.values(), key=_player_sort_key)),
                        )
                    # Controller creates are emitted as one initial batch. Its first
                    # update marks a complete roster, including smaller game modes.
                    if event_name.endswith("_update") and players:
                        return LiveMatchSnapshot(
                            match_id=match_id,
                            game_time_seconds=game_time,
                            players=tuple(sorted(players.values(), key=_player_sort_key)),
                        )
        except TimeoutError as exc:
            if players:
                return LiveMatchSnapshot(
                    match_id=match_id,
                    game_time_seconds=game_time,
                    players=tuple(sorted(players.values(), key=_player_sort_key)),
                )
            raise DeadlockAPIError(
                "The live broadcast did not produce player data before timing out."
            ) from exc
        except aiohttp.ClientError as exc:
            raise DeadlockAPIError(
                "Could not reach the Deadlock live parser. Make sure its Docker service is running."
            ) from exc

        if not players:
            raise DeadlockAPIError("The live broadcast ended without returning player data.")
        return LiveMatchSnapshot(
            match_id=match_id,
            game_time_seconds=game_time,
            players=tuple(sorted(players.values(), key=_player_sort_key)),
        )

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


def _parse_live_player(raw: Mapping[str, Any]) -> LivePlayer | None:
    account_id = _optional_int(raw.get("steam_id"))
    steam_name = raw.get("steam_name")
    if account_id is None or account_id <= 0 or not isinstance(steam_name, str):
        # The parser also emits a steam_id=0 SourceTV controller.
        return None
    return LivePlayer(
        account_id=account_id,
        steam_name=steam_name or f"Account {account_id}",
        hero_id=_optional_int(raw.get("hero_id")),
        team=_optional_int(raw.get("team")),
        player_slot=_optional_int(raw.get("player_slot")),
        kills=_optional_int(raw.get("kills")) or 0,
        deaths=_optional_int(raw.get("deaths")) or 0,
        assists=_optional_int(raw.get("assists")) or 0,
        net_worth=_optional_int(raw.get("net_worth")) or 0,
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
