import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import run_cli


class CliSmokeTests(unittest.TestCase):
    def test_help_lists_core_commands(self) -> None:
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("daemon", result.stdout)
        self.assertIn("inbox", result.stdout)
        self.assertIn("status", result.stdout)
        self.assertIn("doctor", result.stdout)

    def test_doctor_runs_without_configuration(self) -> None:
        result = run_cli("doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Coordinator doctor", result.stdout)

    def test_no_args_launches_tui_instead_of_help(self) -> None:
        with mock.patch(
            "local_cli_coordinator.tui_launcher.launch_tui",
            return_value=0,
        ) as launch_mock:
            from local_cli_coordinator.cli import main

            code = main([])
        self.assertEqual(code, 0)
        launch_mock.assert_called_once()

    def test_administrative_subcommands_keep_current_behavior(self) -> None:
        """Admin CLI must not launch TUI; supervisor status uses isolated home."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch(
                "local_cli_coordinator.tui_launcher.launch_tui",
            ) as launch_mock, mock.patch.dict(
                os.environ,
                {"COORDINATOR_HOME": str(home)},
                clear=False,
            ):
                from local_cli_coordinator.cli import main

                self.assertEqual(main(["doctor"]), 0)
                self.assertEqual(main(["status"]), 0)
                self.assertEqual(main(["supervisor", "status"]), 1)
            launch_mock.assert_not_called()

    def test_unknown_subcommand_still_errors(self) -> None:
        from local_cli_coordinator.cli import main

        with self.assertRaises(SystemExit) as ctx:
            main(["not-a-command"])
        self.assertEqual(ctx.exception.code, 2)
