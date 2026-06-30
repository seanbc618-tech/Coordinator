"""Phase 10 notification sink tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class NotificationSinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_file_sink_appends_jsonl(self) -> None:
        from local_cli_coordinator.notification_sinks import deliver_to_file_sink

        path = self.state_dir / "notifications.jsonl"
        deliver_to_file_sink(
            path,
            payload={"project_id": "p1", "title": "CI failed"},
        )
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["title"], "CI failed")

    def test_command_sink_uses_stdin_not_shell(self) -> None:
        from local_cli_coordinator.notification_sinks import deliver_to_command_sink

        script = self.tmp / "capture.py"
        script.write_text(
            "import json,sys\nprint(json.dumps(json.load(sys.stdin)))\n",
            encoding="utf-8",
        )
        injection = "x; touch /tmp/pwned-notify"
        result = deliver_to_command_sink(
            [sys.executable, str(script)],
            payload={"head": injection},
        )
        self.assertEqual(result.status, "sent")
        self.assertFalse(Path("/tmp/pwned-notify").exists())


if __name__ == "__main__":
    unittest.main()