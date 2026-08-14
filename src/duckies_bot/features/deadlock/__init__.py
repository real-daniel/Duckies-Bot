"""Deadlock bot features."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .scoreboard import render_live_scoreboard
    from .screenshot import DeadlockScreenshotReader
    from .service import DeadlockService
    from .template import ScoutTemplateCache

__all__ = [
    "DeadlockScreenshotReader",
    "render_live_scoreboard",
    "DeadlockService",
    "ScoutTemplateCache",
    "sample_scout_template",
]


def __getattr__(name: str) -> Any:
    if name == "DeadlockService":
        from .service import DeadlockService

        return DeadlockService
    if name == "DeadlockScreenshotReader":
        from .screenshot import DeadlockScreenshotReader

        return DeadlockScreenshotReader
    if name == "render_live_scoreboard":
        from .scoreboard import render_live_scoreboard

        return render_live_scoreboard
    if name in {"ScoutTemplateCache", "sample_scout_template"}:
        from .template import ScoutTemplateCache, sample_scout_template

        return {
            "ScoutTemplateCache": ScoutTemplateCache,
            "sample_scout_template": sample_scout_template,
        }[name]
    raise AttributeError(name)
