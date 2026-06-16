import unittest

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
