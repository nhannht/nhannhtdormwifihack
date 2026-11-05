"""The command line: parsing, the settings subcommands, and the unit file."""

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


class UnitFileTests(unittest.TestCase):
    def test_the_unit_runs_the_watcher_and_restarts_it(self):
        unit = cli.unit_contents(
            Path("/usr/local/bin/dormwifi"), ["run"], Path("/home/someone/.config/x")
        )
        self.assertIn('ExecStart=/usr/local/bin/dormwifi run', unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("WantedBy=default.target", unit)

    def test_a_path_with_a_space_is_quoted(self):
        # systemd splits ExecStart on whitespace, so an unquoted path with a space
        # becomes two arguments and the unit fails naming a binary that exists.
        unit = cli.unit_contents(
            Path("/home/some one/bin/dormwifi"), ["run"], Path("/home/some one/.config/x")
        )
        self.assertIn('ExecStart="/home/some one/bin/dormwifi" run', unit)
        self.assertIn('WorkingDirectory="/home/some one/bin"', unit)

    def test_a_quote_in_a_path_cannot_break_out(self):
        unit = cli.unit_contents(
            Path('/tmp/a"b/dormwifi'), ["run"], Path("/tmp/logs")
        )
        self.assertIn('WorkingDirectory="/tmp/a\\"b"', unit)


class SettingsSubcommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.config_home = Path(self.directory.name)
        self.settings_path = self.config_home / "nhannhtdormwifihack" / "settings.json"
        self.patch = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config_home)})
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
