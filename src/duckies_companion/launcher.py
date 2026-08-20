"""Launch Deadlock through Steam with console-file logging enabled."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import shlex
import subprocess

from .steam import DEADLOCK_APP_ID, find_steam_executable


class SteamNotFoundError(RuntimeError):
    """Raised when the companion cannot locate a Steam executable."""


def launch_deadlock(
    steam_executable: str | Path | None = None,
    additional_args: Sequence[str] = (),
) -> None:
    """Ask Steam to launch Deadlock with ``-condebug``.

    ``Popen`` receives an argument list and never invokes a command shell.
    Steam forwards the final argument to the game whether Steam is newly
    started or already running.
    """

    steam = Path(steam_executable) if steam_executable is not None else find_steam_executable()
    if steam is None or not steam.is_file():
        raise SteamNotFoundError(
            "Steam could not be found. Start Deadlock manually with -condebug, "
            "or pass --steam-path."
        )
    try:
        subprocess.Popen(
            [
                str(steam),
                "-applaunch",
                DEADLOCK_APP_ID,
                "-condebug",
                *additional_args,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        raise RuntimeError(f"Steam could not launch Deadlock: {exc}") from exc


def split_launch_options(value: str) -> tuple[str, ...]:
    """Split a launch-options field without ever invoking a command shell."""

    if not value.strip():
        return ()
    try:
        tokens = shlex.split(value, posix=False)
        return tuple(_remove_matching_quotes(token) for token in tokens)
    except ValueError as exc:
        raise ValueError(f"Invalid additional launch options: {exc}") from exc


def _remove_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
