"""Red tests for Phase 6 autonomous loop iteration engine.

These tests capture the contract for ``loop_autonomy.py``:
``run_autonomous_iteration`` and ``LoopDecision``.

Owner: Claude Code (Phase 6 Task 0, Phase 6B Task 0)
Expected before implementation: generation tests fail until Commander backlog
adapter and ``_maybe_generate_backlog()`` are implemented.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.config import CoordinatorConfig
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.fixtures.phase6b_commander import (
    autonomy_loop_config,
    make_commander_proposal,
    make_commander_response,
    make_commander_run_result,
)
from tests.helpers import init_git_repo, insert_terminal_task


class LoopDecisionTests(unittest.TestCase):
    """LoopDecision dataclass has required fields."""

    def test_loop_decision_import(self) -> None:
        from local_cli_coordinator.loop_autonomy import LoopDecision
        decision = LoopDecision(
            project_id="proj-a",
            goal_id=1,
            decision="wait",
            reason="no active goal",
        )
        self.assertEqual(decision.decision, "wait")
        self.assertEqual(decision.reason, "no active goal")

    def test_loop_decision_defaults(self) -> None:
        """LoopDecision has zero-count defaults."""
        from local_cli_coordinator.loop_autonomy import LoopDecision
        decision = LoopDecision(
            project_id="p", goal_id=None, decision="wait", reason="r",
        )
        self.assertEqual(decision.evaluated_count, 0)
        self.assertEqual(len(decision.admitted_task_ids), 0)
        self.assertEqual(len(decision.generated_backlog_ids), 0)


class AutonomousIterationTests(unittest.TestCase):
    """run_autonomous_iteration makes bounded decisions."""

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
        self.goal_id = create_goal(
            self.conn, "Loop goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, self.goal_id, "active")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _autonomy_config(self, *, max_generated: int = 3) -> CoordinatorConfig:
        return autonomy_loop_config(
            self.tmp,
            self.repo,
            max_generated=max_generated,
        )

    def _run_iteration(
        self,
        *,
        config: CoordinatorConfig | None = None,
        **kwargs,
    ):
        from local_cli_coordinator.loop_autonomy import run_autonomous_iteration
        return run_autonomous_iteration(
            self.conn,
            project_id=self.project_id,
            config=config or mock.MagicMock(),
            paths=self.paths,
            **kwargs,
        )

    def test_loop_waits_when_project_has_running_task(self) -> None:
        """Iteration decides 'wait' when a task is already running."""
        # Insert a running task.
        insert_terminal_task(
            self.conn,
            task_id="task-running",
            title="busy",
            state="running",
            project_id=self.project_id,
        )
        self.conn.commit()
        decision = self._run_iteration(
            max_evaluations=3, max_admissions=1,
        )
        self.assertEqual(decision.decision, "wait")
        self.assertIn("running", decision.reason.lower())

    def test_loop_admits_one_backlog_item_when_idle(self) -> None:
        """When idle with ready backlog, iteration admits one item."""
        from local_cli_coordinator.autonomous_backlog import (
            BacklogDraft,
            propose_backlog_items,
        )
        propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            drafts=[BacklogDraft(
                source="operator", title="Task A", rationale="r",
                acceptance_criteria=["c"], verification_commands=[],
            )],
        )
        decision = self._run_iteration(
            max_evaluations=3, max_admissions=1,
        )
        self.assertEqual(decision.decision, "admit")
        self.assertEqual(len(decision.admitted_task_ids), 1)

    def test_loop_records_every_iteration_reason(self) -> None:
        """Every iteration writes a loop_iterations row with a reason."""
        self._run_iteration(max_evaluations=3, max_admissions=1)
        row = self.conn.execute(
            "select decision, reason from loop_iterations "
            "where project_id = ? order by started_at desc limit 1",
            (self.project_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(len(row["reason"]) > 0)

    def test_loop_respects_max_evaluations(self) -> None:
        """Iteration evaluates at most max_evaluations terminal tasks."""
        # Create 5 terminal tasks.
        for i in range(5):
            insert_terminal_task(
                self.conn,
                task_id=f"task-eval-{i}",
                title=f"task {i}",
                state="done",
                project_id=self.project_id,
            )
        self.conn.commit()
        decision = self._run_iteration(
            max_evaluations=2, max_admissions=0,
        )
        self.assertLessEqual(decision.evaluated_count, 2)

    def test_loop_respects_max_admissions(self) -> None:
        """Iteration admits at most max_admissions backlog items."""
        from local_cli_coordinator.autonomous_backlog import (
            BacklogDraft,
            propose_backlog_items,
        )
        propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            drafts=[
                BacklogDraft(
                    source="operator", title=f"Task {i}", rationale="r",
                    acceptance_criteria=[f"c{i}"], verification_commands=[],
                )
                for i in range(5)
            ],
        )
        decision = self._run_iteration(
            max_evaluations=0, max_admissions=2,
        )
        self.assertLessEqual(len(decision.admitted_task_ids), 2)

    def test_loop_wait_when_no_active_goal(self) -> None:
        """Iteration waits when project has no active goal."""
        # Complete the goal.
        transition_goal(self.conn, self.goal_id, "completed")
        self.conn.commit()
        # Create a new non-active goal.
        new_goal = create_goal(
            self.conn, "Paused goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, new_goal, "paused")
        self.conn.commit()
        decision = self._run_iteration(
            max_evaluations=3, max_admissions=1,
        )
        self.assertEqual(decision.decision, "wait")

    def test_loop_iteration_is_project_scoped(self) -> None:
        """Iteration only sees tasks/backlog for its project."""
        from local_cli_coordinator.autonomous_backlog import (
            BacklogDraft,
            propose_backlog_items,
        )
        # Insert a task in a different project.
        insert_terminal_task(
            self.conn,
            task_id="task-other",
            title="other",
            state="done",
            project_id="other-project",
        )
        self.conn.commit()
        # Propose backlog for this project only.
        propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            drafts=[BacklogDraft(
                source="operator", title="My task", rationale="r",
                acceptance_criteria=["c"], verification_commands=[],
            )],
        )
        decision = self._run_iteration(
            max_evaluations=10, max_admissions=1,
        )
        # Should not evaluate tasks from other projects.
        self.assertLessEqual(decision.evaluated_count, 1)

    @mock.patch("local_cli_coordinator.loop_autonomy.run_commander", create=True)
    def test_loop_generates_backlog_when_idle_and_empty(
        self,
        mock_run_commander: mock.MagicMock,
    ) -> None:
        """Idle project with no ready backlog asks Commander for drafts."""
        mock_run_commander.return_value = make_commander_run_result(
            make_commander_response(make_commander_proposal(title="Generated slice")),
            tmp_dir=self.tmp,
        )
        decision = self._run_iteration(
            config=self._autonomy_config(),
            max_evaluations=0,
            max_admissions=0,
        )
        self.assertEqual(decision.decision, "generate")
        self.assertEqual(len(decision.generated_backlog_ids), 1)
        mock_run_commander.assert_called_once()

    @mock.patch("local_cli_coordinator.loop_autonomy.run_commander", create=True)
    def test_loop_generation_does_not_admit_task_same_iteration(
        self,
        mock_run_commander: mock.MagicMock,
    ) -> None:
        """Generation inserts backlog only; no task is admitted in the same tick."""
        mock_run_commander.return_value = make_commander_run_result(
            make_commander_response(make_commander_proposal(title="No same-tick admit")),
            tmp_dir=self.tmp,
        )
        tasks_before = self.conn.execute("select count(*) from tasks").fetchone()[0]
        decision = self._run_iteration(
            config=self._autonomy_config(),
            max_evaluations=0,
            max_admissions=1,
        )
        tasks_after = self.conn.execute("select count(*) from tasks").fetchone()[0]

        self.assertEqual(decision.decision, "generate")
        self.assertEqual(len(decision.admitted_task_ids), 0)
        self.assertEqual(tasks_before, tasks_after)

    def test_loop_does_not_generate_when_ready_backlog_exists(self) -> None:
        """Ready backlog is admitted before Commander generation runs."""
        from local_cli_coordinator.autonomous_backlog import (
            BacklogDraft,
            propose_backlog_items,
        )

        propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            drafts=[BacklogDraft(
                source="operator",
                title="Ready first",
                rationale="r",
                acceptance_criteria=["c"],
                verification_commands=[],
            )],
        )
        with mock.patch(
            "local_cli_coordinator.loop_autonomy.run_commander",
            create=True,
        ) as mock_run_commander:
            decision = self._run_iteration(
                config=self._autonomy_config(),
                max_evaluations=0,
                max_admissions=1,
            )
            mock_run_commander.assert_not_called()
        self.assertEqual(decision.decision, "admit")
        self.assertEqual(len(decision.admitted_task_ids), 1)

    @mock.patch("local_cli_coordinator.loop_autonomy.run_commander", create=True)
    def test_loop_does_not_generate_when_commander_run_active(
        self,
        mock_run_commander: mock.MagicMock,
    ) -> None:
        """Active Commander run returns quickly without generating backlog."""
        from local_cli_coordinator.commander_runner import CommanderRunActiveError

        mock_run_commander.side_effect = CommanderRunActiveError(
            "commander run already active"
        )
        decision = self._run_iteration(
            config=self._autonomy_config(),
            max_evaluations=0,
            max_admissions=0,
        )
        mock_run_commander.assert_called_once()
        self.assertEqual(decision.decision, "wait")
        self.assertEqual(len(decision.generated_backlog_ids), 0)

    @mock.patch("local_cli_coordinator.loop_autonomy.run_commander", create=True)
    def test_duplicate_generated_backlog_is_idempotent(
        self,
        mock_run_commander: mock.MagicMock,
    ) -> None:
        """Repeated Commander proposals with the same dedupe key stay idempotent."""
        mock_run_commander.return_value = make_commander_run_result(
            make_commander_response(
                make_commander_proposal(title="Idempotent backlog item")
            ),
            tmp_dir=self.tmp,
        )
        config = self._autonomy_config()
        first = self._run_iteration(
            config=config,
            max_evaluations=0,
            max_admissions=0,
        )
        second = self._run_iteration(
            config=config,
            max_evaluations=0,
            max_admissions=0,
        )
        backlog_count = self.conn.execute(
            "select count(*) from project_backlog_items where project_id = ?",
            (self.project_id,),
        ).fetchone()[0]

        self.assertEqual(first.decision, "generate")
        self.assertEqual(backlog_count, 1)
        self.assertIn(second.decision, {"wait", "generate"})
