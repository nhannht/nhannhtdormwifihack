"""The command line: parsing, the settings subcommands, and the startup script."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dormwifi import cli
from dormwifi.settings import Settings


class ParsingTests(unittest.TestCase):
    def test_verbose_works_on_either_side_of_the_subcommand(self):
        # argparse writes a subparser's defaults over the top-level namespace, so
        # `-v run` used to come out as verbose=False without a word.
        self.assertTrue(cli.parse(["-v", "run"]).verbose)
        self.assertTrue(cli.parse(["run", "-v"]).verbose)
        self.assertFalse(cli.parse(["run"]).verbose)

    def test_settings_works_on_either_side_too(self):
        for argv in (
            ["--settings", "/tmp/x.json", "config", "path"],
            ["config", "path", "--settings", "/tmp/x.json"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(cli.parse(argv).settings_path, "/tmp/x.json")

    def test_no_subcommand_runs_the_watcher(self):
        options = cli.parse([])
        self.assertIs(options.handler, cli.command_run)
        self.assertFalse(options.menu)

    def test_the_menu_flag_is_spelled_either_way(self):
        self.assertTrue(cli.parse(["run", "--menu"]).menu)
        self.assertTrue(cli.parse(["run", "-g"]).menu)


class GuiCommandTests(unittest.TestCase):
    def test_an_existing_window_is_brought_forward_instead_of_a_second_one(self):
        options = cli.parse(["gui"])
        with (
            mock.patch("dormwifi.cli._signal_existing_window", return_value=True),
            mock.patch("dormwifi.cli._start_window") as start,
            mock.patch("dormwifi.cli.Console.line"),
        ):
            code = cli.command_gui(options)
        self.assertEqual(code, 0)
        start.assert_not_called()

    def test_a_window_is_opened_when_none_is_running(self):
        options = cli.parse(["gui"])
        with (
            mock.patch("dormwifi.cli._signal_existing_window", return_value=False),
            mock.patch("dormwifi.cli._start_window", return_value=0) as start,
        ):
            code = cli.command_gui(options)
        self.assertEqual(code, 0)
        start.assert_called_once_with(options)


class LauncherTests(unittest.TestCase):
    def test_the_launcher_runs_the_watcher_hidden_and_restarts_it(self):
        launcher = cli.launcher_contents(
            Path("C:/Program Files/Dorm WiFi/dormwifi.cmd"),
            ["run", "--verbose"],
            Path("C:/Users/me/AppData/Roaming/nhannhtdormwifihack/watch.vbs"),
        )
        self.assertIn('CreateObject("WScript.Shell")', launcher)
        self.assertIn("0, True", launcher)
        self.assertIn("WScript.Sleep 5000", launcher)
        self.assertIn("FileExists", launcher)
        self.assertIn("--verbose", launcher)

    def test_a_path_with_a_space_is_quoted_and_vbs_quotes_are_doubled(self):
        launcher = cli.launcher_contents(
            Path("C:/Program Files/Dorm WiFi/dormwifi.cmd"),
            ["run"],
            Path("C:/Users/me/AppData/Roaming/nhannhtdormwifihack/watch.vbs"),
        )
        self.assertIn(
            '"""C:/Program Files/Dorm WiFi/dormwifi.cmd"" run"', launcher
        )

    def test_a_normal_terminal_installs_through_the_startup_folder(self):
        options = cli.parse(["install"])
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "dormwifi.cmd"
            executable.write_text("@echo off\n", encoding="utf-8")
            with (
                mock.patch.dict(
                    os.environ, {"DORMWIFI_CONFIG_DIR": directory}
                ),
                mock.patch("dormwifi.cli._is_elevated", return_value=False),
                mock.patch("dormwifi.cli._entry_point", return_value=executable),
                mock.patch(
                    "dormwifi.cli._install_into_startup_folder", return_value=0
                ) as fallback,
            ):
                code = cli.command_install(options)
        self.assertEqual(code, 0)
        fallback.assert_called_once()

    def test_the_startup_fallback_also_creates_the_desktop_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / "watch.vbs"
            launcher.write_text("x", encoding="utf-8")
            startup = Path(directory) / "Startup"
            with (
                mock.patch("dormwifi.cli._startup_directory", return_value=startup),
                mock.patch("dormwifi.cli._report_shortcut") as shortcut,
            ):
                code = cli._install_into_startup_folder(launcher)
            self.assertEqual(code, 0)
            self.assertTrue((startup / "Dorm WiFi.vbs").is_file())
            shortcut.assert_called_once()

    def test_the_launcher_is_written_where_the_task_can_find_it(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "dormwifi.cmd"
            executable.write_text("@echo off\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ, {"DORMWIFI_CONFIG_DIR": directory}
            ):
                path = cli._write_launcher(executable, None, True)
            self.assertEqual(path, Path(directory) / "watch.vbs")
            text = path.read_text(encoding="utf-16")
            self.assertIn("--verbose", text)
            self.assertIn("run", text)

    def test_the_shortcut_targets_pythonw_and_this_checkout(self):
        completed = mock.Mock()
        completed.returncode = 0
        completed.stdout = "C:/Users/me/OneDrive/Desktop/Dorm WiFi.lnk"
        completed.stderr = ""
        with (
            mock.patch(
                "dormwifi.cli._window_interpreter",
                return_value=r"C:\Python\pythonw.exe",
            ),
            mock.patch("dormwifi.cli._run_powershell") as run,
        ):
            run.return_value = completed
            path = cli._create_desktop_shortcut()

        script = run.call_args.args[0]
        self.assertEqual(path, Path("C:/Users/me/OneDrive/Desktop/Dorm WiFi.lnk"))
        self.assertIn(r"C:\Python\pythonw.exe", script)
        self.assertIn("'-m dormwifi gui'", script)
        self.assertIn("Dorm WiFi.lnk", script)
        self.assertIn("dormwifi.ico", script)

    def test_a_quote_in_a_path_cannot_break_the_powershell_literal(self):
        self.assertEqual(cli._powershell_literal("a'b"), "'a''b'")

    def test_the_shortcut_command_is_parsed_either_way(self):
        options = cli.parse(["shortcut"])
        self.assertIs(options.handler, cli.command_shortcut)
        self.assertFalse(options.remove)
        self.assertTrue(cli.parse(["shortcut", "--remove"]).remove)


class SettingsSubcommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.config_home = Path(self.directory.name)
        self.settings_path = self.config_home / "settings.json"
        self.patch = mock.patch.dict(
            os.environ, {"DORMWIFI_CONFIG_DIR": str(self.config_home)}
        )
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.directory.cleanup()

    def run_cli(self, *arguments: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(arguments))
        return code, out.getvalue()

    def test_path_prints_where_the_settings_live(self):
        code, output = self.run_cli("config", "path")
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), str(self.settings_path))

    def test_show_prints_the_json_the_file_would_hold(self):
        code, output = self.run_cli("config", "show")
        self.assertEqual(code, 0)
        self.assertIn('"interval": 15', output)
        self.assertIn('"portal_endpoints"', output)

    def test_set_writes_a_whole_number(self):
        code, output = self.run_cli("config", "set", "interval", "20")
        self.assertEqual(code, 0)
        self.assertIn("Set interval to 20.", output)
        self.assertEqual(Settings.load(self.settings_path).interval, 20)

    def test_set_reads_true_and_false(self):
        self.run_cli("config", "set", "auto_connect", "no")
        self.assertFalse(Settings.load(self.settings_path).auto_connect)
        self.run_cli("config", "set", "auto_connect", "yes")
        self.assertTrue(Settings.load(self.settings_path).auto_connect)

    def test_set_refuses_a_number_that_is_not_one(self):
        code, _ = self.run_cli("config", "set", "interval", "later")
        self.assertEqual(code, 2)

    def test_set_refuses_a_key_that_does_not_exist(self):
        code, _ = self.run_cli("config", "set", "nonsense", "1")
        self.assertEqual(code, 2)

    def test_a_secret_is_never_echoed_back(self):
        # This line goes to the terminal, into shell history, and into the
        # journal for the installed service.
        _code, output = self.run_cli("config", "set", "gemini_api_key", "secret-value")
        self.assertIn("Set gemini_api_key.", output)
        self.assertNotIn("secret-value", output)

    def test_an_unreadable_settings_file_is_reported_not_raised(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text("{not json", encoding="utf-8")
        code, _ = self.run_cli("config", "show")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
