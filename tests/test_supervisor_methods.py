"""Tests for multi-client Supervisor methods."""

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import (
    add_artifact,
    connect,
    create_task,
    finish_attempt,
    init_db,
    set_task_branch_and_worktree,
    start_attempt,
    transition_task,
)
from local_cli_coordinator.goals import (
    create_goal,
    finish_commander_run,
    start_commander_run,
    transition_goal,
)
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import RequestEnvelope

_PYTHON = sys.executable


def _request(method: str, project_id: str | None = None, **params) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=1,
        request_id="req-1",
        project_id=project_id,
        method=method,
        params=params,
    )


class SupervisorMethodsTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.methods = SupervisorMethods()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_project_status(self) -> None:
        wrap = Path(self.tmp.name) / "wrap"
        wrap.mkdir()
        repo = _git_repo(wrap)
        draft = inspect_project(repo)
        project_id = register_project(self.conn, draft, confirmed=True)
        create_task(
            self.conn, title="t", repo="r", source_path="x",
            priority="normal", capabilities=["code"], goal="g",
            acceptance_criteria=["a"], verification_commands=[],
            project_id=project_id,
        )
        resp = self.methods.handle(
            self.conn, _request("project.status", project_id=project_id)
        )
        self.assertTrue(resp.ok)
        self.assertIn("counts", resp.result)

    def test_project_status_unknown(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("project.status", project_id="nonexistent")
        )
        self.assertFalse(resp.ok)
        self.assertIn("not registered", resp.error)

    def test_chat_send_requires_registered_project(self) -> None:
        methods = SupervisorMethods(
            config=CoordinatorConfig(
                agents={},
                repos={},
                policy=PolicyConfig(
                    require_single_repo=False,
                    require_acceptance_criteria=False,
                    require_verification_commands=False,
                    require_handoff_summary=False,
                    max_files_touched=10,
                    max_expected_minutes=60,
                    max_attempts=3,
                    split_if_touches_multiple_subsystems=False,
                    split_if_research_and_code_are_mixed=False,
                ),
                daemon_policy=DaemonPolicyConfig(),
            )
        )
        resp = methods.handle(
            self.conn,
            _request("chat.send", project_id="proj-a", text="hello"),
        )
        self.assertFalse(resp.ok)
        self.assertIn("not registered", resp.error)

    def test_project_pause_resume(self) -> None:
        resp_pause = self.methods.handle(
            self.conn, _request("project.pause", project_id="proj-a")
        )
        self.assertTrue(resp_pause.ok)

        resp_resume = self.methods.handle(
            self.conn, _request("project.resume", project_id="proj-a")
        )
        self.assertTrue(resp_resume.ok)

    def test_project_stop(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("project.stop", project_id="proj-a")
        )
        self.assertTrue(resp.ok)

    def test_unknown_method(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("unknown.method", project_id="proj-a")
        )
        self.assertFalse(resp.ok)
        self.assertIn("unsupported", resp.error)

    def test_events_subscribe(self) -> None:
        resp = self.methods.handle(
            self.conn, _request("events.subscribe", project_id="proj-a")
        )
        self.assertTrue(resp.ok)
        self.assertIn("subscription_id", resp.result)

    def test_events_replay(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("events.replay", project_id="proj-a", after=0),
        )
        self.assertTrue(resp.ok)
        self.assertIn("events", resp.result)


def _git_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
    (repo / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        capture_output=True,
        check=True,
        env=env,
    )
    return repo


