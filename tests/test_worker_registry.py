"""Unit tests for worker subprocess registry used by task cancel."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest

from local_cli_coordinator.worker_registry import WorkerRegistry


class WorkerRegistryTests(unittest.TestCase):
    def test_terminate_sends_sigterm_then_sigkill(self) -> None:
        registry = WorkerRegistry()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name == "posix",
        )
        registry.register("task-sleep", process)
        self.assertTrue(registry.terminate("task-sleep", grace_seconds=0.2))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.05)
        self.assertIsNotNone(process.poll())

    def test_terminate_missing_task_is_noop(self) -> None:
        registry = WorkerRegistry()
        self.assertFalse(registry.terminate("missing-task"))


if __name__ == "__main__":
    unittest.main()