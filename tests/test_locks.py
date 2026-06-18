import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.locks import (
    LockInfo,
    acquire_lock,
    lockfile_path,
    release_lock,
)


class LockfileTests(unittest.TestCase):
    def test_lockfile_path_points_to_state_coordinator_lock(self) -> None:
        root = Path("/tmp/test-root")
        self.assertEqual(lockfile_path(root), root / "state" / "coordinator.lock")

    def test_acquire_lock_creates_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = acquire_lock(root)
            self.assertIsInstance(result, LockInfo)
            self.assertTrue((root / "state" / "coordinator.lock").exists())
            self.assertEqual(result.pid, __import__("os").getpid())

    def test_acquire_lock_writes_json_with_pid_and_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acquire_lock(root)
            data = json.loads((root / "state" / "coordinator.lock").read_text())

        self.assertIn("pid", data)
        self.assertIn("acquired_at", data)
        self.assertEqual(data["pid"], __import__("os").getpid())

    def test_second_acquire_without_force_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acquire_lock(root)
            result = acquire_lock(root)

        self.assertIsInstance(result, str)
        self.assertIn("already running", result)

    def test_acquire_with_force_overwrites_existing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acquire_lock(root)
            result = acquire_lock(root, force=True)

        self.assertIsInstance(result, LockInfo)

    def test_release_lock_removes_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acquire_lock(root)
            released = release_lock(root)

        self.assertTrue(released)
        self.assertFalse((root / "state" / "coordinator.lock").exists())

    def test_release_lock_returns_false_when_no_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(release_lock(root))

    def test_stale_lock_is_overwritten_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Write a lockfile with a nonexistent PID
            lock_path = root / "state" / "coordinator.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text(
                json.dumps({"pid": 999999999, "acquired_at": "2026-01-01T00:00:00Z"}),
                encoding="utf-8",
            )
            result = acquire_lock(root)

        self.assertIsInstance(result, LockInfo)

    def test_acquire_lock_creates_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse((root / "state").exists())
            acquire_lock(root)
            self.assertTrue((root / "state").exists())
