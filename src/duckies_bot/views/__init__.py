"""Interactive Discord views."""

from .deadlock_scout import DeadlockScoutView
from .deadlock_watch import DeadlockWatchView, WATCH_SCOREBOARD_FILENAME

__all__ = ["DeadlockScoutView", "DeadlockWatchView", "WATCH_SCOREBOARD_FILENAME"]
