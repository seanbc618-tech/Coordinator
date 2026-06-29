"""Red tests for Phase 7 strategic autonomy E2E integration.

Owner: Grok (Phase 7 Task 0)
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

from local_cli_coordinator.config import CoordinatorConfig
from local_cli_coordinator.config_runtime import load_config_for_paths
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope
from tests.fixtures.fake_supervisor import FakeSupervisor
from tests.helpers import ROOT, SRC, init_git_repo, insert_terminal_task

_PYTHON = sys.executable


def _write_config(config_dir: Path, repo_path: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    fake_commander = Path(__file__).resolve().parent / "fixtures" / "fake_commander.py"
    (config_dir / "agents.toml").write_text(textwrap.dedent(f"""
        [agents.worker]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"

        [agents.commander]
        command = "{_PYTHON} {fake_commander}"
        capabilities = ["code", "tests", "docs", "research"]
        max_concurrency = 1
        role = "commander"
    """).strip())
    (config_dir / "repos.toml").write_text(textwrap.dedent(f"""
        [repos.test-repo]
        path = "{repo_path}"
        default_branch = "main"
        allow_push = false
        merge_policy = "no_push"
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
        max_iterations_per_tick = 1
        max_evaluations_per_iteration = 3
        max_admissions_per_iteration = 1
        max_generated_backlog_per_iteration = 3
        commander_generation_timeout_seconds = 45
        wait_when_running = true
        require_evaluation_before_followup = true
        pause_after_consecutive_failures = 3

        [overnight]
        quiet_start = "22:00"
        quiet_end = "08:00"

        [daemon_policy]
        poll_interval_seconds = 5
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
        request_id="req-phase7",
        project_id=project_id,
        method=method,
        params=params,
    )


class StrategyRPCTests(unittest.TestCase):
    """project.strategy returns milestone objective summary."""

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
        self.goal_id = create_goal(
            self.conn, "E2E goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, self.goal_id, "active")
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(config=self.config, broker=EventBroker())

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_strategy_rpc_exists(self) -> None:
        from local_cli_coordinator.strategy import create_milestone

        create_milestone(
            self.conn,
            project_id=self.project_id,
            title="Phase 7 objective",
            goal_id=self.goal_id,
        )
        self.conn.commit()
        resp = self.methods.handle(
            self.conn, _request("project.strategy", self.project_id)
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("current_milestone", resp.result)
        self.assertEqual(
            resp.result["current_milestone"]["title"], "Phase 7 objective"
        )


class RecoveryRPCTests(unittest.TestCase):
    """project.recoveries lists pending recovery proposals."""

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
        self.task_id = "e2e-fail-task"
        insert_terminal_task(
            self.conn,
            task_id=self.task_id,
            title="E2E failed task",
            state="failed",
            project_id=self.project_id,
            verification_commands="false",
        )
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(config=self.config, broker=EventBroker())

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_recoveries_rpc_lists_pending(self) -> None:
        from local_cli_coordinator.recovery import propose_recovery_for_failed_task

        propose_recovery_for_failed_task(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
        )
        self.conn.commit()
        resp = self.methods.handle(
            self.conn, _request("project.recoveries", self.project_id)
        )
        self.assertTrue(resp.ok, resp.error)
        proposals = resp.result.get("proposals") or []
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["task_id"], self.task_id)


class AgentsRPCTests(unittest.TestCase):
    """project.agents returns scorecard routing hints."""

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
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(config=self.config, broker=EventBroker())

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_agents_rpc_returns_scorecards(self) -> None:
        from local_cli_coordinator.agent_scorecard import record_agent_outcome

        record_agent_outcome(
            self.conn,
            agent_id="worker",
            role="worker",
            outcome="success",
        )
        self.conn.commit()
        resp = self.methods.handle(
            self.conn, _request("project.agents", self.project_id)
        )
        self.assertTrue(resp.ok, resp.error)
        agents = resp.result.get("agents") or []
        self.assertTrue(any(entry.get("agent_id") == "worker" for entry in agents))


class OvernightRPCTests(unittest.TestCase):
    """project.overnight exposes schedule and latest summary."""

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
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(config=self.config, broker=EventBroker())

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_overnight_rpc_returns_window(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("project.overnight", self.project_id)
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result.get("quiet_start"), "22:00")
        self.assertEqual(resp.result.get("quiet_end"), "08:00")


class Phase7SlashRoutingTests(unittest.TestCase):
    """Slash commands route through Supervisor RPC."""

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

    def test_strategy_slash_maps_to_project_strategy(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/strategy", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.strategy", methods)

    def test_recoveries_slash_maps_to_project_recoveries(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/recoveries", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.recoveries", methods)

    def test_overnight_slash_maps_to_project_overnight(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/overnight start --until 08:00",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.overnight", methods)


if __name__ == "__main__":
    unittest.main()