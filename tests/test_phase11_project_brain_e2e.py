"""Phase 11 project brain E2E: RPCs, slash routing, memory learning."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config_runtime import load_config_for_paths
from local_cli_coordinator.db import connect, create_task, init_db, transition_task
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
        request_id="req-phase11",
        project_id=project_id,
        method=method,
        params=params,
    )


class ProjectBrainRpcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "core.py").write_text("VERSION = 1\n")
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
        self.methods = SupervisorMethods(config=self.config, broker=EventBroker())

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_brain_rpc_returns_snapshot(self) -> None:
        response = self.methods.handle(
            self.conn, _request("project.brain", self.project_id)
        )
        self.assertTrue(response.ok, response.error)
        self.assertIn("snapshot", response.result)

    def test_project_map_rpc_lists_components(self) -> None:
        response = self.methods.handle(
            self.conn, _request("project.map", self.project_id)
        )
        self.assertTrue(response.ok, response.error)
        self.assertIn("cards", response.result)

    def test_project_context_rpc_for_task(self) -> None:
        task_id = create_task(
            self.conn,
            title="Fix db",
            repo="test-repo",
            source_path="t.md",
            priority="normal",
            capabilities=["code"],
            goal="g",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        self.conn.commit()
        response = self.methods.handle(
            self.conn,
            _request("project.context", self.project_id, task_id=task_id),
        )
        self.assertTrue(response.ok, response.error)
        self.assertIn("packet_id", response.result)

    def test_failed_task_creates_failure_memory(self) -> None:
        from local_cli_coordinator.project_brain import learn_from_task_outcome

        task_id = create_task(
            self.conn,
            title="Broken",
            repo="test-repo",
            source_path="t.md",
            priority="normal",
            capabilities=["code"],
            goal="g",
            acceptance_criteria=["done"],
            verification_commands=["false"],
            project_id=self.project_id,
        )
        transition_task(self.conn, task_id, "running", "start")
        transition_task(self.conn, task_id, "failed", "boom")
        self.conn.commit()
        learn_from_task_outcome(self.conn, project_id=self.project_id, task_id=task_id)
        self.conn.commit()
        row = self.conn.execute(
            """
            select count(*) as c from project_brain_memories
            where project_id = ? and memory_type = 'failure'
            """,
            (self.project_id,),
        ).fetchone()
        self.assertGreaterEqual(int(row["c"]), 1)


class Phase11SlashRoutingTests(unittest.TestCase):
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

    def test_brain_slash_maps_to_project_brain(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/brain", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.brain", methods)

    def test_map_slash_maps_to_project_map(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/map", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.map", methods)

    def test_context_slash_maps_to_project_context(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/context task-abc",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.context", methods)


if __name__ == "__main__":
    unittest.main()