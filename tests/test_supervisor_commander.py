"""Supervisor bridge: structured Commander chat results and broker events."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.commander_policy import CommanderAdmissionResult
from local_cli_coordinator.commander_protocol import CommanderResponse
from local_cli_coordinator.commander_runner import CommanderRunResult
from local_cli_coordinator.commander_service import (
    CommanderChatResult,
    send_project_chat_message,
)
from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import (
    create_goal,
    finish_commander_run,
    start_commander_run,
    transition_goal,
)
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import RequestEnvelope

_PYTHON = sys.executable

# Admission-language tokens that must never appear in visible user replies.
_ADMISSION_LEAK_TOKENS = [
    "duplicate title",
    "linked task",
    "admission",
    "no duplicate",
]


def _request(method: str, project_id: str, **params) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=1,
        request_id="req-1",
        project_id=project_id,
        method=method,
        params=params,
    )


def _write_commander_fixture(tmp_dir: Path) -> str:
    script = tmp_dir / "fixture_commander.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            response = {
                "schema_version": 2,
                "intent": "task_request",
                "user_reply": "好的，我来添加 helper 模块。",
                "goal_status": "active",
                "progress_summary": "Slice ready",
                "tasks": [{
                    "title": "Add helper",
                    "repo": "demo",
                    "capabilities": ["code"],
                    "goal": "Add helper module",
                    "acceptance_criteria": ["helper exists"],
                    "verification_commands": [],
                    "expected_files": 1,
                    "expected_minutes": 10,
                    "parent_task_id": None,
                    "rationale": "First slice",
                }],
                "stop_reason": None,
            }
            print(json.dumps(response))
            """
        ).strip()
    )
    return f"{_PYTHON} {script}"


