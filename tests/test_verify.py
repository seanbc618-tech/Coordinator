import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.verify import run_verification


class VerificationTests(unittest.TestCase):
    def test_empty_command_list_fails_and_writes_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()

            result = run_verification([], worktree, run_dir)

            self.assertFalse(result.passed)
            self.assertEqual(result.results, [])
            self.assertIn("no verification commands configured", (run_dir / "verifier.log").read_text())

    def test_blank_command_fails_and_writes_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()

            result = run_verification(["   "], worktree, run_dir)

            self.assertFalse(result.passed)
            self.assertEqual(len(result.results), 1)
            self.assertNotEqual(result.results[0].exit_code, 0)
            self.assertIn("empty verification command", (run_dir / "verifier.log").read_text())

    def test_missing_binary_fails_and_writes_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()

            command = "missing-local-cli-coordinator-verifier-binary"
            result = run_verification([command], worktree, run_dir)

            self.assertFalse(result.passed)
            self.assertEqual(len(result.results), 1)
            self.assertNotEqual(result.results[0].exit_code, 0)
            log = (run_dir / "verifier.log").read_text()
            self.assertIn(command, log)
            self.assertIn("error:", log)

    def test_verification_pass_and_fail_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            run_dir = root / "run"
            worktree.mkdir()
            run_dir.mkdir()

            passed = run_verification([f"{sys.executable} -c \"print('ok')\""], worktree, run_dir)
            self.assertTrue(passed.passed)
            self.assertEqual(passed.results[0].exit_code, 0)

            failed = run_verification([f"{sys.executable} -c \"raise SystemExit(7)\""], worktree, run_dir)
            self.assertFalse(failed.passed)
            self.assertEqual(failed.results[0].exit_code, 7)
            self.assertTrue((run_dir / "verifier.log").exists())
