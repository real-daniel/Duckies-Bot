"""Detect whether the local Deadlock game process is running."""

from __future__ import annotations

import csv
import io
import subprocess
import sys


DEADLOCK_PROCESS_NAME = "deadlock.exe"


def is_deadlock_running() -> bool:
    """Return whether Deadlock's game process is currently visible."""

    try:
        if sys.platform == "win32":
            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    f"IMAGENAME eq {DEADLOCK_PROCESS_NAME}",
                    "/NH",
                    "/FO",
                    "CSV",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                return False
            return any(
                row and row[0].casefold() == DEADLOCK_PROCESS_NAME
                for row in csv.reader(io.StringIO(result.stdout))
            )

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
            line.strip().rsplit("/", 1)[-1].casefold()
            in {"deadlock", DEADLOCK_PROCESS_NAME}
            for line in result.stdout.splitlines()
        )
    except (OSError, subprocess.SubprocessError):
        return False
