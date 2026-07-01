"""Phase 17 E2E: simulation RPCs, CLI, and slash routing."""

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
        [agents.alpha]
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
        autonomy_enabled = true
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

        [autonomy]
        enabled = true

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
        request_id="req-phase17-e2e",
        project_id=project_id,
        method=method,
        params=params,
    )


class Phase17RpcTests(unittest.TestCase):
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
        self.project_id = register_project(
            self.conn, inspect_project(self.repo), confirmed=True
        )
        create_task(
            self.conn,
            title="ready",
            repo="test-repo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=[],
            project_id=self.project_id,
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

    def test_simulation_run_rpc(self) -> None:
        before_tasks = self.conn.execute("select count(*) as c from tasks").fetchone()["c"]
        resp = self.methods.handle(
            self.conn,
            _request("simulation.run", self.project_id, scope="global", horizon_hours=8),
        )
        self.assertTrue(resp.ok, resp.error)
        assert resp.result is not None
        self.assertIn("simulation_run_id", resp.result)
        self.assertTrue(resp.result.get("forecast"))
        after_tasks = self.conn.execute("select count(*) as c from tasks").fetchone()["c"]
        self.assertEqual(before_tasks, after_tasks)

    def test_simulation_report_and_list_rpcs(self) -> None:
        run_resp = self.methods.handle(
            self.conn,
            _request("simulation.run", self.project_id, scope="project", horizon_hours=4),
        )
        self.assertTrue(run_resp.ok, run_resp.error)
        run_id = run_resp.result["simulation_run_id"]
        report_resp = self.methods.handle(
            self.conn,
            _request("simulation.report", self.project_id, simulation_run_id=run_id),
        )
        self.assertTrue(report_resp.ok, report_resp.error)
        self.assertTrue(report_resp.result.get("forecast"))
        list_resp = self.methods.handle(
            self.conn,
            _request("simulation.list", self.project_id, limit=5),
        )
        self.assertTrue(list_resp.ok, list_resp.error)
        self.assertGreaterEqual(list_resp.result["count"], 1)


class Phase17CliTests(unittest.TestCase):
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
        self.project_id = register_project(
            self.conn, inspect_project(self.repo), confirmed=True
        )
        self.conn.commit()
        self._orig_home = os.environ.get("COORDINATOR_HOME")
        os.environ["COORDINATOR_HOME"] = str(self.home)

    def tearDown(self) -> None:
        self.conn.close()
        if self._orig_home is not None:
            os.environ["COORDINATOR_HOME"] = self._orig_home
        else:
            os.environ.pop("COORDINATOR_HOME", None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_simulate_overnight_json(self) -> None:
        result = _run_cli_with_home(self.home, "simulate", "overnight", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertIn("simulation_run_id", payload["data"])

    def test_cli_simulate_project_json(self) -> None:
        result = _run_cli_with_home(
            self.home,
            "simulate",
            "project",
            "--project",
            self.project_id,
            "--hours",
            "6",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["report"]["scope"], "project")


class Phase17SlashRoutingTests(unittest.TestCase):
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

    def test_simulate_slash_maps_to_simulation_run(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/simulate overnight",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("simulation.run", methods)

    def test_simulate_project_slash_maps_to_simulation_run(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/simulate project",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("simulation.run", methods)
        params = [p for m, p in self.server.drain_requests() if m == "simulation.run"]
        # drain_requests cleared; re-check via second call
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/simulate project 4",
            cwd=self.repo,
        )
        for method, param in self.server.drain_requests():
            if method == "simulation.run":
                self.assertEqual(param.get("scope"), "project")

    def test_what_if_slash_maps_to_simulation_run(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/what-if overnight",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("simulation.run", methods)

    def test_simulate_preset_slash_keeps_onboard_simulate(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/simulate preset overnight",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.onboard.simulate", methods)
        self.assertNotIn("project.onboard.apply", methods)


if __name__ == "__main__":
    unittest.main()