"""Tests for local Deadlock match detection."""

import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, sentinel

from duckies_companion.app import report_match, report_match_with_retries
from duckies_companion.detector import extract_match_id
from duckies_companion.launcher import (
    SteamNotFoundError,
    launch_deadlock,
    split_launch_options,
)
from duckies_companion.process import is_deadlock_running
from duckies_companion.settings import (
    CompanionSettings,
    console_log_from_steamapps,
    load_settings,
    save_settings,
    validate_steamapps_path,
)
from duckies_companion.steam import DEADLOCK_APP_ID, find_deadlock_console_log
from duckies_companion.tailer import MatchLogTailer
from duckies_companion.ui import CompanionWindow


class MatchDetectorTests(unittest.TestCase):
    def test_extracts_known_connection_formats(self) -> None:
        lines = (
            "[SteamNetSockets] ticket server=x match_id=100141930 mm=1",
            "LOBBY STATE RUN: lobby 123: connecting to MatchID 100141930 at ServerID x",
            "Lobby 123 for Match 100141930 created",
            "[Client] Lobby MatchID: 100141930",
        )
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(extract_match_id(line), 100141930)

    def test_ignores_unrelated_ids_and_destroyed_lobbies(self) -> None:
        self.assertIsNone(extract_match_id("[U:1:123456789] connected to server 987654321"))
        self.assertIsNone(extract_match_id("Lobby 123 for Match 100141930 destroyed"))


class MatchLogTailerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "console.log"
        self.log_path.write_text("match_id=99999999\n", encoding="utf-8")
        self.tailer = MatchLogTailer(self.log_path, poll_seconds=0.01)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_starts_at_eof_then_finds_new_match_once(self) -> None:
        self.assertEqual(self.tailer.read_available(), ())
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write("ticket match_id=100141930 mm=1\n")
        self.assertEqual(self.tailer.read_available(), (100141930,))
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write("connecting to MatchID 100141930 at ServerID x\n")
        self.assertEqual(self.tailer.read_available(), ())

    def test_waits_for_complete_line_and_handles_truncation(self) -> None:
        self.assertEqual(self.tailer.read_available(), ())
        with self.log_path.open("ab") as log:
            log.write(b"ticket match_id=100141")
        self.assertEqual(self.tailer.read_available(), ())
        with self.log_path.open("ab") as log:
            log.write(b"930 mm=1\n")
        self.assertEqual(self.tailer.read_available(), (100141930,))

        self.log_path.write_text("ticket match_id=100141931 mm=1\n", encoding="utf-8")
        self.assertEqual(self.tailer.read_available(), (100141931,))

    def test_reads_new_log_created_after_companion_starts(self) -> None:
        missing_path = Path(self.temp_dir.name) / "new" / "console.log"
        tailer = MatchLogTailer(missing_path, poll_seconds=0.01)
        self.assertEqual(tailer.read_available(), ())
        missing_path.parent.mkdir()
        missing_path.write_text("ticket match_id=100141932 mm=1\n", encoding="utf-8")
        self.assertEqual(tailer.read_available(), (100141932,))


class SteamDiscoveryTests(unittest.TestCase):
    def test_explicit_game_folder_builds_log_path(self) -> None:
        path = find_deadlock_console_log(Path("D:/Games/Deadlock"))
        self.assertEqual(path, Path("D:/Games/Deadlock/game/citadel/console.log"))


