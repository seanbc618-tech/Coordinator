"""Phase 13 E2E: approval RPCs, CLI callbacks, and slash routing.

Owner: Grok (Phase 13 Task 0)
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
from local_cli_coordinator.db import connect, create_task, init_db, transition_task
from local_cli_coordinator.operator_inbox import upsert_operator_item
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
        request_id="req-phase13",
        project_id=project_id,
        method=method,
        params=params,
    )


class Phase13RpcTests(unittest.TestCase):
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
            title="External approval task",
            repo="test-repo",
            source_path="ext.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        transition_task(self.conn, self.task_id, "awaiting_human", "needs review")
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(
            config=self.config,
            broker=EventBroker(),
            paths=self.paths,
        )
        self.item = upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="task",
            source_id=self.task_id,
            severity="warning",
            title="Approve externally",
            dedupe_key=f"ext-approve:{self.task_id}",
            action_label="Approve",
            action_method="project.task.approve",
            action_params={"task_id": self.task_id},
            commit=True,
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_operator_approvals_rpc_lists_pending(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "operator.approval.create",
                self.project_id,
                operator_item_id=self.item.id,
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        listed = self.methods.handle(
            self.conn, _request("operator.approvals", self.project_id)
        )
        self.assertTrue(listed.ok, listed.error)
        self.assertGreaterEqual(len(listed.result.get("requests") or []), 1)

    def test_operator_channels_rpc_lists_configs(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("operator.channels", self.project_id)
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertIn("channels", resp.result)

    def test_operator_notify_rpc_uses_runtime_paths_state_dir(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "operator.notify",
                self.project_id,
                dry_run=True,
                args="test",
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertTrue(resp.result.get("dry_run"))
        self.assertIn("deliveries", resp.result)

    def test_operator_approval_approve_rpc_consumes_token(self) -> None:
        created = self.methods.handle(
            self.conn,
            _request(
                "operator.approval.create",
                self.project_id,
                operator_item_id=self.item.id,
            ),
        )
        self.assertTrue(created.ok, created.error)
        token = created.result.get("token")
        self.assertIsInstance(token, str)
        approved = self.methods.handle(
            self.conn,
            _request(
                "operator.approval.approve",
                self.project_id,
                token=token,
                confirmed=True,
            ),
        )
        self.assertTrue(approved.ok, approved.error)
        self.assertEqual(approved.result.get("status"), "consumed")

    def test_operator_approval_reject_rpc(self) -> None:
        created = self.methods.handle(
            self.conn,
            _request(
                "operator.approval.create",
                self.project_id,
                operator_item_id=self.item.id,
            ),
        )
        token = created.result.get("token")
        rejected = self.methods.handle(
            self.conn,
            _request(
                "operator.approval.reject",
                self.project_id,
                token=token,
            ),
        )
        self.assertTrue(rejected.ok, rejected.error)
        self.assertEqual(rejected.result.get("status"), "rejected")


class Phase13SlashRoutingTests(unittest.TestCase):
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

    def test_approvals_slash_maps_to_operator_approvals(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/approvals", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.approvals", methods)

    def test_channels_slash_maps_to_operator_channels(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/channels", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.channels", methods)

    def test_approve_token_slash_maps_to_operator_approval_approve(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/approve token coord-appr-test-token-placeholder",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.approval.approve", methods)

    def test_notify_test_slash_maps_to_operator_notify(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/notify test", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.notify", methods)


class Phase13CliCallbackTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_approve_subcommand_exists(self) -> None:
        proc = _run_cli_with_home(self.home, "approve", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("approve", proc.stdout.lower())

    def test_cli_reject_subcommand_exists(self) -> None:
        proc = _run_cli_with_home(self.home, "reject", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("reject", proc.stdout.lower())


if __name__ == "__main__":
    unittest.main()