"""Red tests for Phase 6 rule-based task evaluator.

These tests capture the contract for ``evaluator.py``:
``find_unevaluated_terminal_tasks``, ``evaluate_task``,
``record_task_evaluation``, and verdict rules.

Owner: Claude Code (Phase 6 Task 0)
Expected before implementation: ``ModuleNotFoundError`` for ``evaluator``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class EvaluatorImportTests(unittest.TestCase):
    """evaluator module exists and exports required types."""

    def test_evaluator_module_imports(self) -> None:
        from local_cli_coordinator.evaluator import (
            TaskEvaluation,
            find_unevaluated_terminal_tasks,
            evaluate_task,
            record_task_evaluation,
        )
        self.assertIsNotNone(TaskEvaluation)
        self.assertIsNotNone(find_unevaluated_terminal_tasks)
        self.assertIsNotNone(evaluate_task)
        self.assertIsNotNone(record_task_evaluation)

    def test_task_evaluation_dataclass(self) -> None:
        """TaskEvaluation has required fields with correct types."""
        from local_cli_coordinator.evaluator import TaskEvaluation
        ev = TaskEvaluation(
            task_id="task-1",
            project_id="proj-a",
            goal_id=1,
            verdict="pass",
            summary="task completed successfully",
            evidence={"exit_code": 0},
            next_action="none",
        )
        self.assertEqual(ev.verdict, "pass")
        self.assertEqual(ev.next_action, "none")


class EvaluatorTerminalDetectionTests(unittest.TestCase):
    """find_unevaluated_terminal_tasks returns terminal tasks without evaluation."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_evaluator_records_terminal_task_once(self) -> None:
        """Recording an evaluation twice raises or returns the same id."""
        from local_cli_coordinator.evaluator import (
            TaskEvaluation,
            record_task_evaluation,
        )
        ev = TaskEvaluation(
            task_id="task-1",
            project_id=self.project_id,
            goal_id=None,
            verdict="pass",
            summary="ok",
            evidence={},
            next_action="none",
        )
        id1 = record_task_evaluation(self.conn, ev)
        self.assertIsNotNone(id1)
        # Second record for same task+evaluator should be idempotent.
        id2 = record_task_evaluation(self.conn, ev)
        self.assertEqual(id1, id2)

    def test_evaluator_finds_unevaluated_terminal(self) -> None:
        """Terminal tasks without evaluations are returned."""
        from local_cli_coordinator.evaluator import (
            find_unevaluated_terminal_tasks,
        )
        # Create a terminal task directly in DB.
        self.conn.execute(
            "insert into tasks (id, title, status, project_id) "
            "values ('task-uneval', 'test task', 'done', ?)",
            (self.project_id,),
        )
        self.conn.commit()
        task_ids = find_unevaluated_terminal_tasks(
            self.conn, project_id=self.project_id, limit=10,
        )
        self.assertIn("task-uneval", task_ids)

    def test_evaluator_skips_already_evaluated(self) -> None:
        """Tasks with existing evaluations are excluded."""
        from local_cli_coordinator.evaluator import (
            TaskEvaluation,
            find_unevaluated_terminal_tasks,
            record_task_evaluation,
        )
        self.conn.execute(
            "insert into tasks (id, title, status, project_id) "
            "values ('task-evaled', 'done task', 'done', ?)",
            (self.project_id,),
        )
        self.conn.commit()
        # Record evaluation first.
        record_task_evaluation(self.conn, TaskEvaluation(
            task_id="task-evaled",
            project_id=self.project_id,
            goal_id=None,
            verdict="pass",
            summary="ok",
            evidence={},
            next_action="none",
        ))
        task_ids = find_unevaluated_terminal_tasks(
            self.conn, project_id=self.project_id, limit=10,
        )
        self.assertNotIn("task-evaled", task_ids)


class EvaluatorVerdictTests(unittest.TestCase):
    """evaluate_task produces correct verdicts based on task state."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()
        self.project_id = self.conn.execute(
            "select id from projects limit 1"
        ).fetchone()["id"]

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_evaluator_flags_failed_task_as_followup(self) -> None:
        """A failed task evaluates to 'fail' or 'needs_followup'."""
        from local_cli_coordinator.evaluator import evaluate_task
        self.conn.execute(
            "insert into tasks (id, title, status, project_id) "
            "values ('task-fail', 'failed task', 'failed', ?)",
            (self.project_id,),
        )
        self.conn.commit()
        ev = evaluate_task(self.conn, task_id="task-fail")
        self.assertIn(ev.verdict, ("fail", "needs_followup"))

    def test_evaluator_passes_completed_task(self) -> None:
        """A completed task with verification passes evaluation."""
        from local_cli_coordinator.evaluator import evaluate_task
        self.conn.execute(
            "insert into tasks (id, title, status, project_id, "
            "verification_commands_json) "
            "values ('task-done', 'done task', 'done', ?, '[\"true\"]')",
            (self.project_id,),
        )
        self.conn.commit()
        ev = evaluate_task(self.conn, task_id="task-done")
        self.assertIn(ev.verdict, ("pass", "needs_followup"))

    def test_evaluator_next_action_is_valid(self) -> None:
        """evaluate_task returns a valid next_action value."""
        from local_cli_coordinator.evaluator import evaluate_task
        valid_actions = {
            "none", "admit_followup", "ask_commander",
            "pause_goal", "human_review",
        }
        self.conn.execute(
            "insert into tasks (id, title, status, project_id) "
            "values ('task-na', 'test', 'done', ?)",
            (self.project_id,),
        )
        self.conn.commit()
        ev = evaluate_task(self.conn, task_id="task-na")
        self.assertIn(ev.next_action, valid_actions)
