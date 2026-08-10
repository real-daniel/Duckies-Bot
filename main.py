"""Run Duckies Bot from a source checkout."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).parent / "src"))

from duckies_bot.bot import run  # noqa: E402


if __name__ == "__main__":
    run()
