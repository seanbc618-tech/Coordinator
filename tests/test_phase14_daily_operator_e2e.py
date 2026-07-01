"""Phase 14 E2E: daily operator RPCs and slash routing.

Owner: Grok (Phase 14 Task 0)
Expected before implementation: Phase 14 RPC methods and slash commands missing.
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

from local_cli_coordinator.config_runtime import load_config_for_paths
from local_cli_coordinator.db import (
    connect,
    create_task,
    finish_attempt,
    init_db,
    start_attempt,
    transition_task,
)
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope
from tests.fixtures.fake_supervisor import FakeSupervisor
from tests.helpers import ROOT, SRC, init_git_repo

_PYTHON = sys.executable


def _write_config(config_dir: Path, repo_path: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text(textwrap.dedent("""
        [agents.worker]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"
    """).strip())
    (config_dir / "repos.toml").write_text(textwrap.dedent(f"""
        [repos.test-repo]
        path = "{repo_path}"
        default_branch = "main"
        allow_push = false
        merge_policy = "no_push"
        review_policy = "tests_only"
    """).strip())
    (config_dir / "policy.toml").write_text(textwrap.dedent("""
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

        [notifications]
        allow_command_sink = false
    """).strip())


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


def _request(method: str, project_id: str, **params) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id="req-phase14-e2e",
        project_id=project_id,
        method=method,
        params=params,
    )


class Phase14RpcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        register_project(self.conn, inspect_project(self.repo), confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]
        self.task_id = create_task(
            self.conn,
            title="E2E failure task",
            repo="test-repo",
            source_path="e2e.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["false"],
            project_id=self.project_id,
        )
        transition_task(self.conn, self.task_id, "failed", "verification failed")
        attempt_id = start_attempt(
            self.conn,
            self.task_id,
            agent_id="worker",
            command="true",
        )
        finish_attempt(
            self.conn,
            attempt_id,
            exit_code=1,
            result_class="verification_failed",
        )
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(
            config=self.config,
            broker=EventBroker(),
            paths=self.paths,
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_operator_doctor_rpc_runs_dry_run(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("operator.doctor", self.project_id, dry_run=True),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result.get("mode"), "repair_dry_run")
        self.assertIn("findings", resp.result)

    def test_operator_repair_rpc_requires_apply_confirmation(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("operator.repair", self.project_id, dry_run=True),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertFalse(resp.result.get("applied"))

    def test_global_pause_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "global.pause",
                self.project_id,
                reason="e2e pause",
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertTrue(resp.result.get("global_pause"))

    def test_global_resume_rpc(self) -> None:
        self.methods.handle(
            self.conn,
            _request("global.pause", self.project_id, reason="e2e pause"),
        )
        resp = self.methods.handle(
            self.conn,
            _request("global.resume", self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertFalse(resp.result.get("global_pause"))


class Phase14SlashRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        register_project(self.conn, inspect_project(self.repo), confirmed=True)
        self.conn.commit()
        self.task_id = create_task(
            self.conn,
            title="Slash failure task",
            repo="test-repo",
            source_path="slash.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["false"],
            project_id=self.conn.execute(
                "select id from projects limit 1"
            ).fetchone()["id"],
        )
        self.conn.commit()
        self._orig_home = os.environ.get("COORDINATOR_HOME")
        os.environ["COORDINATOR_HOME"] = str(self.home)
        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        if self._orig_home is not None:
            os.environ["COORDINATOR_HOME"] = self._orig_home
        else:
            os.environ.pop("COORDINATOR_HOME", None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_doctor_slash_maps_to_operator_doctor(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/doctor", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.doctor", methods)

    def test_repair_slash_maps_to_operator_repair(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/repair --dry-run", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.repair", methods)

    def test_health_slash_maps_to_operator_health(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/health", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.health", methods)

    def test_morning_slash_maps_to_operator_morning(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/morning", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.morning", methods)

    def test_why_task_slash_maps_to_operator_explain_failure(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            f"/why {self.task_id}",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.explain_failure", methods)

    def test_pause_all_slash_maps_to_global_pause(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/pause all", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("global.pause", methods)

    def test_resume_all_slash_maps_to_global_resume(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/resume all", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("global.resume", methods)

    def test_dashboard_slash_includes_phase14_fields(self) -> None:
        result = _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/dashboard", cwd=self.repo
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout.strip().splitlines()[0])
        self.assertTrue(envelope["ok"], envelope)
        payload = envelope["result"]
        self.assertIn("global_pause", payload)
        self.assertIn("morning_handoff_at", payload)


if __name__ == "__main__":
    unittest.main()