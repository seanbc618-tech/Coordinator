"""Red tests for Phase 6D mock provider parity harness.

Owner: Grok (Phase 6D Task 0)
Expected before implementation: missing ``mock_provider`` module or CLI subcommand.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT, SRC

_PYTHON = sys.executable

_COMMANDER_FIXTURE = {
    "schema_version": 2,
    "intent": "task_request",
    "user_reply": "Created one task.",
    "goal_status": "active",
    "progress_summary": "Planning next task.",
    "stop_reason": None,
    "tasks": [
        {
            "title": "Add operability tests",
            "repo": "test-repo",
            "capabilities": ["code"],
            "goal": "Cover mock provider",
            "acceptance_criteria": ["tests pass"],
            "verification_commands": ["true"],
            "expected_files": 1,
            "expected_minutes": 10,
            "parent_task_id": None,
            "rationale": "Need deterministic CI coverage.",
        }
    ],
}

_WORKER_FIXTURE = {
    "exit_code": 0,
    "stdout": "worker completed\n",
    "stderr": "",
    "changed_files": ["src/example.py"],
    "log_text": "worker completed\n",
}


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    return subprocess.run(
        [_PYTHON, "-m", "local_cli_coordinator", *args],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class MockProviderParityRedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mock_commander_fixture_validates_schema_without_live_binary(self) -> None:
        from local_cli_coordinator.mock_provider import validate_commander_fixture

        fixture_path = self.fixture_dir / "commander.json"
        fixture_path.write_text(json.dumps(_COMMANDER_FIXTURE))
        result = validate_commander_fixture(fixture_path)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["intent"], "task_request")
        self.assertEqual(len(result["tasks"]), 1)

    def test_mock_worker_fixture_generates_deterministic_agent_log(self) -> None:
        from local_cli_coordinator.mock_provider import render_worker_fixture

        fixture_path = self.fixture_dir / "worker.json"
        fixture_path.write_text(json.dumps(_WORKER_FIXTURE))
        rendered = render_worker_fixture(fixture_path)
        self.assertEqual(rendered["exit_code"], 0)
        self.assertIn("worker completed", rendered["stdout"])
        self.assertEqual(rendered["changed_files"], ["src/example.py"])

    def test_mock_provider_cli_runs_without_network(self) -> None:
        fixture_path = self.fixture_dir / "commander.json"
        fixture_path.write_text(json.dumps(_COMMANDER_FIXTURE))
        result = _run_cli(
            "mock-provider",
            "run",
            "commander",
            "--fixture",
            str(fixture_path),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["intent"], "task_request")