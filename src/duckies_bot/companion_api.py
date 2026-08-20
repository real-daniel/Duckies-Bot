"""Private HTTP receiver for authenticated companion match events."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
import logging
import time
from typing import cast

from aiohttp import web

from .storage import CompanionPairing, CompanionPairingRepository


LOGGER = logging.getLogger(__name__)
MatchCallback = Callable[[CompanionPairing, int], Awaitable[None]]
_MAX_EVENT_AGE = timedelta(minutes=10)
_MAX_FUTURE_SKEW = timedelta(minutes=2)
_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMIT = 5
_REPOSITORY_KEY = web.AppKey("companion_repository", CompanionPairingRepository)
_CALLBACK_KEY = web.AppKey("companion_callback", object)
_RATE_LIMITS_KEY = web.AppKey("companion_rate_limits", dict)


class CompanionAPIServer:
    def __init__(
        self,
        repository: CompanionPairingRepository,
        on_match: MatchCallback,
        *,
        host: str = "127.0.0.1",
        port: int = 8080,
    ) -> None:
        self.repository = repository
        self.on_match = on_match
        self.host = host
        self.port = port
        self._runner: web.AppRunner | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        if self._runner is not None:
            return
        application = create_companion_api(self.repository, self._accept_match)
        runner = web.AppRunner(application, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.host, self.port).start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner
        LOGGER.info("Companion API listening on http://%s:%d", self.host, self.port)

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _accept_match(self, pairing: CompanionPairing, match_id: int) -> None:
        task = asyncio.create_task(
            self.on_match(pairing, match_id),
            name=f"companion-match-{pairing.guild_id}-{match_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._match_task_done)

    def _match_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "Companion match task failed",
                exc_info=(type(error), error, error.__traceback__),
            )


def create_companion_api(
    repository: CompanionPairingRepository,
    on_match: MatchCallback,
) -> web.Application:
    application = web.Application(client_max_size=2048)
    application[_REPOSITORY_KEY] = repository
    application[_CALLBACK_KEY] = on_match
    application[_RATE_LIMITS_KEY] = defaultdict(deque)
    application.router.add_get("/healthz", _health)
    application.router.add_post("/v1/companion/matches", _submit_match)
    return application


async def _health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _submit_match(request: web.Request) -> web.Response:
    token = _bearer_token(request.headers.get("Authorization", ""))
    if token is None:
        return _error("unauthorized", 401)
    repository = request.app[_REPOSITORY_KEY]
    pairing = await repository.authenticate(token)
    if pairing is None:
        return _error("unauthorized", 401)
    limits = request.app[_RATE_LIMITS_KEY]
    if _rate_limited(limits[pairing.token_hash]):
        return _error("rate_limited", 429)

    try:
        payload = await request.json()
    except (ValueError, web.HTTPException):
        return _error("invalid_json", 400)
    try:
        match_id = _validate_payload(payload)
    except ValueError as exc:
        return _error(str(exc), 400)

    if not await repository.record_match(pairing, match_id):
        return web.json_response(
            {"accepted": True, "duplicate": True, "match_id": match_id}
        )
    callback = cast(MatchCallback, request.app[_CALLBACK_KEY])
    await callback(pairing, match_id)
    return web.json_response(
        {"accepted": True, "duplicate": False, "match_id": match_id},
        status=202,
    )


def _bearer_token(header: str) -> str | None:
    scheme, separator, token = header.partition(" ")
    if separator and scheme.casefold() == "bearer" and token and " " not in token:
        return token
    return None


def _validate_payload(payload: object) -> int:
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    match_id = payload.get("match_id")
    if isinstance(match_id, bool) or not isinstance(match_id, int):
        raise ValueError("invalid_match_id")
    if match_id <= 0 or not 6 <= len(str(match_id)) <= 12:
        raise ValueError("invalid_match_id")
    if payload.get("source") != "deadlock-console":
        raise ValueError("invalid_source")
    detected_at = payload.get("detected_at")
    if not isinstance(detected_at, str):
        raise ValueError("invalid_detected_at")
    try:
        detected = datetime.fromisoformat(detected_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid_detected_at") from exc
    if detected.tzinfo is None:
        raise ValueError("invalid_detected_at")
    now = datetime.now(UTC)
    detected = detected.astimezone(UTC)
    if now - detected > _MAX_EVENT_AGE or detected - now > _MAX_FUTURE_SKEW:
        raise ValueError("stale_event")
    return match_id


def _rate_limited(requests: deque[float]) -> bool:
    now = time.monotonic()
    while requests and now - requests[0] >= _RATE_WINDOW_SECONDS:
        requests.popleft()
    if len(requests) >= _RATE_LIMIT:
        return True
    requests.append(now)
    return False


def _error(code: str, status: int) -> web.Response:
    return web.json_response({"error": code}, status=status)
