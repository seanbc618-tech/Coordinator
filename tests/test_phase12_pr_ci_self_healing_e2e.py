"""Phase 12 E2E: PR/CI self-healing RPCs and slash routing.

Owner: Grok (Phase 12 Task 0)
Expected before implementation: missing RPC methods and slash commands.
"""

from __future__ import annotations

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
        request_id="req-phase12",
        project_id=project_id,
        method=method,
        params=params,
    )


class Phase12RpcTests(unittest.TestCase):
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
        register_project(self.conn, inspect_project(self.repo), confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]
        self.task_id = create_task(
            self.conn,
            title="Heal task",
            repo="test-repo",
            source_path="heal.md",
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
        from local_cli_coordinator import github_delivery

        self.delivery = github_delivery.create_delivery_record(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            repo_id="test-repo",
            branch_name="coord/heal-task",
            base_branch="main",
            status="open",
            pr_number=88,
            pr_url="https://github.com/example/coordinator/pull/88",
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_pr_health_rpc_returns_records(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("project.pr.health", self.project_id)
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("records", resp.result)

    def test_project_pr_heal_rpc_runs_bounded_cycle(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("project.pr.heal", self.project_id, dry_run=True),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("attempts", resp.result)

    def test_project_pr_rebase_rpc_defaults_to_dry_run(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "project.pr.rebase",
                self.project_id,
                delivery_id=self.delivery.id,
                dry_run=True,
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("status", resp.result)

    def test_project_pr_reviews_rpc_lists_unresolved(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("project.pr.reviews", self.project_id)
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("reviews", resp.result)

    def test_project_pr_update_evidence_rpc_refreshes_body(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "project.pr.update_evidence",
                self.project_id,
                delivery_id=self.delivery.id,
                dry_run=True,
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("updated", resp.result)


class Phase12SlashRoutingTests(unittest.TestCase):
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
        register_project(self.conn, inspect_project(self.repo), confirmed=True)
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

    def test_heal_slash_maps_to_project_pr_heal(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/heal", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.pr.heal", methods)

    def test_stale_slash_maps_to_project_pr_health(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/stale", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.pr.health", methods)

    def test_ci_failures_slash_maps_to_project_pr_health(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/ci failures", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.pr.health", methods)

    def test_reviews_slash_maps_to_project_pr_reviews(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/reviews", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.pr.reviews", methods)

    def test_pr_update_slash_maps_to_project_pr_update_evidence(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/pr update 1",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.pr.update_evidence", methods)

    def test_rebase_slash_maps_to_project_pr_rebase(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/rebase 1",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.pr.rebase", methods)


if __name__ == "__main__":
    unittest.main()