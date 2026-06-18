import tempfile
import unittest
from pathlib import Path

from tests.helpers import run_cli


class LoopStatusTests(unittest.TestCase):
    def test_status_loop_reports_readiness_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Loop Status", result.stdout)
        self.assertIn("Readiness:", result.stdout)

    def test_status_loop_reports_lock_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Lock:", result.stdout)

    def test_status_loop_reports_circuit_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Circuit breaker:", result.stdout)

    def test_status_loop_reports_active_leases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Active leases: 0", result.stdout)

    def test_status_loop_reports_tasks_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Tasks:", result.stdout)

    def test_status_loop_reports_human_review_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status", "--loop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Human review pending:", result.stdout)

    def test_status_without_loop_shows_original_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "status")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Loop Status", result.stdout)
        self.assertIn("no tasks", result.stdout)
