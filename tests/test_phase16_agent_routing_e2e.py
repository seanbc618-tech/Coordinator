"""Phase 16 E2E: agent routing RPCs, slash commands, and claim integration.

Owner: Grok (Phase 16 Task 0)
Expected before implementation: agent.* RPCs and routing slash commands missing.
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
    claim_project_ready_task,
    connect,
    create_task,
    init_db,
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
        [agents.alpha]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"
        fallback_agents = ["beta"]

        [agents.beta]
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
        request_id="req-phase16-e2e",
        project_id=project_id,
        method=method,
        params=params,
    )


class Phase16RpcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        (self.repo / "pyproject.toml").write_text("[project]\nname='demo'\n")
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
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(
            config=self.config,
            broker=EventBroker(),
            paths=self.paths,
        )
        self.task_id = create_task(
            self.conn,
            title="Route me",
            repo="test-repo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
            commit=True,
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_agent_list_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("agent.list", self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        agents = resp.result.get("agents") or []
        ids = {item["agent_id"] for item in agents}
        self.assertIn("alpha", ids)
        self.assertIn("beta", ids)

    def test_agent_detail_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("agent.detail", self.project_id, agent_id="alpha"),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result.get("agent_id"), "alpha")
        self.assertIn("profile", resp.result)

    def test_agent_route_preview_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "agent.route.preview",
                self.project_id,
                task_id=self.task_id,
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("selected_agent_id", resp.result)
        self.assertIn("reason", resp.result)
        self.assertIn("candidates", resp.result)

    def test_agent_benchmark_rpc_uses_local_fixtures(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("agent.benchmark", self.project_id, scope="agents"),
        )
        self.assertTrue(resp.ok, resp.error)
        results = resp.result.get("results") or []
        self.assertTrue(results)
        row = self.conn.execute(
            "select count(*) as cnt from agent_benchmark_runs"
        ).fetchone()
        self.assertGreater(int(row["cnt"]), 0)

    def test_claim_records_route_decision(self) -> None:
        task, agent_id = claim_project_ready_task(
            self.conn,
            self.project_id,
            self.config,
        )
        self.assertIsNotNone(task)
        self.assertIsNotNone(agent_id)
        row = self.conn.execute(
            """
            select selected_agent_id, reason
            from agent_route_decisions
            where task_id = ?
            """,
            (self.task_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["selected_agent_id"], agent_id)


class Phase16SlashRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        (self.repo / "pyproject.toml").write_text("[project]\nname='demo'\n")
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

    def test_agents_slash_maps_to_agent_list(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/agents", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("agent.list", methods)

    def test_agent_slash_maps_to_agent_detail(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/agent alpha",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("agent.detail", methods)

    def test_route_slash_maps_to_route_preview(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/route task-missing",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("agent.route.preview", methods)

    def test_benchmark_agents_slash_maps_to_agent_benchmark(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/benchmark agents",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("agent.benchmark", methods)


if __name__ == "__main__":
    unittest.main()