def _commander_fixture(tmp: Path) -> str:
    script = tmp / "commander.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            print(json.dumps({
                "schema_version": 2,
                "intent": "conversation",
                "user_reply": "Preview is ready — no tasks proposed yet.",
                "goal_status": "active",
                "progress_summary": "Preview ready",
                "tasks": [],
                "stop_reason": None,
            }))
            """
        ).strip()
    )
    return f"{_PYTHON} {script}"


def _slash_config(repo: Path, tmp: Path) -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "commander": AgentConfig(
                id="commander",
                command=_commander_fixture(tmp),
                capabilities=["code"],
                max_concurrency=1,
                role="commander",
            ),
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=repo,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[],
            ),
        },
        policy=PolicyConfig(
            require_single_repo=True,
            require_acceptance_criteria=True,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=10,
            max_expected_minutes=60,
            max_attempts=3,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
        ),
        daemon_policy=DaemonPolicyConfig(),
    )


class ProjectSlashMethodsTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = _git_repo(self.root)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)
        self.paths = RuntimePaths(
            config_dir=self.root / "config",
            data_dir=self.root / "data",
            state_dir=self.root / "state",
        )
        self.paths.state_dir.mkdir(parents=True)
        (self.paths.state_dir / "supervisor.log").write_text(
            "supervisor started\nchat wired\n"
        )
        self.config = _slash_config(self.repo, self.root)
        draft = inspect_project(self.repo)
        self.project_id = register_project(self.conn, draft, confirmed=True)
        self.methods = SupervisorMethods(
            config=self.config,
            paths=self.paths,
        )

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_status_for_registered_project_with_zero_tasks(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("project.status", project_id=self.project_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result["counts"], {})
        self.assertIsNone(resp.result["goal"])

    def test_chat_rejected_when_goal_draft(self) -> None:
        create_goal(
            self.conn,
            "Draft goal",
            "Objective",
            project_id=self.project_id,
            repo_ids=["demo"],
        )
        resp = self.methods.handle(
            self.conn,
            _request("chat.send", project_id=self.project_id, text="hello"),
        )
        self.assertFalse(resp.ok)
        self.assertIn("draft", resp.error.lower())

    def test_project_goal_status_when_empty(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("project.goal", project_id=self.project_id, args=""),
        )
        self.assertTrue(resp.ok)
        self.assertIsNone(resp.result["goal"])

    def test_project_goal_create_draft(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request(
                "project.goal",
                project_id=self.project_id,
                args="Ship feature X",
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result["status"], "draft")

    def test_project_tasks_lists_rows(self) -> None:
        create_task(
            self.conn,
            title="Slice one",
            repo="demo",
            source_path="tasks/generated/a.md",
            priority="normal",
            capabilities=["code"],
            goal="g",
            acceptance_criteria=["a"],
            verification_commands=[],
            project_id=self.project_id,
        )
        resp = self.methods.handle(
            self.conn,
            _request("project.tasks", project_id=self.project_id),
        )
        self.assertTrue(resp.ok)
        self.assertEqual(len(resp.result["tasks"]), 1)
        self.assertEqual(resp.result["tasks"][0]["title"], "Slice one")

    def test_project_task_returns_detail(self) -> None:
        task_id = create_task(
            self.conn,
            title="Run baseline acceptance checks",
            repo="demo",
            source_path="tasks/generated/baseline.md",
            priority="normal",
            capabilities=["tests"],
            goal="Run baseline checks",
            acceptance_criteria=["`uv run pytest -q` has been executed and the result is recorded."],
            verification_commands=["uv run pytest -q", "uv run ruff check src/ tests/"],
            project_id=self.project_id,
        )
        set_task_branch_and_worktree(
            self.conn,
            task_id,
            "coord/baseline",
            self.repo / "worktrees" / task_id,
        )
        transition_task(self.conn, task_id, "failed", "no changed files")
        attempt_id = start_attempt(self.conn, task_id, "claude_worker", "claude --print ...")
        log_path = self.root / "runs" / task_id / "agent.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("I need permission to read the prompt file.\n")
        finish_attempt(
            self.conn,
            attempt_id,
            exit_code=0,
            result_class="interactive_blocked",
            result_reason="permission required",
            log_path=str(log_path),
        )
        add_artifact(self.conn, task_id, "agent_log", log_path)

        resp = self.methods.handle(
            self.conn,
            _request("project.task", project_id=self.project_id, args=task_id),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(resp.result["task"]["id"], task_id)
        self.assertEqual(resp.result["task"]["goal"], "Run baseline checks")
        self.assertIn("uv run pytest -q", resp.result["task"]["verification_commands"])
        self.assertEqual(resp.result["latest_event"]["note"], "no changed files")
        self.assertTrue(
            any("agent.log" in art["path"] for art in resp.result["artifacts"]),
        )

    def test_project_task_rejects_foreign_task(self) -> None:
        (self.root / "other").mkdir(parents=True, exist_ok=True)
        other_repo = _git_repo(self.root / "other")
        other_draft = inspect_project(other_repo)
        other_project_id = register_project(self.conn, other_draft, confirmed=True)
        foreign_id = create_task(
            self.conn,
            title="Foreign",
            repo="demo",
            source_path="tasks/generated/foreign.md",
            priority="normal",
            capabilities=["code"],
            goal="g",
            acceptance_criteria=["a"],
            verification_commands=[],
            project_id=other_project_id,
        )
        resp = self.methods.handle(
            self.conn,
            _request("project.task", project_id=self.project_id, args=foreign_id),
        )
        self.assertFalse(resp.ok)
        self.assertIn("not found", resp.error)

    def test_project_tasks_includes_goal_and_latest_note(self) -> None:
        task_id = create_task(
            self.conn,
            title="Slice one",
            repo="demo",
            source_path="tasks/generated/a.md",
            priority="normal",
            capabilities=["code"],
            goal="Ship slice",
            acceptance_criteria=["a"],
            verification_commands=[],
            project_id=self.project_id,
        )
        transition_task(self.conn, task_id, "failed", "agent command failed")
        resp = self.methods.handle(
            self.conn,
            _request("project.tasks", project_id=self.project_id),
        )
        self.assertTrue(resp.ok)
        row = resp.result["tasks"][0]
        self.assertEqual(row["goal"], "Ship slice")
        self.assertEqual(row["latest_note"], "agent command failed")

    def test_project_logs_returns_tail_and_commander_run(self) -> None:
        goal_id = create_goal(
            self.conn,
            "Roadmap",
            "Finish roadmap",
            project_id=self.project_id,
            repo_ids=["demo"],
        )
        run_id = start_commander_run(
            self.conn, goal_id, "initial_plan", 1, Path("/tmp/prompt.md")
        )
        finish_commander_run(self.conn, run_id, status="succeeded")
        transition_goal(self.conn, goal_id, "active")

        resp = self.methods.handle(
            self.conn,
            _request("project.logs", project_id=self.project_id),
        )
        self.assertTrue(resp.ok)
        self.assertIn("supervisor started", resp.result["log_tail"])
        self.assertEqual(resp.result["commander_run"]["status"], "succeeded")
