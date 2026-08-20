"""Locate Deadlock's console log across Steam libraries."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys


DEADLOCK_APP_ID = "1422450"
CONSOLE_LOG_PARTS = ("game", "citadel", "console.log")
_VDF_PATH = re.compile(r'"path"\s+"([^"]+)"', re.IGNORECASE)
_INSTALL_DIR = re.compile(r'"installdir"\s+"([^"]+)"', re.IGNORECASE)


def find_deadlock_console_log(game_folder: str | Path | None = None) -> Path:
    """Find the expected console.log path, including custom Steam libraries.

    The returned path need not exist yet: Source 2 creates it after Deadlock is
    launched with ``-condebug``.
    """

    if game_folder is not None:
        return Path(game_folder).expanduser().joinpath(*CONSOLE_LOG_PARTS)

    roots = _steam_roots()
    for root in roots:
        for library in _steam_libraries(root):
            manifest = library / "steamapps" / f"appmanifest_{DEADLOCK_APP_ID}.acf"
            install_dir = _read_install_dir(manifest)
            if install_dir is None:
                continue
            game_root = library / "steamapps" / "common" / install_dir
            if game_root.is_dir():
                return game_root.joinpath(*CONSOLE_LOG_PARTS)

    fallback_root = roots[0] if roots else _default_steam_root()
    return fallback_root.joinpath(
        "steamapps", "common", "Deadlock", *CONSOLE_LOG_PARTS
    )


def find_steam_executable() -> Path | None:
    """Return the Steam launcher used to pass Deadlock its console flag."""

    if sys.platform == "win32":
        for root in _steam_roots():
            candidate = root / "steam.exe"
            if candidate.is_file():
                return candidate
        return None

    candidates: list[Path] = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory:
            candidates.append(Path(directory) / "steam")
    for root in _steam_roots():
        candidates.extend((root / "steam.sh", root / "steam"))
    return next((path for path in _unique(candidates) if path.is_file()), None)


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                value, _ = winreg.QueryValueEx(key, "SteamPath")
                roots.append(Path(value))
        except (ImportError, OSError):
            pass
        roots.extend((Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")))
    else:
        user_home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())
        roots.extend((user_home / ".steam" / "steam", user_home / ".local" / "share" / "Steam"))
    return _unique_existing_or_first(roots)


def _default_steam_root() -> Path:
    if sys.platform == "win32":
        return Path(r"C:\Program Files (x86)\Steam")
    return Path.home() / ".local" / "share" / "Steam"


def _steam_libraries(root: Path) -> list[Path]:
    libraries = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        content = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libraries
    libraries.extend(
        Path(match.group(1).replace(r"\\", "\\"))
        for match in _VDF_PATH.finditer(content)
    )
    return _unique(libraries)


def _read_install_dir(manifest: Path) -> str | None:
    try:
        content = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _INSTALL_DIR.search(content)
    return match.group(1) if match is not None else None


def _unique(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _unique_existing_or_first(paths: list[Path]) -> list[Path]:
    unique = _unique(paths)
    existing = [path for path in unique if path.is_dir()]
    return existing or unique[:1]
