"""End-to-end test for exact-two-attempt fallback recovery."""

import tempfile
from io import StringIO
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.agent_result import AgentResultClass
from local_cli_coordinator.db import (
    connect,
    init_db,
    create_task,
    get_task,
    list_attempts,
)
from local_cli_coordinator.reporting import ConsoleReporter, ExecutionEvent


class FallbackRenderingTest(TestCase):
    """Verify fallback events are rendered in live output."""

    def test_fallback_started_event_rendered(self) -> None:
        stream = StringIO()
        reporter = ConsoleReporter(stream=stream)
        reporter.emit(ExecutionEvent(
            kind="fallback_started",
            stage="engine",
            task_id="task-1",
            actor="grok",
        ))
        output = stream.getvalue()
        self.assertIn("FALLBACK started", output)
        self.assertIn("task-1", output)
        self.assertIn("grok", output)

    def test_worker_blocked_event_rendered(self) -> None:
        stream = StringIO()
        reporter = ConsoleReporter(stream=stream)
        reporter.emit(ExecutionEvent(
            kind="worker_blocked",
            stage="engine",
            task_id="task-1",
            actor="claude",
            text="approval_request",
        ))
        output = stream.getvalue()
        self.assertIn("BLOCKED", output)
        self.assertIn("task-1", output)

    def test_fallback_exhausted_event_rendered(self) -> None:
        stream = StringIO()
        reporter = ConsoleReporter(stream=stream)
        reporter.emit(ExecutionEvent(
            kind="fallback_exhausted",
            stage="engine",
            task_id="task-1",
            text="no eligible fallback",
        ))
        output = stream.getvalue()
        self.assertIn("FALLBACK exhausted", output)


class ExactTwoAttemptE2ETest(TestCase):
    """Verify the engine runs exactly two attempts for a blocked-then-success flow."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_two_attempts_recorded(self) -> None:
        """Simulate: first attempt blocked, second succeeds. Verify two attempts."""
        task_id = create_task(
            self.conn,
            title="test task",
            repo="repo",
            source_path="x",
            priority="normal",
            capabilities=["code"],
            goal="x",
            acceptance_criteria=["x"],
            verification_commands=["x"],
        )

        # First attempt - blocked
        from local_cli_coordinator.db import start_attempt, finish_attempt, fallback_count_for_task
        a1 = start_attempt(self.conn, task_id, "claude", "claude --print ...")
        finish_attempt(
            self.conn, a1, exit_code=0,
            result_class="interactive_blocked",
            result_reason="approval_request",
        )

        # Verify fallback count is 0 (no fallback yet)
        self.assertEqual(fallback_count_for_task(self.conn, task_id), 0)

        # Second attempt - fallback succeeds
        a2 = start_attempt(
            self.conn, task_id, "grok", "grok ...",
            fallback_from_attempt_id=a1,
        )
        finish_attempt(
            self.conn, a2, exit_code=0,
            result_class="completed",
            result_reason="ok",
        )

        # Verify
        attempts = list_attempts(self.conn, task_id)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["result_class"], "interactive_blocked")
        self.assertEqual(attempts[0]["agent_id"], "claude")
        self.assertEqual(attempts[1]["result_class"], "completed")
        self.assertEqual(attempts[1]["agent_id"], "grok")
        self.assertEqual(attempts[1]["fallback_from_attempt_id"], a1)
        self.assertEqual(fallback_count_for_task(self.conn, task_id), 1)

    def test_no_third_attempt_after_two_blocked(self) -> None:
        """After two blocked attempts, no further fallback should be possible."""
        from local_cli_coordinator.db import start_attempt, finish_attempt, fallback_count_for_task
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
        finish_attempt(self.conn, a2, exit_code=0, result_class="interactive_blocked")

        # Fallback count is 1 (one fallback was made)
        self.assertEqual(fallback_count_for_task(self.conn, task_id), 1)

        # A third attempt should NOT happen - the fallback limit is 1
        # The decide_fallback function checks fallback_count_for_task >= MAX_FALLBACK_COUNT
        from local_cli_coordinator.fallback import decide_fallback, FallbackDecision
        from local_cli_coordinator.agent_result import ClassifiedResult
        decision = decide_fallback(
            self.conn, task_id,
            ClassifiedResult(AgentResultClass.INTERACTIVE_BLOCKED, "approval"),
            fallback_agent_id="claude",
        )
        self.assertEqual(decision, FallbackDecision.HUMAN_REVIEW)

    def test_restart_preserves_fallback_count(self) -> None:
        """Fallback count persists across database close/reopen."""
        from local_cli_coordinator.db import start_attempt, finish_attempt, fallback_count_for_task
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

        # Close and reopen
        self.conn.close()
        self.conn = connect(self.db_path)

        self.assertEqual(fallback_count_for_task(self.conn, task_id), 1)
        attempts = list_attempts(self.conn, task_id)
        self.assertEqual(len(attempts), 2)
