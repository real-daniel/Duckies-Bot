"""League of Legends bot features."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import LeaguePlayerLookup
    from .service import LeagueService

__all__ = ["LeaguePlayerLookup", "LeagueService"]


def __getattr__(name: str):
    if name == "LeaguePlayerLookup":
        from .models import LeaguePlayerLookup

        return LeaguePlayerLookup
    if name == "LeagueService":
        from .service import LeagueService

        return LeagueService
    raise AttributeError(name)
