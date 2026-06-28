"""Red tests for Phase 6D permission mode reporting.

Owner: Grok (Phase 6D Task 0)
Expected before implementation: missing ``permission_modes`` module or JSON fields.
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


def _run_cli_with_home(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["COORDINATOR_HOME"] = str(home)
    return subprocess.run(
        [_PYTHON, "-m", "local_cli_coordinator", *args],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class PermissionModesRedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_permission_modes_default_roles_are_safe(self) -> None:
        from local_cli_coordinator.permission_modes import resolve_agent_permissions

        (self.paths.config_dir / "agents.toml").write_text(textwrap.dedent("""
            [agents.commander]
            command = "true"
            capabilities = ["code"]
            max_concurrency = 1
            role = "commander"

            [agents.reviewer]
            command = "true"
            capabilities = ["review"]
            max_concurrency = 1
            role = "spec_reviewer"

            [agents.worker]
            command = "true"
            capabilities = ["code"]
            max_concurrency = 1
            role = "worker"
        """).strip())
        (self.paths.config_dir / "repos.toml").write_text("[repos]\n")
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
        """).strip())

        commander = resolve_agent_permissions(self.paths, agent_id="commander", role="commander")
        reviewer = resolve_agent_permissions(self.paths, agent_id="reviewer", role="spec_reviewer")
        worker = resolve_agent_permissions(self.paths, agent_id="worker", role="worker")

        self.assertEqual(commander.mode, "read-only")
        self.assertEqual(reviewer.mode, "read-only")
        self.assertEqual(worker.mode, "workspace-write")

    def test_agent_allowed_tools_are_reported_in_config_json(self) -> None:
        (self.paths.config_dir / "agents.toml").write_text(textwrap.dedent("""
            [agents.claude_worker]
            command = "true"
            capabilities = ["code"]
            max_concurrency = 1
            role = "worker"

            [agents.claude_worker.permissions]
            mode = "workspace-write"
            allowed_tools = ["read", "edit", "shell:test", "shell:lint"]
            denied_tools = ["shell:push", "shell:merge"]
        """).strip())
        (self.paths.config_dir / "repos.toml").write_text("[repos]\n")
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

            [policy.permissions]
            default_mode = "workspace-write"
            danger_requires_confirmation = true
        """).strip())

        result = _run_cli_with_home(self.home, "config", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["ok"])
        agents = data["data"]["agents"]
        worker = next(item for item in agents if item["id"] == "claude_worker")
        self.assertEqual(worker["permissions"]["mode"], "workspace-write")
        self.assertEqual(
            worker["permissions"]["allowed_tools"],
            ["read", "edit", "shell:test", "shell:lint"],
        )
        self.assertEqual(
            worker["permissions"]["denied_tools"],
            ["shell:push", "shell:merge"],
        )