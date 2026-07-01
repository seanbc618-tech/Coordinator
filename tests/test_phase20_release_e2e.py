"""Phase 20 E2E: backup/restore CLI, release RPCs, and slash routing."""

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
        request_id="req-phase20-e2e",
        project_id=project_id,
        method=method,
        params=params,
    )


class Phase20RpcTests(unittest.TestCase):
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

    def test_release_backup_create_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("release.backup.create", self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("backup_id", resp.result)

    def test_release_upgrade_preflight_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("release.upgrade_preflight", self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn(resp.result["status"], {"pass", "warn", "fail"})

    def test_release_extensions_list_rpc(self) -> None:
        ext_dir = self.paths.extensions_dir
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "demo.json").write_text(
            json.dumps(
                {
                    "name": "demo-ext",
                    "version": "1.0.0",
                    "slash_commands": [
                        {"name": "/demo", "description": "Demo"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        resp = self.methods.handle(
            self.conn,
            _request("release.extensions.list", self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        extensions = resp.result.get("extensions") or resp.result.get("enabled") or []
        self.assertEqual(len(extensions), 1)

    def test_release_check_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("release.check", self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("checks", resp.result)


class Phase20CliTests(unittest.TestCase):
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
        conn = connect(self.paths.database)
        init_db(conn)
        conn.close()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_backup_create_and_verify_json(self) -> None:
        created = _run_cli_with_home(self.home, "backup", "create", "--json")
        self.assertEqual(created.returncode, 0, created.stderr)
        payload = json.loads(created.stdout)
        self.assertTrue(payload["ok"])
        verified = _run_cli_with_home(
            self.home, "backup", "verify", "--latest", "--json"
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        verify_payload = json.loads(verified.stdout)
        self.assertTrue(verify_payload["ok"])

    def test_cli_restore_defaults_to_dry_run(self) -> None:
        created = _run_cli_with_home(self.home, "backup", "create", "--json")
        backup_id = json.loads(created.stdout)["data"]["backup_id"]
        backup_path = self.home / "data" / "backups" / backup_id
        self.paths.database.write_text("mutated", encoding="utf-8")
        restored = _run_cli_with_home(
            self.home,
            "restore",
            str(backup_path),
            "--json",
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        payload = json.loads(restored.stdout)
        self.assertEqual(payload["data"]["mode"], "dry_run")
        self.assertEqual(self.paths.database.read_text(encoding="utf-8"), "mutated")

    def test_cli_upgrade_preflight_json(self) -> None:
        proc = _run_cli_with_home(self.home, "upgrade", "preflight", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["command"], "upgrade.preflight")


class Phase20SlashRoutingTests(unittest.TestCase):
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
        conn = connect(self.paths.database)
        init_db(conn)
        register_project(conn, inspect_project(self.repo), confirmed=True)
        conn.commit()
        conn.close()
        self._orig_home = os.environ.get("COORDINATOR_HOME")
        os.environ["COORDINATOR_HOME"] = str(self.home)
        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        if self._orig_home is not None:
            os.environ["COORDINATOR_HOME"] = self._orig_home
        else:
            os.environ.pop("COORDINATOR_HOME", None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_slash_maps_to_release_backup_create(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/backup", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("release.backup.create", methods)

    def test_upgrade_check_slash_maps_to_preflight(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/upgrade-check", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("release.upgrade_preflight", methods)

    def test_extensions_slash_maps_to_release_extensions_list(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/extensions", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("release.extensions.list", methods)

    def test_release_check_slash_maps_to_release_check(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/release-check", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("release.check", methods)


if __name__ == "__main__":
    unittest.main()