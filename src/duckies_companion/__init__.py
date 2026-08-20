"""Local Deadlock match detector used by the Duckies companion app."""

from .detector import extract_match_id
from .launcher import launch_deadlock
from .steam import find_deadlock_console_log
from .tailer import MatchLogTailer

__all__ = [
    "MatchLogTailer",
    "extract_match_id",
    "find_deadlock_console_log",
    "launch_deadlock",
]
