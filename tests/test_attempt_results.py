"""Tests for attempt result persistence and fallback lineage."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.db import (
    connect,
    init_db,
    create_task,
    start_attempt,
    finish_attempt,
    list_attempts,
    fallback_count_for_task,
)


class AttemptResultPersistenceTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.task_id = create_task(
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

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_start_and_finish_attempt(self) -> None:
        attempt_id = start_attempt(
            self.conn, self.task_id, "claude", "claude --print ..."
        )
        self.assertIsInstance(attempt_id, int)
        self.assertGreater(attempt_id, 0)

        finish_attempt(
            self.conn,
            attempt_id,
            exit_code=0,
            result_class="completed",
            result_reason="ok",
            log_path="/tmp/agent.log",
        )
        attempts = list_attempts(self.conn, self.task_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["result_class"], "completed")
        self.assertEqual(attempts[0]["result_reason"], "ok")
        self.assertEqual(attempts[0]["exit_code"], 0)

    def test_result_class_interactive_blocked(self) -> None:
        attempt_id = start_attempt(
            self.conn, self.task_id, "claude", "claude --print ..."
        )
        finish_attempt(
            self.conn,
            attempt_id,
            exit_code=0,
            result_class="interactive_blocked",
            result_reason="approval_request",
        )
        attempts = list_attempts(self.conn, self.task_id)
        self.assertEqual(attempts[0]["result_class"], "interactive_blocked")

    def test_fallback_from_attempt_id(self) -> None:
        first = start_attempt(
            self.conn, self.task_id, "claude", "claude --print ..."
        )
        finish_attempt(
            self.conn, first, exit_code=0, result_class="interactive_blocked"
        )
        second = start_attempt(
            self.conn,
            self.task_id,
            "grok",
            "grok --prompt-file ...",
            fallback_from_attempt_id=first,
        )
        finish_attempt(self.conn, second, exit_code=0, result_class="completed")
        attempts = list_attempts(self.conn, self.task_id)
        self.assertEqual(len(attempts), 2)
        self.assertIsNone(attempts[0]["fallback_from_attempt_id"])
        self.assertEqual(attempts[1]["fallback_from_attempt_id"], first)

    def test_fallback_count_for_task(self) -> None:
        self.assertEqual(fallback_count_for_task(self.conn, self.task_id), 0)
        first = start_attempt(self.conn, self.task_id, "claude", "cmd")
        finish_attempt(self.conn, first, exit_code=0, result_class="interactive_blocked")
        self.assertEqual(fallback_count_for_task(self.conn, self.task_id), 0)
        second = start_attempt(
            self.conn, self.task_id, "grok", "cmd", fallback_from_attempt_id=first
        )
        finish_attempt(self.conn, second, exit_code=0, result_class="completed")
        self.assertEqual(fallback_count_for_task(self.conn, self.task_id), 1)

    def test_fallback_parent_must_belong_to_same_task(self) -> None:
        other_task = create_task(
            self.conn,
            title="other",
            repo="repo",
            source_path="x",
            priority="normal",
            capabilities=["code"],
            goal="x",
            acceptance_criteria=["x"],
            verification_commands=["x"],
        )
        first = start_attempt(self.conn, other_task, "claude", "cmd")
        with self.assertRaises(ValueError):
            start_attempt(
                self.conn, self.task_id, "grok", "cmd",
                fallback_from_attempt_id=first,
            )

    def test_attempts_ordered_by_id(self) -> None:
        a1 = start_attempt(self.conn, self.task_id, "claude", "cmd1")
        finish_attempt(self.conn, a1, exit_code=1, result_class="command_failed")
        a2 = start_attempt(self.conn, self.task_id, "grok", "cmd2")
        finish_attempt(self.conn, a2, exit_code=0, result_class="completed")
        attempts = list_attempts(self.conn, self.task_id)
        self.assertEqual(attempts[0]["id"], a1)
        self.assertEqual(attempts[1]["id"], a2)

    def test_restart_persistence(self) -> None:
        attempt_id = start_attempt(self.conn, self.task_id, "claude", "cmd")
        finish_attempt(
            self.conn, attempt_id, exit_code=0, result_class="completed"
        )
        # Close and reopen
        self.conn.close()
        self.conn = connect(self.db_path)
        attempts = list_attempts(self.conn, self.task_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["result_class"], "completed")
