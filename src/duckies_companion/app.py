"""Command-line entry point for the Duckies Deadlock companion."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

from .launcher import SteamNotFoundError, launch_deadlock
from .steam import find_deadlock_console_log
from .tailer import MatchLogTailer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect new Deadlock match IDs from the local console log."
    )
    parser.add_argument("--log-path", type=Path, help="Override the console.log path")
    parser.add_argument("--game-folder", type=Path, help="Override the Deadlock install folder")
    parser.add_argument("--endpoint", help="HTTPS endpoint that receives detected IDs")
    parser.add_argument(
        "--launch",
        action="store_true",
        help="Launch Deadlock through Steam with -condebug, then watch it",
    )
    parser.add_argument("--steam-path", type=Path, help="Override the Steam executable path")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--once", action="store_true", help="Exit after reporting one match")
    return parser


def report_match(match_id: int, endpoint: str | None, token: str | None) -> None:
    payload = {
        "match_id": match_id,
        "detected_at": datetime.now(UTC).isoformat(),
        "source": "deadlock-console",
    }
    if endpoint is None:
        print(json.dumps(payload), flush=True)
        return
    if not endpoint.casefold().startswith("https://"):
        raise ValueError("The companion endpoint must use HTTPS.")
    if not token:
        raise ValueError("DUCKIES_COMPANION_TOKEN is required when an endpoint is configured.")

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Duckies-Companion/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Companion endpoint returned HTTP {response.status}.")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Companion endpoint returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach the companion endpoint: {exc.reason}") from exc


def report_match_with_retries(
    match_id: int,
    endpoint: str | None,
    token: str | None,
    *,
    attempts: int = 5,
) -> None:
    """Retry temporary delivery failures without duplicating server watches."""

    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            report_match(match_id, endpoint, token)
            return
        except ValueError:
            raise
        except RuntimeError:
            if attempt == attempts:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 10.0)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.log_path is not None and args.game_folder is not None:
        print("Use --log-path or --game-folder, not both.", file=sys.stderr)
        return 2
    path = args.log_path or find_deadlock_console_log(args.game_folder)
    endpoint = args.endpoint or os.getenv("DUCKIES_COMPANION_ENDPOINT")
    token = os.getenv("DUCKIES_COMPANION_TOKEN")
    print(f"Watching {path}", file=sys.stderr, flush=True)

    tailer = MatchLogTailer(path, poll_seconds=args.poll_seconds)
    # Establish the current end-of-file before launching. This closes the small
    # race where a very early Source 2 line could otherwise be treated as old.
    tailer.read_available()
    if args.launch:
        try:
            launch_deadlock(args.steam_path)
        except (RuntimeError, SteamNotFoundError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("Deadlock launch requested with -condebug.", file=sys.stderr, flush=True)
    try:
        for match_id in tailer.follow():
            try:
                report_match_with_retries(match_id, endpoint, token)
            except (RuntimeError, ValueError) as exc:
                print(f"Could not report match {match_id}: {exc}", file=sys.stderr, flush=True)
                continue
            if args.once:
                return 0
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
