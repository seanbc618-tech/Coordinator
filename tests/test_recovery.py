"""Red tests for Phase 7 failure recovery proposals.

These tests capture the contract for ``recovery.py``:
bounded, deduped recovery proposals for failed terminal tasks.

Owner: Grok (Phase 7 Task 0)
Expected before implementation: ``ModuleNotFoundError`` for ``recovery``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.evaluator import record_task_evaluation
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo, insert_terminal_task


class RecoveryModuleTests(unittest.TestCase):
    """recovery module exports proposal helpers."""

    def test_recovery_module_import(self) -> None:
        from local_cli_coordinator.recovery import (
            RecoveryProposal,
            admit_recovery_to_backlog,
            list_recovery_proposals,
            propose_recovery_for_failed_task,
        )
        self.assertTrue(callable(propose_recovery_for_failed_task))
        self.assertTrue(callable(list_recovery_proposals))
        self.assertTrue(callable(admit_recovery_to_backlog))
        self.assertIn("id", RecoveryProposal.__annotations__)


class RecoveryProposalTests(unittest.TestCase):
    """Failed tasks produce at most one open recovery proposal."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
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
        self.task_id = "task-fail-1"
        insert_terminal_task(
            self.conn,
            task_id=self.task_id,
            title="Broken build",
            state="failed",
            project_id=self.project_id,
            verification_commands="false",
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_failed_task_produces_recovery_proposal(self) -> None:
        from local_cli_coordinator.recovery import (
            list_recovery_proposals,
            propose_recovery_for_failed_task,
        )

        proposal_id = propose_recovery_for_failed_task(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
        )
        self.assertIsNotNone(proposal_id)
        pending = list_recovery_proposals(
            self.conn, project_id=self.project_id, status="pending"
        )
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].task_id, self.task_id)
        self.assertIn(pending[0].proposal_type, ("repair", "diagnostic"))

    def test_duplicate_proposals_are_deduped(self) -> None:
        from local_cli_coordinator.recovery import (
            list_recovery_proposals,
            propose_recovery_for_failed_task,
        )

        first = propose_recovery_for_failed_task(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
        )
        second = propose_recovery_for_failed_task(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        pending = list_recovery_proposals(
            self.conn, project_id=self.project_id, status="pending"
        )
        self.assertEqual(len(pending), 1)

    def test_non_failed_task_does_not_propose_recovery(self) -> None:
        from local_cli_coordinator.recovery import propose_recovery_for_failed_task

        insert_terminal_task(
            self.conn,
            task_id="task-done-1",
            title="Done task",
            state="done",
            project_id=self.project_id,
        )
        self.conn.commit()
        proposal_id = propose_recovery_for_failed_task(
            self.conn,
            project_id=self.project_id,
            task_id="task-done-1",
        )
        self.assertIsNone(proposal_id)


class RecoveryAdmissionTests(unittest.TestCase):
    """Recovery admission flows through backlog governance."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
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
        self.task_id = "task-fail-admit"
        insert_terminal_task(
            self.conn,
            task_id=self.task_id,
            title="Needs repair",
            state="failed",
            project_id=self.project_id,
            verification_commands="false",
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_admit_recovery_creates_ready_backlog_item(self) -> None:
        from local_cli_coordinator.evaluator import TaskEvaluation
        from local_cli_coordinator.recovery import (
            admit_recovery_to_backlog,
            propose_recovery_for_failed_task,
        )

        proposal_id = propose_recovery_for_failed_task(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
        )
        assert proposal_id is not None
        record_task_evaluation(
            self.conn,
            TaskEvaluation(
                task_id=self.task_id,
                project_id=self.project_id,
                goal_id=None,
                verdict="fail",
                summary="verification failed",
                evidence={},
                next_action="repair",
            ),
        )
        backlog_id = admit_recovery_to_backlog(
            self.conn,
            proposal_id=proposal_id,
        )
        self.assertTrue(backlog_id)
        row = self.conn.execute(
            """
            select status, admitted_backlog_id
            from task_recovery_proposals
            where id = ?
            """,
            (proposal_id,),
        ).fetchone()
        self.assertEqual(row["status"], "admitted")
        self.assertEqual(row["admitted_backlog_id"], backlog_id)
        backlog = self.conn.execute(
            "select status from project_backlog_items where id = ?",
            (backlog_id,),
        ).fetchone()
        self.assertEqual(backlog["status"], "ready")


if __name__ == "__main__":
    unittest.main()