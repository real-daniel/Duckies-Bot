"""Detect whether the local Deadlock game process is running."""

from __future__ import annotations

import csv
import io
import subprocess
import sys


_WINDOWS_PROCESS_NAMES = frozenset({"project8.exe", "deadlock.exe"})
_POSIX_PROCESS_NAMES = frozenset({"project8", "project8.exe", "deadlock", "deadlock.exe"})


def is_deadlock_running() -> bool:
    """Return whether a Deadlock client process is currently visible."""

    try:
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["tasklist", "/NH", "/FO", "CSV"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creationflags,
            )
            if result.returncode != 0:
                return False
            names = (
                row[0].casefold()
                for row in csv.reader(io.StringIO(result.stdout))
                if row
            )
            return any(name in _WINDOWS_PROCESS_NAMES for name in names)

        result = subprocess.run(
            ["ps", "-A", "-o", "comm="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        return any(
            line.strip().rsplit("/", 1)[-1].casefold() in _POSIX_PROCESS_NAMES
            for line in result.stdout.splitlines()
        )
    except (OSError, subprocess.SubprocessError):
        return False
