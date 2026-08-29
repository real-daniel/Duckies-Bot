"""Small Tkinter front end for launching and monitoring Deadlock."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .app import report_match_with_retries
from .launcher import SteamNotFoundError, launch_deadlock, split_launch_options
from .process import is_deadlock_running
from .settings import (
    CompanionSettings,
    console_log_from_steamapps,
    load_settings,
    save_settings,
    validate_steamapps_path,
)
from .tailer import MatchLogTailer


class CompanionWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Duckies Companion")
        self._icon_photo: tk.PhotoImage | None = None
        self._set_window_icon()
        self.root.geometry("620x390")
        self.root.minsize(540, 360)
        self._stop = threading.Event()
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._monitored_log_path: Path | None = None

        saved = load_settings()
        self.steamapps = tk.StringVar(value=saved.steamapps_path)
        self.additional_options = tk.StringVar(value=saved.additional_launch_options)
        self.endpoint = tk.StringVar(
            value=saved.endpoint or os.getenv("DUCKIES_COMPANION_ENDPOINT", "")
        )
        self.token = tk.StringVar(
            value=saved.token or os.getenv("DUCKIES_COMPANION_TOKEN", "")
        )
        self._report_endpoint = self.endpoint.get().strip().rstrip("/") or None
        self._report_token = self.token.get().strip() or None
        self.endpoint.trace_add("write", self._sync_delivery_settings)
        self.token.trace_add("write", self._sync_delivery_settings)
        self.status = tk.StringVar(value="Ready")

        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(0, self._start_monitoring)

    def _set_window_icon(self) -> None:
        icon_path = _asset_path("duckies_companion.png")
        try:
            self._icon_photo = tk.PhotoImage(file=icon_path)
            self.root.iconphoto(True, self._icon_photo)
        except tk.TclError:
            self._icon_photo = None

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=20)
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Deadlock", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        ttk.Label(frame, text="Steamapps folder").grid(row=1, column=0, sticky="w")
        path_row = ttk.Frame(frame)
        path_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 12))
        path_row.columnconfigure(0, weight=1)
        ttk.Entry(path_row, textvariable=self.steamapps).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(path_row, text="Browse…", command=self._browse).grid(
            row=0, column=1, padx=(8, 0)
        )

        ttk.Label(frame, text="Additional launch options").grid(
            row=3, column=0, sticky="w"
        )
        ttk.Entry(frame, textvariable=self.additional_options).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(4, 2)
        )
        ttk.Label(
            frame,
            text="-condebug is always included. Example: -novid +fps_max 144",
            foreground="#666666",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 14))

        ttk.Separator(frame).grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Label(frame, text="Companion endpoint").grid(row=7, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.endpoint).grid(
            row=8, column=0, columnspan=2, sticky="ew", pady=(4, 10)
        )
        ttk.Label(frame, text="Pairing token").grid(row=9, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.token, show="•").grid(
            row=10, column=0, columnspan=2, sticky="ew", pady=(4, 16)
        )

        actions = ttk.Frame(frame)
        actions.grid(row=11, column=0, columnspan=2, sticky="ew")
        actions.columnconfigure(1, weight=1)
        self.play_button = ttk.Button(
            actions,
            text="Play Deadlock",
            command=self._play,
        )
        self.play_button.grid(row=0, column=0, padx=(0, 14))
        ttk.Label(actions, textvariable=self.status).grid(row=0, column=1, sticky="w")

    def _browse(self) -> None:
        initial = self.steamapps.get().strip()
        selected = filedialog.askdirectory(
            title="Select the Steam steamapps folder",
            initialdir=initial if Path(initial).is_dir() else None,
        )
        if selected:
            self.steamapps.set(selected)
            self._start_monitoring(show_error=True)

    def _current_settings(self) -> CompanionSettings:
        return CompanionSettings(
            steamapps_path=self.steamapps.get().strip(),
            additional_launch_options=self.additional_options.get().strip(),
            endpoint=self.endpoint.get().strip().rstrip("/"),
            token=self.token.get().strip(),
        )

    def _play(self) -> None:
        settings = self._current_settings()
        try:
            steamapps = validate_steamapps_path(settings.steamapps_path)
            arguments = split_launch_options(settings.additional_launch_options)
            if bool(settings.endpoint) != bool(settings.token):
                raise ValueError("Enter both the companion endpoint and pairing token.")
            if settings.endpoint and not settings.endpoint.casefold().startswith("https://"):
                raise ValueError("The companion endpoint must use HTTPS.")
            save_settings(settings)
            self._start_monitoring(settings=settings, steamapps=steamapps)
            launch_deadlock(additional_args=arguments)
        except (OSError, SteamNotFoundError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not launch Deadlock", str(exc), parent=self.root)
            self.status.set("Launch failed")
            return

        self.status.set("Deadlock launch requested — waiting for deadlock.exe")

    def _start_monitoring(
        self,
        *,
        settings: CompanionSettings | None = None,
        steamapps: Path | None = None,
        show_error: bool = False,
    ) -> bool:
        settings = settings or self._current_settings()
        try:
            steamapps = steamapps or validate_steamapps_path(settings.steamapps_path)
        except ValueError as exc:
            self.status.set("Select your Steamapps folder to enable detection")
            if show_error:
                messagebox.showerror("Could not monitor Deadlock", str(exc), parent=self.root)
            return False

        log_path = console_log_from_steamapps(steamapps)
        already_monitoring = (
            self._monitor_thread is not None
            and self._monitor_thread.is_alive()
            and self._monitored_log_path == log_path
        )
        if already_monitoring:
            return True

        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            self._monitor_stop.set()

        tailer = MatchLogTailer(log_path)
        tailer.read_available()
        monitor_stop = threading.Event()
        self._monitor_stop = monitor_stop
        self._monitored_log_path = log_path
        self._monitor_thread = threading.Thread(
            target=self._monitor,
            args=(tailer, monitor_stop),
            name="duckies-companion-monitor",
            daemon=True,
        )
        self._monitor_thread.start()
        self.status.set("Waiting for Deadlock (deadlock.exe)")
        return True

    def _monitor(
        self,
        tailer: MatchLogTailer,
        monitor_stop: threading.Event,
    ) -> None:
        was_running: bool | None = None
        while not self._stop.is_set():
            running = is_deadlock_running()
            if running != was_running:
                status = (
                    "Deadlock detected — monitoring console.log"
                    if running
                    else "Deadlock is closed — waiting for deadlock.exe"
                )
                self._set_status(status)
                was_running = running

            if running:
                for match_id in tailer.read_available():
                    self._set_status(f"Match {match_id} detected — reporting")
                    endpoint, token = self._current_delivery_settings()
                    try:
                        report_match_with_retries(match_id, endpoint, token)
                    except (RuntimeError, ValueError) as exc:
                        self._set_status(f"Match {match_id} could not be reported: {exc}")
                    else:
                        destination = "sent to Discord" if endpoint else "detected locally"
                        self._set_status(f"Match {match_id} {destination}")

            if monitor_stop.wait(max(tailer.poll_seconds, 1.0)):
                return

    def _current_delivery_settings(self) -> tuple[str | None, str | None]:
        return self._report_endpoint, self._report_token

    def _sync_delivery_settings(self, *_args: object) -> None:
        self._report_endpoint = self.endpoint.get().strip().rstrip("/") or None
        self._report_token = self.token.get().strip() or None

    def _set_status(self, value: str) -> None:
        self.root.after(0, self.status.set, value)

    def _close(self) -> None:
        self._stop.set()
        self._monitor_stop.set()
        try:
            save_settings(self._current_settings())
        except OSError:
            pass
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    if "--smoke-test" in sys.argv:
        root.withdraw()
    CompanionWindow(root)
    if "--smoke-test" in sys.argv:
        root.update_idletasks()
        root.destroy()
        return 0
    root.mainloop()
    return 0


def _asset_path(filename: str) -> str:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return str(Path(bundle_root) / "assets" / filename)
    return str(Path(__file__).resolve().parents[2] / "assets" / filename)


if __name__ == "__main__":
    raise SystemExit(main())
