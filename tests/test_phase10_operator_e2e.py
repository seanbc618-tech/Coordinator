"""Phase 10 operator control tower E2E tests."""

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
        request_id="req-phase10",
        project_id=project_id,
        method=method,
        params=params,
    )


class OperatorRpcTests(unittest.TestCase):
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
        from local_cli_coordinator.db import transition_task

        task_id = create_task(
            self.conn,
            title="Awaiting",
            repo="test-repo",
            source_path="t.md",
            priority="normal",
            capabilities=["code"],
            goal="g",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        transition_task(self.conn, task_id, "awaiting_human", "needs review")
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)
        self.methods = SupervisorMethods(config=self.config, broker=EventBroker())

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_operator_inbox_rpc(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("operator.inbox", self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result.get("project_id"), self.project_id)

    def test_operator_decision_returns_rpc_not_mutation(self) -> None:
        from local_cli_coordinator.operator_inbox import (
            list_operator_items,
            refresh_operator_inbox,
        )

        refresh_operator_inbox(
            self.conn,
            project_id=self.project_id,
            config=self.config,
            repo_root=self.repo,
        )
        self.conn.commit()
        items = list_operator_items(self.conn, project_id=self.project_id)
        human = next(
            (item for item in items if item.action_method == "project.task.approve"),
            None,
        )
        self.assertIsNotNone(human)
        assert human is not None
        resp = self.methods.handle(
            self.conn,
            _request(
                "operator.decision",
                self.project_id,
                item_id=human.id,
                dry_run=True,
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result.get("routed_method"), "project.task.approve")
        self.assertTrue(resp.result.get("dry_run"))


class Phase10SlashRoutingTests(unittest.TestCase):
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

    def test_inbox_slash_maps_to_operator_inbox(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/inbox", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.inbox", methods)

    def test_attention_slash_maps_to_operator_attention(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/attention", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.attention", methods)

    def test_summary_slash_maps_to_operator_summary(self) -> None:
        _run_cli_with_home(
            self.home, "--mode", "rpc", "-p", "/summary", cwd=self.repo
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("operator.summary", methods)


if __name__ == "__main__":
    unittest.main()