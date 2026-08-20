"""Extract live Deadlock match IDs from Source 2 console output."""

from __future__ import annotations

import re


# Keep these patterns tied to connection/lobby messages. Matching an arbitrary
# long number risks treating account, lobby, or server IDs as match IDs.
_MATCH_PATTERNS = (
    re.compile(r"\bmatch_id\s*=\s*(\d{6,12})\b", re.IGNORECASE),
    re.compile(r"\bconnecting\s+to\s+MatchID\s+(\d{6,12})\b", re.IGNORECASE),
    re.compile(r"\bLobby\s+\d+\s+for\s+Match\s+(\d{6,12})\s+created\b", re.IGNORECASE),
    re.compile(r"\bLobby\s+MatchID:\s*(\d{6,12})\b", re.IGNORECASE),
)


def extract_match_id(line: str) -> int | None:
    """Return a match ID from a known connection line, if one is present."""

    for pattern in _MATCH_PATTERNS:
        match = pattern.search(line)
        if match is not None:
            value = int(match.group(1))
            return value if value > 0 else None
    return None
