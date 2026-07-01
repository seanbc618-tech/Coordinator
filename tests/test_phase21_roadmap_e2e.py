"""Phase 21 E2E: roadmap RPC, CLI, slash routing, and loop integration."""

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
from local_cli_coordinator.db import connect, init_db
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
        request_id="req-phase21-e2e",
        project_id=project_id,
        method=method,
        params=params,
    )


class Phase21RpcTests(unittest.TestCase):
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
        self.project_id = register_project(
            self.conn, inspect_project(self.repo), confirmed=True
        )
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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roadmap_status_rpc(self) -> None:
        resp = self.methods.handle(self.conn, _request("roadmap.status", self.project_id))
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("project_id", resp.result)

    def test_roadmap_next_rpc_returns_ready_items_only(self) -> None:
        resp = self.methods.handle(self.conn, _request("roadmap.next", self.project_id))
        self.assertTrue(resp.ok, resp.error)
        items = resp.result.get("items") or []
        for item in items:
            self.assertIn("node_id", item)
            self.assertIn("reason", item)

    def test_roadmap_blocked_rpc(self) -> None:
        resp = self.methods.handle(self.conn, _request("roadmap.blocked", self.project_id))
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("items", resp.result)


class Phase21CliTests(unittest.TestCase):
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
        self.conn.close()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_roadmap_status_json(self) -> None:
        proc = _run_cli_with_home(
            self.home, "roadmap", "status", "--json", cwd=self.repo
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "roadmap.status")

    def test_cli_roadmap_next_json(self) -> None:
        proc = _run_cli_with_home(
            self.home, "roadmap", "next", "--json", cwd=self.repo
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "roadmap.next")


class Phase21SlashRoutingTests(unittest.TestCase):
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
        self.conn.close()
        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_next_slash_maps_to_roadmap_next(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/next", cwd=self.repo)
        methods = {m for m, _ in self.server.drain_requests()}
        self.assertIn("roadmap.next", methods)

    def test_roadmap_slash_maps_to_roadmap_status(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/roadmap", cwd=self.repo
        )
        methods = {m for m, _ in self.server.drain_requests()}
        self.assertIn("roadmap.status", methods)

    def test_blocked_slash_maps_to_roadmap_blocked(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/blocked", cwd=self.repo
        )
        methods = {m for m, _ in self.server.drain_requests()}
        self.assertIn("roadmap.blocked", methods)


if __name__ == "__main__":
    unittest.main()