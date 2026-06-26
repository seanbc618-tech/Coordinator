"""Concurrency contract for synchronous chat.send RPC."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.commander_runner import CommanderResponse, CommanderRunResult
from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.goals import (
    create_goal,
    finish_commander_run,
    start_commander_run,
    transition_goal,
)
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import (
    PROTOCOL_VERSION,
    RequestEnvelope,
)
from local_cli_coordinator.supervisor_server import SupervisorServer, send_request

_PYTHON = sys.executable


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


def _commander_noop_command(tmp: Path) -> str:
    script = tmp / "noop_commander.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            print(json.dumps({
                "schema_version": 2,
                "intent": "conversation",
                "user_reply": "Got it — no new tasks for now.",
                "goal_status": "active",
                "progress_summary": "ok",
                "tasks": [],
                "stop_reason": None,
            }))
            """
        ).strip()
    )
    return f"{_PYTHON} {script}"


def _config(repo: Path, tmp: Path) -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "commander": AgentConfig(
                id="commander",
                command=_commander_noop_command(tmp),
                capabilities=["code"],
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


class _Paths:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

    @property
    def socket(self) -> Path:
        return self.state_dir / "coordinator.sock"

    @property
    def lock(self) -> Path:
        return self.state_dir / "supervisor.lock"


def _chat_request(project_id: str, text: str = "hi") -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id="chat-1",
        project_id=project_id,
        method="chat.send",
        params={"text": text},
    )


def _status_request(project_id: str) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id="status-1",
        project_id=project_id,
        method="project.status",
        params={},
    )


def _ping_request() -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id="ping-1",
        project_id=None,
        method="system.ping",
        params={},
    )


class CommanderChatConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = _git_repo(self.root)
        self.db_path = self.root / "coordinator.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.broker = EventBroker()
        self.config = _config(self.repo, self.root)
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
        create_task(
            self.conn,
            title="seed",
            repo="demo",
            source_path="x",
            priority="normal",
            capabilities=["code"],
            goal="g",
            acceptance_criteria=["a"],
            verification_commands=[],
            project_id=self.project_id,
        )

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_chat_send_busy_returns_immediately_when_commander_active(self) -> None:
        start_commander_run(
            self.conn, self.goal_id, "chat", 1, Path("/tmp/running.md")
        )
        started = time.time()
        resp = self.methods.handle(
            self.conn,
            _chat_request(self.project_id, "blocked?"),
        )
        elapsed = time.time() - started

        self.assertFalse(resp.ok)
        self.assertLess(elapsed, 1.0)
        events = self.broker.replay(self.conn, self.project_id)
        thinking = [
            e
            for e in events
            if e.event_type == "chat.message"
            and e.payload.get("role") == "system"
            and "thinking" in e.payload.get("text", "").lower()
        ]
        self.assertEqual(thinking, [])

    def test_project_status_responds_while_chat_commander_blocks(self) -> None:
        response = CommanderResponse(
            schema_version=2,
            intent="conversation",
            user_reply="Working on your message.",
            goal_status="active",
            progress_summary="slow",
            tasks=[],
            stop_reason=None,
        )

        def slow_commander(*args, **kwargs):
            time.sleep(10)
            return CommanderRunResult(
                succeeded=True,
                response=response,
                run_id=99,
                prompt_path=Path("/tmp/p.md"),
                raw_output_path=Path("/tmp/raw.txt"),
                parsed_output_path=Path("/tmp/parsed.json"),
                exit_code=0,
                timed_out=False,
                error=None,
            )

        status_result: dict = {}
        status_error: list[Exception] = []

        def status_worker() -> None:
            try:
                conn = connect(self.db_path)
                started = time.time()
                resp = self.methods.handle(conn, _status_request(self.project_id))
                status_result["elapsed"] = time.time() - started
                status_result["ok"] = resp.ok
                conn.close()
            except Exception as exc:
                status_error.append(exc)

        def chat_worker() -> None:
            conn = connect(self.db_path)
            try:
                self.methods.handle(
                    conn,
                    _chat_request(self.project_id, "slow path"),
                )
            finally:
                conn.close()

        with patch(
            "local_cli_coordinator.commander_service.run_commander",
            side_effect=slow_commander,
        ):
            chat_thread = threading.Thread(target=chat_worker, daemon=True)
            chat_thread.start()
            time.sleep(0.2)
            status_thread = threading.Thread(target=status_worker, daemon=True)
            status_thread.start()
            status_thread.join(timeout=5.0)
            chat_thread.join(timeout=15.0)

        self.assertFalse(status_error, status_error)
        self.assertTrue(status_result.get("ok"))
        self.assertLess(status_result.get("elapsed", 99), 5.0)

    def test_chat_timeout_does_not_kill_supervisor_ping(self) -> None:
        paths = _Paths(self.root / "state")
        paths.state_dir.mkdir(parents=True, exist_ok=True)

        def timeout_commander(*args, **kwargs):
            return CommanderRunResult(
                succeeded=False,
                response=None,
                run_id=42,
                prompt_path=Path("/tmp/p.md"),
                raw_output_path=Path("/tmp/raw.txt"),
                parsed_output_path=None,
                exit_code=None,
                timed_out=True,
                error="timed out",
            )

        def handler(request: RequestEnvelope):
            conn = connect(self.db_path)
            try:
                if request.method == "system.ping":
                    from local_cli_coordinator.supervisor_protocol import ResponseEnvelope

                    return ResponseEnvelope(
                        protocol_version=PROTOCOL_VERSION,
                        request_id=request.request_id,
                        ok=True,
                        result={"pong": True},
                        error=None,
                    )
                return self.methods.handle(conn, request)
            finally:
                conn.close()

        server = SupervisorServer(paths, handler=handler, methods=self.methods)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        deadline = time.time() + 3.0
        while time.time() < deadline and not paths.socket.exists():
            time.sleep(0.05)

        with patch(
            "local_cli_coordinator.commander_service.run_commander",
            side_effect=timeout_commander,
        ):
            chat_resp = send_request(
                paths.socket,
                _chat_request(self.project_id, "timeout please"),
                timeout=5.0,
            )
            ping_resp = send_request(
                paths.socket,
                _ping_request(),
                timeout=2.0,
            )

        server.request_shutdown()
        thread.join(timeout=3.0)

        self.assertFalse(chat_resp.ok)
        self.assertTrue(ping_resp.ok)
        completed = [
            e
            for e in self.broker.replay(self.conn, self.project_id)
            if e.event_type == "commander.completed"
        ]
        self.assertTrue(completed)
        self.assertFalse(completed[-1].payload.get("succeeded", True))


if __name__ == "__main__":
    unittest.main()