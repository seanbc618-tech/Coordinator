"""Red tests for Phase 6D config explain output.

Owner: Grok (Phase 6D Task 0)
Expected before implementation: missing ``config explain`` command or module.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import ROOT, SRC

_PYTHON = sys.executable


def _run_cli_with_home(
    home: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["COORDINATOR_HOME"] = str(home)
    return subprocess.run(
        [_PYTHON, "-m", "local_cli_coordinator", *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class ConfigExplainRedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        (self.paths.config_dir / "policy.toml").write_text(textwrap.dedent("""
            [task_policy]
            require_single_repo = false
            require_acceptance_criteria = false
            require_verification_commands = false
            require_handoff_summary = false
            max_files_touched = 20
            max_expected_minutes = 60
            max_attempts = 3
            split_if_touches_multiple_subsystems = false
            split_if_research_and_code_are_mixed = false
            max_tasks_per_day = 12
        """).strip())
        (self.paths.config_dir / "agents.toml").write_text(textwrap.dedent("""
            [agents.commander]
            command = "true"
            capabilities = ["code"]
            max_concurrency = 1
            role = "commander"
        """).strip())
        (self.paths.config_dir / "repos.toml").write_text("[repos]\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_config_explain_reports_source_for_policy_value(self) -> None:
        from local_cli_coordinator.config_explain import explain_config

        entries = explain_config(self.paths, key="policy.max_tasks_per_day")
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["key"], "policy.max_tasks_per_day")
        self.assertEqual(entry["effective_value"], 12)
        self.assertIn(entry["source_kind"], {"default", "config_file", "environment", "computed"})
        self.assertIn("source", entry)
        self.assertIn("explanation", entry)

    def test_config_explain_json_redacts_secret_like_values(self) -> None:
        (self.paths.config_dir / "agents.toml").write_text(textwrap.dedent("""
            [agents.commander]
            command = "true"
            capabilities = ["code"]
            max_concurrency = 1
            role = "commander"
            api_key = "sk-live-secret-value"
        """).strip())
        result = _run_cli_with_home(self.home, "config", "explain", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["ok"])
        payload = json.dumps(data)
        self.assertNotIn("sk-live-secret-value", payload)
        self.assertIn("[REDACTED]", payload)