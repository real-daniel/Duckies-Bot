"""Small in-memory reply sessions for conversational ``/ask`` commands."""

from __future__ import annotations

import time
from dataclasses import dataclass


ASK_SESSION_TTL_SECONDS = 10 * 60
ASK_SESSION_MAX_TURNS = 4
ASK_SESSION_TURN_CHARS = 140


@dataclass(frozen=True, slots=True)
class AskConversation:
    user_id: int
    channel_id: int
    turns: tuple[str, ...]
    metadata: str | None
    expires_at: float

    def follow_up_query(self, message: str) -> str:
        turns = (*self.turns, _compact_turn(message))[-ASK_SESSION_MAX_TURNS:]
        transcript = "\n".join(f"User turn {index}: {turn}" for index, turn in enumerate(turns, 1))
        return (
            "Continue this /ask conversation. Interpret the latest turn using earlier "
            f"turns only as context.\n{transcript}"
        )


class AskConversationStore:
    """Bind a bot response message to one user's short-lived conversation."""

    def __init__(self, *, ttl_seconds: float = ASK_SESSION_TTL_SECONDS) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[int, AskConversation] = {}

    def register(
        self,
        message_id: int,
        *,
        user_id: int,
        channel_id: int,
        turns: tuple[str, ...],
        metadata: str | None = None,
    ) -> None:
        if message_id <= 0 or user_id <= 0 or channel_id <= 0:
            raise ValueError("Discord identifiers must be positive")
        compact = tuple(_compact_turn(turn) for turn in turns if turn.strip())
        if not compact:
            raise ValueError("at least one conversation turn is required")
        self._prune()
        self._sessions[message_id] = AskConversation(
            user_id=user_id,
            channel_id=channel_id,
            turns=compact[-ASK_SESSION_MAX_TURNS:],
            metadata=metadata,
            expires_at=time.monotonic() + self.ttl_seconds,
        )

    def claim(
        self,
        message_id: int,
        *,
        user_id: int,
        channel_id: int,
    ) -> AskConversation | None:
        self._prune()
        session = self._sessions.get(message_id)
        if session is None:
            return None
        if session.user_id != user_id or session.channel_id != channel_id:
            return None
        return self._sessions.pop(message_id)

    def continue_from(
        self,
        message_id: int,
        session: AskConversation,
        follow_up: str,
    ) -> bool:
        turns = (*session.turns, _compact_turn(follow_up))
        if len(turns) >= ASK_SESSION_MAX_TURNS:
            return False
        self.register(
            message_id,
            user_id=session.user_id,
            channel_id=session.channel_id,
            turns=turns,
            metadata=session.metadata,
        )
        return True

    def clear(self) -> None:
        self._sessions.clear()

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            message_id
            for message_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for message_id in expired:
            self._sessions.pop(message_id, None)


def _compact_turn(value: str) -> str:
    return " ".join(value.split())[:ASK_SESSION_TURN_CHARS]