def _write_conversation_fixture(tmp_dir: Path, summary: str = "Greeting acknowledged") -> str:
    """Commander fixture that returns zero tasks (pure conversation)."""
    script = tmp_dir / "fixture_conversation.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            response = {{
                "schema_version": 2,
                "intent": "conversation",
                "user_reply": "你好！有什么可以帮你的吗？",
                "goal_status": "active",
                "progress_summary": {json.dumps(summary)},
                "tasks": [],
                "stop_reason": None,
            }}
            print(json.dumps(response))
            """
        ).strip()
    )
    return f"{_PYTHON} {script}"


def _test_config(repo: Path, commander_command: str) -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "commander": AgentConfig(
                id="commander",
                command=commander_command,
                capabilities=["code", "tests"],
                max_concurrency=1,
                role="commander",
            ),
            "worker": AgentConfig(
                id="worker",
                command="true",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
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


class SendProjectChatMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = _git_repo(self.root)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)
        self.config = _test_config(self.repo, _write_commander_fixture(self.root))

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_send_project_chat_message_returns_admission(self) -> None:
        goal_id = create_goal(
            self.conn,
            "Roadmap",
            "Finish roadmap",
            project_id="proj-a",
            repo_ids=["demo"],
        )
        transition_goal(self.conn, goal_id, "active")

        result = send_project_chat_message(
            self.conn,
            self.config,
            self.repo,
            goal_id,
            "plan next slice",
            project_id="proj-a",
        )

        self.assertIsInstance(result, CommanderChatResult)
        self.assertTrue(result.succeeded)
        self.assertIsNotNone(result.admission)
        self.assertIsInstance(result.admission, CommanderAdmissionResult)
        self.assertGreater(len(result.admission.accepted_task_ids), 0)


class ChatSendBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = _git_repo(self.root)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)
        self.broker = EventBroker()
        self.config = _test_config(self.repo, _write_commander_fixture(self.root))
        self.methods = SupervisorMethods(broker=self.broker, config=self.config)
        draft = inspect_project(self.repo)
        self.project_id = register_project(self.conn, draft, confirmed=True)
        self.goal_id = create_goal(
            self.conn,
            "Roadmap",
            "Finish roadmap",
            project_id=self.project_id,
            repo_ids=["demo"],
        )
        run_id = start_commander_run(
            self.conn, self.goal_id, "initial_plan", 1, Path("/tmp/prompt.md")
        )
        finish_commander_run(self.conn, run_id, status="succeeded")
        transition_goal(self.conn, self.goal_id, "active")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_chat_send_publishes_task_created_and_commander_completed(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("chat.send", self.project_id, text="hello commander"),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertNotIn("Received:", json.dumps(resp.result or {}))

        events = self.broker.replay(self.conn, self.project_id)
        types = [e.event_type for e in events]
        self.assertIn("commander.completed", types)
        self.assertIn("task.created", types)
        self.assertFalse(any("Received:" in str(e.payload) for e in events))

        created = [e for e in events if e.event_type == "task.created"]
        self.assertTrue(created)
        payload = created[0].payload
        self.assertIn("title", payload)
        self.assertIn("state", payload)
        self.assertIn("goal", payload)
        self.assertIn("acceptance_criteria", payload)
        self.assertIn("verification_commands", payload)
        self.assertIsInstance(payload["verification_commands"], list)

        coord = [
            e
            for e in events
            if e.event_type == "chat.message"
            and e.payload.get("role") == "coordinator"
        ]
        self.assertTrue(coord)

        msgs = self.conn.execute(
            "select role from commander_messages where goal_id = ?",
            (self.goal_id,),
        ).fetchall()
        self.assertIn("assistant", {m["role"] for m in msgs})


class ConversationRegressionTests(unittest.TestCase):
    """Greetings and questions must create zero tasks; admission internals
    must never leak into visible Commander text."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = _git_repo(self.root)
        self.conn = connect(self.root / "coordinator.db")
        init_db(self.conn)
        self.broker = EventBroker()
        # Use conversation fixture (no tasks) by default.
        self.config = _test_config(
            self.repo,
            _write_conversation_fixture(self.root),
        )
        self.methods = SupervisorMethods(broker=self.broker, config=self.config)
        draft = inspect_project(self.repo)
        self.project_id = register_project(self.conn, draft, confirmed=True)
        self.goal_id = create_goal(
            self.conn,
            "Roadmap",
            "Finish roadmap",
            project_id=self.project_id,
            repo_ids=["demo"],
        )
        run_id = start_commander_run(
            self.conn, self.goal_id, "initial_plan", 1, Path("/tmp/prompt.md")
        )
        finish_commander_run(self.conn, run_id, status="succeeded")
        transition_goal(self.conn, self.goal_id, "active")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _tasks_created(self) -> list[dict]:
        rows = self.conn.execute("select * from tasks").fetchall()
        return [dict(r) for r in rows]

    def _coordinator_messages(self) -> list[str]:
        events = self.broker.replay(self.conn, self.project_id)
        return [
            e.payload.get("text", "")
            for e in events
            if e.event_type == "chat.message"
            and e.payload.get("role") == "coordinator"
        ]

    # --- Task 0 assertions: greetings create zero tasks ---

    def test_greeting_ni_hao_creates_zero_tasks(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("chat.send", self.project_id, text="你好"),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(self._tasks_created(), [])

    def test_greeting_question_marks_creates_zero_tasks(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("chat.send", self.project_id, text="？？？"),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(self._tasks_created(), [])

    def test_greeting_how_to_start_creates_zero_tasks(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("chat.send", self.project_id, text="如何启动？"),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertEqual(self._tasks_created(), [])

    # --- Explicit task request CAN create work ---

    def test_explicit_task_request_can_create_work(self) -> None:
        task_config = _test_config(
            self.repo,
            _write_commander_fixture(self.root),
        )
        task_methods = SupervisorMethods(broker=self.broker, config=task_config)
        resp = task_methods.handle(
            self.conn,
            _request(
                "chat.send",
                self.project_id,
                text="创建一个只读任务，运行 uv run ruff check src/ tests/ 并报告结果。",
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        self.assertGreater(len(self._tasks_created()), 0)

    # --- Admission language must not leak into visible text ---

    def test_visible_text_hides_admission_language(self) -> None:
        resp = self.methods.handle(
            self.conn,
            _request("chat.send", self.project_id, text="你好"),
        )
        self.assertTrue(resp.ok, resp.error)
        for msg in self._coordinator_messages():
            for token in _ADMISSION_LEAK_TOKENS:
                self.assertNotIn(
                    token,
                    msg.lower(),
                    f"admission token {token!r} leaked into visible text: {msg}",
                )

    def test_task_request_includes_human_outcome_details(self) -> None:
        task_config = _test_config(
            self.repo,
            _write_commander_fixture(self.root),
        )
        task_methods = SupervisorMethods(broker=self.broker, config=task_config)
        resp = task_methods.handle(
            self.conn,
            _request(
                "chat.send",
                self.project_id,
                text="创建一个只读任务，运行 uv run ruff check src/ tests/ 并报告结果。",
            ),
        )
        self.assertTrue(resp.ok, resp.error)
        combined = "\n".join(self._coordinator_messages())
        self.assertIn("已创建 1 个任务", combined)
        self.assertIn("Add helper", combined)
        self.assertNotIn("|", combined)

        events = self.broker.replay(self.conn, self.project_id)
        created = [e for e in events if e.event_type == "task.created"]
        self.assertTrue(created)
        self.assertEqual(created[-1].payload.get("agent"), "worker")

    def test_duplicate_proposal_shows_operator_language(self) -> None:
        task_config = _test_config(
            self.repo,
            _write_commander_fixture(self.root),
        )
        task_methods = SupervisorMethods(broker=self.broker, config=task_config)
        first = task_methods.handle(
            self.conn,
            _request("chat.send", self.project_id, text="请创建 helper 任务"),
        )
        self.assertTrue(first.ok, first.error)
        existing = self._tasks_created()[0]

        second = task_methods.handle(
            self.conn,
            _request("chat.send", self.project_id, text="再创建一次 helper 任务"),
        )
        self.assertTrue(second.ok, second.error)
        combined = "\n".join(self._coordinator_messages())
        self.assertIn("没有创建重复任务", combined)
        self.assertIn(existing["id"], combined)
        self.assertNotIn("duplicate title", combined.lower())

        events = self.broker.replay(self.conn, self.project_id)
        diagnostics = [
            e for e in events if e.event_type == "commander.completed"
        ]
        self.assertTrue(any(e.payload.get("rejection_reasons") for e in diagnostics))


if __name__ == "__main__":
    unittest.main()