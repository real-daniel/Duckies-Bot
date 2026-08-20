"""Persistent local settings for the companion UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys

from .steam import find_deadlock_console_log


@dataclass(slots=True)
class CompanionSettings:
    steamapps_path: str = ""
    additional_launch_options: str = ""
    endpoint: str = ""
    token: str = ""


def settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Duckies Companion" / "settings.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "duckies-companion" / "settings.json"


def load_settings(path: str | Path | None = None) -> CompanionSettings:
    target = Path(path) if path is not None else settings_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CompanionSettings(steamapps_path=str(default_steamapps_path() or ""))
    if not isinstance(payload, dict):
        return CompanionSettings(steamapps_path=str(default_steamapps_path() or ""))
    return CompanionSettings(
        steamapps_path=_string(payload.get("steamapps_path")),
        additional_launch_options=_string(payload.get("additional_launch_options")),
        endpoint=_string(payload.get("endpoint")),
        token=_string(payload.get("token")),
    )


def save_settings(settings: CompanionSettings, path: str | Path | None = None) -> None:
    target = Path(path) if path is not None else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    if sys.platform != "win32":
        temporary.chmod(0o600)
    temporary.replace(target)


def console_log_from_steamapps(steamapps_path: str | Path) -> Path:
    steamapps = Path(steamapps_path).expanduser()
    return steamapps / "common" / "Deadlock" / "game" / "citadel" / "console.log"


def validate_steamapps_path(steamapps_path: str | Path) -> Path:
    steamapps = Path(steamapps_path).expanduser()
    deadlock = steamapps / "common" / "Deadlock"
    if not steamapps.is_dir() or not deadlock.is_dir():
        raise ValueError(
            "Select the Steam 'steamapps' folder that contains common/Deadlock."
        )
    return steamapps


def default_steamapps_path() -> Path | None:
    log = find_deadlock_console_log()
    parents = log.parents
    return parents[4] if len(parents) > 4 else None


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""
