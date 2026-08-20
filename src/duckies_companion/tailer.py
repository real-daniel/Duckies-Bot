"""Rotation-safe polling tailer for Deadlock's console log."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import time

from .detector import extract_match_id


class MatchLogTailer:
    """Follow newly appended log lines and yield each match once per run."""

    def __init__(self, path: str | Path, *, poll_seconds: float = 0.5) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be greater than zero")
        self.path = Path(path)
        self.poll_seconds = poll_seconds
        self._offset: int | None = None
        self._partial = b""
        self._seen: set[int] = set()

    def read_available(self) -> tuple[int, ...]:
        """Read currently available new bytes without blocking.

        The first call starts at EOF so a stale match from a previous game is
        never submitted when the companion launches.
        """

        try:
            size = self.path.stat().st_size
        except OSError:
            # If the game creates the log after the companion starts, consume
            # that new file from byte zero. Existing files still start at EOF.
            self._offset = 0
            self._partial = b""
            return ()

        if self._offset is None:
            self._offset = size
            return ()
        if size < self._offset:
            self._offset = 0
            self._partial = b""
        if size == self._offset:
            return ()

        try:
            with self.path.open("rb") as log:
                log.seek(self._offset)
                chunk = log.read()
        except OSError:
            return ()
        self._offset += len(chunk)

        data = self._partial + chunk
        lines = data.split(b"\n")
        self._partial = lines.pop()
        found: list[int] = []
        for raw_line in lines:
            match_id = extract_match_id(raw_line.decode("utf-8", errors="replace").rstrip("\r"))
            if match_id is not None and match_id not in self._seen:
                self._seen.add(match_id)
                found.append(match_id)
        return tuple(found)

    def follow(self) -> Iterator[int]:
        while True:
            yield from self.read_available()
            time.sleep(self.poll_seconds)
