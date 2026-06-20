"""Tests for cross-agent fallback decision logic."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.agent_result import AgentResultClass, ClassifiedResult
from local_cli_coordinator.db import (
    connect,
    init_db,
    create_task,
    start_attempt,
    finish_attempt,
    fallback_count_for_task,
)
from local_cli_coordinator.fallback import FallbackDecision, decide_fallback


class DecideFallbackTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.task_id = create_task(
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
        # Create a fake worktree with no changes
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        (self.worktree / "file.txt").write_text("original")
        # Initialize git so git status works
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=self.worktree, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=self.worktree, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=self.worktree, capture_output=True,
            env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        )

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_blocked_with_eligible_fallback_returns_run(self) -> None:
        classified = ClassifiedResult(
            classification=AgentResultClass.INTERACTIVE_BLOCKED,
            reason="approval_request",
        )
        decision = decide_fallback(
            self.conn,
            self.task_id,
            classified,
            fallback_agent_id="grok",
            worktree=self.worktree,
        )
        self.assertEqual(decision, FallbackDecision.RUN)

    def test_completed_returns_fail(self) -> None:
        classified = ClassifiedResult(
            classification=AgentResultClass.COMPLETED,
            reason="ok",
        )
        decision = decide_fallback(
            self.conn,
            self.task_id,
            classified,
            fallback_agent_id="grok",
            worktree=self.worktree,
        )
        self.assertEqual(decision, FallbackDecision.FAIL)

    def test_command_failed_returns_fail(self) -> None:
        classified = ClassifiedResult(
            classification=AgentResultClass.COMMAND_FAILED,
            reason="exit_code=1",
        )
        decision = decide_fallback(
            self.conn,
            self.task_id,
            classified,
            fallback_agent_id="grok",
            worktree=self.worktree,
        )
        self.assertEqual(decision, FallbackDecision.FAIL)

    def test_timed_out_returns_fail(self) -> None:
        classified = ClassifiedResult(
            classification=AgentResultClass.TIMED_OUT,
            reason="timeout",
        )
        decision = decide_fallback(
            self.conn,
            self.task_id,
            classified,
            fallback_agent_id="grok",
            worktree=self.worktree,
        )
        self.assertEqual(decision, FallbackDecision.FAIL)

    def test_no_eligible_fallback_returns_human_review(self) -> None:
        classified = ClassifiedResult(
            classification=AgentResultClass.INTERACTIVE_BLOCKED,
            reason="approval_request",
        )
        decision = decide_fallback(
            self.conn,
            self.task_id,
            classified,
            fallback_agent_id=None,
            worktree=self.worktree,
        )
        self.assertEqual(decision, FallbackDecision.HUMAN_REVIEW)

    def test_already_used_fallback_returns_human_review(self) -> None:
        # Record one fallback already
        a1 = start_attempt(self.conn, self.task_id, "claude", "cmd1")
        finish_attempt(self.conn, a1, exit_code=0, result_class="interactive_blocked")
        a2 = start_attempt(
            self.conn, self.task_id, "grok", "cmd2", fallback_from_attempt_id=a1
        )
        finish_attempt(self.conn, a2, exit_code=0, result_class="interactive_blocked")

        classified = ClassifiedResult(
            classification=AgentResultClass.INTERACTIVE_BLOCKED,
            reason="approval_request",
        )
        decision = decide_fallback(
            self.conn,
            self.task_id,
            classified,
            fallback_agent_id="claude",
            worktree=self.worktree,
        )
        self.assertEqual(decision, FallbackDecision.HUMAN_REVIEW)

    def test_tracked_changes_returns_human_review(self) -> None:
        # Create a git repo in worktree with a tracked change
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=self.worktree, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=self.worktree, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=self.worktree, capture_output=True,
            env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        )
        # Add a tracked change
        (self.worktree / "file.txt").write_text("modified")
        subprocess.run(["git", "add", "file.txt"], cwd=self.worktree, capture_output=True)

        classified = ClassifiedResult(
            classification=AgentResultClass.INTERACTIVE_BLOCKED,
            reason="approval_request",
        )
        decision = decide_fallback(
            self.conn,
            self.task_id,
            classified,
            fallback_agent_id="grok",
            worktree=self.worktree,
        )
        self.assertEqual(decision, FallbackDecision.HUMAN_REVIEW)

    def test_untracked_changes_returns_human_review(self) -> None:
        # Initialize a git repo so git status works
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=self.worktree, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=self.worktree, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=self.worktree, capture_output=True,
            env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        )
        # Add an untracked file
        (self.worktree / "new_file.txt").write_text("untracked")

        classified = ClassifiedResult(
            classification=AgentResultClass.INTERACTIVE_BLOCKED,
            reason="approval_request",
        )
        decision = decide_fallback(
            self.conn,
            self.task_id,
            classified,
            fallback_agent_id="grok",
            worktree=self.worktree,
        )
        self.assertEqual(decision, FallbackDecision.HUMAN_REVIEW)
