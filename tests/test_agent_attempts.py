"""Tests for attempt recording in the agent runner."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.agent_result import AgentResultClass
from local_cli_coordinator.db import (
    connect,
    init_db,
    create_task,
    get_task,
    list_attempts,
    list_task_artifacts,
)


class AttemptRecordingTest(TestCase):
    """Verify the engine records attempts per worker invocation."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_attempt_creates_record(self) -> None:
        """A single worker invocation should create one attempt record."""
        from local_cli_coordinator.db import start_attempt, finish_attempt

        task_id = create_task(
            self.conn,
            title="test task",
            repo="repo",
            source_path="inbox/task.md",
            priority="normal",
            capabilities=["code"],
            goal="do something",
            acceptance_criteria=["it works"],
            verification_commands=["echo ok"],
        )
        attempt_id = start_attempt(
            self.conn, task_id, "claude", "claude --print ..."
        )
        finish_attempt(
            self.conn,
            attempt_id,
            exit_code=0,
            result_class="completed",
            result_reason="ok",
            log_path=str(self.root / "runs" / task_id / "attempt-1" / "agent.log"),
        )
        attempts = list_attempts(self.conn, task_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["agent_id"], "claude")
        self.assertEqual(attempts[0]["result_class"], "completed")

    def test_per_attempt_log_paths(self) -> None:
        """Each attempt should have a distinct log path."""
        from local_cli_coordinator.db import start_attempt, finish_attempt

        task_id = create_task(
            self.conn,
            title="test",
            repo="repo",
            source_path="x",
            priority="normal",
            capabilities=["code"],
            goal="x",
            acceptance_criteria=["x"],
            verification_commands=["x"],
        )
        a1 = start_attempt(self.conn, task_id, "claude", "cmd1")
        log1 = self.root / "runs" / task_id / "attempt-1" / "agent.log"
        finish_attempt(self.conn, a1, exit_code=0, result_class="completed", log_path=str(log1))

        a2 = start_attempt(
            self.conn, task_id, "grok", "cmd2", fallback_from_attempt_id=a1
        )
        log2 = self.root / "runs" / task_id / "attempt-2" / "agent.log"
        finish_attempt(self.conn, a2, exit_code=0, result_class="completed", log_path=str(log2))

        attempts = list_attempts(self.conn, task_id)
        self.assertNotEqual(attempts[0]["log_path"], attempts[1]["log_path"])
        self.assertIn("attempt-1", attempts[0]["log_path"])
        self.assertIn("attempt-2", attempts[1]["log_path"])

    def test_attempt_log_added_as_artifact(self) -> None:
        """Each attempt log should be recorded as an artifact."""
        from local_cli_coordinator.db import start_attempt, finish_attempt, add_artifact

        task_id = create_task(
            self.conn,
            title="test",
            repo="repo",
            source_path="x",
            priority="normal",
            capabilities=["code"],
            goal="x",
            acceptance_criteria=["x"],
            verification_commands=["x"],
        )
        attempt_id = start_attempt(self.conn, task_id, "claude", "cmd")
        log_path = self.root / "runs" / task_id / "attempt-1" / "agent.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("output")
        finish_attempt(
            self.conn, attempt_id, exit_code=0, result_class="completed",
            log_path=str(log_path),
        )
        add_artifact(self.conn, task_id, "attempt_log", log_path)
        add_artifact(self.conn, task_id, "agent_log", log_path)

        artifacts = list_task_artifacts(self.conn, task_id)
        kinds = {a["kind"] for a in artifacts}
        self.assertIn("attempt_log", kinds)
        self.assertIn("agent_log", kinds)  # compatibility pointer

    def test_fallback_lineage_recorded(self) -> None:
        """Fallback attempts should reference the parent attempt."""
        from local_cli_coordinator.db import start_attempt, finish_attempt

        task_id = create_task(
            self.conn,
            title="test",
            repo="repo",
            source_path="x",
            priority="normal",
            capabilities=["code"],
            goal="x",
            acceptance_criteria=["x"],
            verification_commands=["x"],
        )
        a1 = start_attempt(self.conn, task_id, "claude", "cmd1")
        finish_attempt(self.conn, a1, exit_code=0, result_class="interactive_blocked")
        a2 = start_attempt(
            self.conn, task_id, "grok", "cmd2", fallback_from_attempt_id=a1
        )
        finish_attempt(self.conn, a2, exit_code=0, result_class="completed")
        attempts = list_attempts(self.conn, task_id)
        self.assertIsNone(attempts[0]["fallback_from_attempt_id"])
        self.assertEqual(attempts[1]["fallback_from_attempt_id"], a1)
