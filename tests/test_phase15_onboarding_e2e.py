"""Phase 15 E2E: onboarding CLI, RPCs, and slash routing.

Owner: Grok (Phase 15 Task 0)
Expected before implementation: onboard/fleet commands and Phase 15 RPCs missing.
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
        request_id="req-phase15-e2e",
        project_id=project_id,
        method=method,
        params=params,
    )


class Phase15CliTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_inspect_json_includes_profile(self) -> None:
        result = _run_cli_with_home(
            self.home,
            "project",
            "inspect",
            str(self.repo),
            "--json",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout.strip())
        self.assertTrue(envelope["ok"], envelope)
        payload = envelope["result"]
        self.assertIn("detected_profile", payload)
        self.assertEqual(payload["recommended_preset"], "observe")
        self.assertIn("verify_commands", payload)

    def test_onboard_dry_run_json_writes_no_config(self) -> None:
        repos_before = (self.paths.config_dir / "repos.toml").read_text()
        result = _run_cli_with_home(
            self.home,
            "onboard",
            str(self.repo),
            "--dry-run",
            "--json",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout.strip())
        self.assertTrue(envelope["ok"], envelope)
        self.assertIn("plan", envelope["result"])
        self.assertEqual(
            (self.paths.config_dir / "repos.toml").read_text(),
            repos_before,
        )

    def test_onboard_apply_observe_snapshots_and_registers(self) -> None:
        result = _run_cli_with_home(
            self.home,
            "onboard",
            str(self.repo),
            "--apply",
            "--preset",
            "observe",
            "--json",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout.strip())
        self.assertTrue(envelope["ok"], envelope)
        self.assertIn("snapshot_id", envelope["result"])
        self.assertFalse(envelope["result"]["autonomy_enabled"])

    def test_fleet_scan_json_lists_discovered_repos(self) -> None:
        fleet_root = self.tmp / "fleet"
        fleet_root.mkdir()
        repo_a = fleet_root / "alpha"
        init_git_repo(repo_a)
        (repo_a / "pyproject.toml").write_text("[project]\nname='alpha'\n")
        result = _run_cli_with_home(
            self.home,
            "fleet",
            "scan",
            str(fleet_root),
            "--json",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout.strip())
        self.assertTrue(envelope["ok"], envelope)
        repo_ids = {item["repo_id"] for item in envelope["result"]["repos"]}
        self.assertIn("alpha", repo_ids)


class Phase15RpcTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_onboard_plan_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "project.onboard.plan",
                self.project_id,
                path=str(self.repo),
                preset="observe",
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result.get("preset"), "observe")
        self.assertIn("config_diff", resp.result)

    def test_project_onboard_apply_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "project.onboard.apply",
                self.project_id,
                path=str(self.repo),
                preset="observe",
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("snapshot_id", resp.result)
        self.assertFalse(resp.result.get("autonomy_enabled"))

    def test_fleet_scan_rpc(self) -> None:
        fleet_root = self.tmp / "fleet"
        fleet_root.mkdir()
        repo_a = fleet_root / "alpha"
        init_git_repo(repo_a)
        (repo_a / "package.json").write_text('{"name":"alpha"}')
        resp = self.methods.handle(
            self.conn,
            _request("fleet.scan", self.project_id, root=str(fleet_root)),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("repos", resp.result)


class Phase15SlashRoutingTests(unittest.TestCase):
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

    def test_profile_slash_maps_to_project_profile(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/profile", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.profile", methods)

    def test_onboard_slash_maps_to_onboard_plan(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/onboard", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.onboard.plan", methods)

    def test_simulate_slash_does_not_apply_preset(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/simulate overnight",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.onboard.simulate", methods)
        self.assertNotIn("project.onboard.apply", methods)

    def test_fleet_slash_maps_to_fleet_scan(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            f"/fleet {self.tmp}",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("fleet.scan", methods)

    def test_rollback_onboard_slash_requires_snapshot_id(self) -> None:
        result = _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/rollback-onboard snap-123",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("project.onboard.rollback", methods)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()