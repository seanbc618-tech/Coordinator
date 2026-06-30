"""Red tests for Phase 8 evidence review E2E integration.

Owner: Grok (Phase 8 Task 0)
Expected before implementation: missing RPC methods and slash routing.
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
from local_cli_coordinator.db import connect, create_task, init_db
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
        review_policy = "risky_human"
        autonomy_enabled = false
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
        request_id="req-phase8",
        project_id=project_id,
        method=method,
        params=params,
    )


class EvidenceRPCTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]
        self.task_id = create_task(
            self.conn,
            title="E2E evidence task",
            repo="test-repo",
            source_path="e2e.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(config=self.config, broker=EventBroker())

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_evidence_rpc_lists_records(self) -> None:
        from local_cli_coordinator.evidence import record_evidence

        record_evidence(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            evidence_type="command",
            status="passed",
            summary="ok",
            data={"command": "true"},
        )
        self.conn.commit()
        resp = self.methods.handle(
            self.conn,
            _request("project.evidence", self.project_id, task_id=self.task_id),
        )
        self.assertTrue(resp.ok, resp.error)
        items = resp.result.get("evidence") or []
        self.assertEqual(len(items), 1)

    def test_project_merge_ready_respects_human_review_policy(self) -> None:
        from local_cli_coordinator.risk import assess_task_risk

        assess_task_risk(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            changed_files=["migrations/018.sql"],
            diff_text="ALTER TABLE x",
        )
        self.conn.commit()
        resp = self.methods.handle(
            self.conn,
            _request("project.merge_ready", self.project_id, task_id=self.task_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertFalse(resp.result.get("merge_ready", True))
        self.assertTrue(resp.result.get("requires_human_review"))


class Phase8SlashRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
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

    def test_evidence_slash_maps_to_project_evidence(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/evidence task-1", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.evidence", methods)

    def test_review_slash_maps_to_project_review(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/review task-1", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.review", methods)

    def test_risk_slash_maps_to_project_risk(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/risk task-1", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.risk", methods)

    def test_merge_ready_slash_maps_to_project_merge_ready(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/merge-ready task-1",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.merge_ready", methods)


if __name__ == "__main__":
    unittest.main()