class DeadlockLauncherTests(unittest.TestCase):
    def test_launches_through_steam_with_condebug(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            steam = Path(temp_dir) / "steam.exe"
            steam.touch()
            with patch("duckies_companion.launcher.subprocess.Popen") as popen:
                launch_deadlock(steam)

        arguments = popen.call_args.args[0]
        self.assertEqual(
            arguments,
            [str(steam), "-applaunch", DEADLOCK_APP_ID, "-condebug"],
        )
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_rejects_missing_steam_override(self) -> None:
        with self.assertRaises(SteamNotFoundError):
            launch_deadlock(Path("Z:/missing/steam.exe"))

    def test_appends_parsed_additional_arguments_without_a_shell(self) -> None:
        options = split_launch_options(r'-novid +exec "my config.cfg" -path C:\Games')
        self.assertEqual(
            options,
            ("-novid", "+exec", "my config.cfg", "-path", r"C:\Games"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            steam = Path(temp_dir) / "steam.exe"
            steam.touch()
            with patch("duckies_companion.launcher.subprocess.Popen") as popen:
                launch_deadlock(steam, options)
        self.assertEqual(popen.call_args.args[0][-len(options) :], list(options))
        self.assertNotIn("shell", popen.call_args.kwargs)


class DeadlockProcessTests(unittest.TestCase):
    def test_detects_windows_project8_process(self) -> None:
        result = SimpleNamespace(
            returncode=0,
            stdout='"steam.exe","1","Console","1","1,000 K"\n'
            '"project8.exe","2","Console","1","2,000 K"\n',
        )
        with (
            patch("duckies_companion.process.sys.platform", "win32"),
            patch("duckies_companion.process.subprocess.run", return_value=result),
        ):
            self.assertTrue(is_deadlock_running())

    def test_returns_false_when_deadlock_is_absent(self) -> None:
        result = SimpleNamespace(returncode=0, stdout="steam\ndiscord\n")
        with (
            patch("duckies_companion.process.sys.platform", "linux"),
            patch("duckies_companion.process.subprocess.run", return_value=result),
        ):
            self.assertFalse(is_deadlock_running())


class CompanionDeliveryTests(unittest.TestCase):
    def test_uses_system_trust_store_for_https_delivery(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = SimpleNamespace(status=202)

        with (
            patch(
                "duckies_companion.app._system_https_context",
                return_value=sentinel.ssl_context,
            ),
            patch(
                "duckies_companion.app.urllib.request.urlopen",
                return_value=response,
            ) as urlopen,
        ):
            report_match(
                100141930,
                "https://example.test/v1/companion/matches",
                "token",
            )

        self.assertIs(urlopen.call_args.kwargs["context"], sentinel.ssl_context)

    def test_retries_temporary_delivery_errors(self) -> None:
        with (
            patch(
                "duckies_companion.app.report_match",
                side_effect=(RuntimeError("offline"), None),
            ) as report,
            patch("duckies_companion.app.time.sleep") as sleep,
        ):
            report_match_with_retries(100141930, "https://example.test", "token")
        self.assertEqual(report.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_does_not_retry_invalid_configuration(self) -> None:
        with (
            patch(
                "duckies_companion.app.report_match",
                side_effect=ValueError("HTTPS required"),
            ) as report,
            patch("duckies_companion.app.time.sleep") as sleep,
            self.assertRaises(ValueError),
        ):
            report_match_with_retries(100141930, "http://example.test", "token")
        report.assert_called_once()
        sleep.assert_not_called()


class CompanionSettingsTests(unittest.TestCase):
    def test_settings_round_trip(self) -> None:
        settings = CompanionSettings(
            steamapps_path="D:/SteamLibrary/steamapps",
            additional_launch_options="-novid",
            endpoint="https://example.test/v1/companion/matches",
            token="secret",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)

    def test_validates_steamapps_and_builds_console_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            steamapps = Path(temp_dir) / "steamapps"
            (steamapps / "common" / "Deadlock").mkdir(parents=True)
            self.assertEqual(validate_steamapps_path(steamapps), steamapps)
            self.assertEqual(
                console_log_from_steamapps(steamapps),
                steamapps / "common" / "Deadlock" / "game" / "citadel" / "console.log",
            )


class CompanionWindowTests(unittest.TestCase):
    def test_starts_monitoring_without_launching_deadlock(self) -> None:
        window = CompanionWindow.__new__(CompanionWindow)
        window.root = MagicMock()
        window.status = MagicMock()
        window._stop = threading.Event()
        window._monitor_stop = threading.Event()
        window._monitor_thread = None
        window._monitored_log_path = None

        with tempfile.TemporaryDirectory() as temp_dir:
            steamapps = Path(temp_dir) / "steamapps"
            (steamapps / "common" / "Deadlock").mkdir(parents=True)
            settings = CompanionSettings(steamapps_path=str(steamapps))
            with (
                patch("duckies_companion.ui.MatchLogTailer") as tailer_type,
                patch("duckies_companion.ui.threading.Thread") as thread_type,
            ):
                started = window._start_monitoring(settings=settings)

        self.assertTrue(started)
        tailer_type.return_value.read_available.assert_called_once_with()
        thread_type.return_value.start.assert_called_once_with()
        self.assertEqual(
            window._monitored_log_path,
            steamapps / "common" / "Deadlock" / "game" / "citadel" / "console.log",
        )

    def test_reads_log_only_while_deadlock_process_is_running(self) -> None:
        window = CompanionWindow.__new__(CompanionWindow)
        window._stop = threading.Event()
        window._set_status = MagicMock()
        window._current_delivery_settings = MagicMock(return_value=(None, None))
        tailer = MagicMock(poll_seconds=0.01)
        tailer.read_available.return_value = (100141930,)
        monitor_stop = MagicMock()
        monitor_stop.wait.side_effect = (False, False, True)

        with (
            patch(
                "duckies_companion.ui.is_deadlock_running",
                side_effect=(False, True, False),
            ),
            patch("duckies_companion.ui.report_match_with_retries") as report,
        ):
            window._monitor(tailer, monitor_stop)

        tailer.read_available.assert_called_once_with()
        report.assert_called_once_with(100141930, None, None)
