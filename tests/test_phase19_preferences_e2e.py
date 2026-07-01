"""Phase 19 E2E: preference RPCs, slash commands, and routing hints."""

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
from local_cli_coordinator.preference_rules import create_rule
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
        request_id="req-phase19-e2e",
        project_id=project_id,
        method=method,
        params=params,
    )


class Phase19RpcTests(unittest.TestCase):
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

    def test_preference_list_rpc(self) -> None:
        create_rule(
            self.conn,
            scope="project",
            project_id=self.project_id,
            rule_type="agent_choice",
            rule={"preferred_agent_id": "beta"},
            status="suggested",
            commit=True,
        )
        resp = self.methods.handle(
            self.conn,
            _request("preference.list", self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        rules = resp.result.get("rules") or []
        self.assertEqual(len(rules), 1)

    def test_preference_approve_rpc(self) -> None:
        rule = create_rule(
            self.conn,
            scope="project",
            project_id=self.project_id,
            rule_type="task_style",
            rule={"prefer_small_tasks": True},
            commit=True,
        )
        resp = self.methods.handle(
            self.conn,
            _request("preference.approve", self.project_id, rule_id=rule.id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result["rule"]["status"], "active")

    def test_preference_reject_rpc(self) -> None:
        rule = create_rule(
            self.conn,
            scope="project",
            project_id=self.project_id,
            rule_type="task_style",
            rule={"reject_vague_tasks": True},
            commit=True,
        )
        resp = self.methods.handle(
            self.conn,
            _request("preference.reject", self.project_id, rule_id=rule.id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result["rule"]["status"], "rejected")

    def test_preference_delete_rpc(self) -> None:
        rule = create_rule(
            self.conn,
            scope="project",
            project_id=self.project_id,
            rule_type="review_preference",
            rule={"prefer_full_review": True},
            status="active",
            commit=True,
        )
        resp = self.methods.handle(
            self.conn,
            _request("preference.delete", self.project_id, rule_id=rule.id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result["rule"]["status"], "deleted")

    def test_preference_permission_escalation_blocked(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "preference.approve",
                self.project_id,
                rule_type="risk_preference",
                rule={"allow_push": True},
            ),
        )
        self.assertFalse(resp.ok)
        self.assertIn("permission", resp.error or "")

    def test_approved_preference_affects_route_preview(self) -> None:
        create_rule(
            self.conn,
            scope="project",
            project_id=self.project_id,
            rule_type="agent_choice",
            rule={"preferred_agent_id": "beta", "score_bonus": 40.0},
            status="active",
            commit=True,
        )
        task_id = create_task(
            self.conn,
            title="Route with preference",
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
        resp = self.methods.handle(
            self.conn,
            _request("agent.route.preview", self.project_id, task_id=task_id),
        )
        self.assertTrue(resp.ok, resp.error)
        candidates = resp.result.get("candidates") or []
        beta = next(item for item in candidates if item["agent_id"] == "beta")
        self.assertIn("rule prefrule-", beta["reason"])


class Phase19SlashRoutingTests(unittest.TestCase):
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

    def test_preferences_slash_maps_to_preference_list(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/preferences", cwd=self.repo)
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("preference.list", methods)

    def test_learned_slash_maps_to_learned_only_list(self) -> None:
        _run_cli_with_home(self.home, "--mode", "rpc", "-p", "/learned", cwd=self.repo)
        requests = self.server.drain_requests()
        methods = [m for m, _ in requests]
        self.assertIn("preference.list", methods)
        for method, params in requests:
            if method == "preference.list":
                self.assertTrue(params.get("learned_only"))
                return
        self.fail("preference.list not observed with learned_only")

    def test_forget_slash_maps_to_preference_delete(self) -> None:
        _run_cli_with_home(
            self.home,
            "--mode",
            "rpc",
            "-p",
            "/forget prefrule-demo123",
            cwd=self.repo,
        )
        methods = [m for m, _ in self.server.drain_requests()]
        self.assertIn("preference.delete", methods)


if __name__ == "__main__":
    unittest.main()