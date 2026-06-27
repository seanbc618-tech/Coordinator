"""Red tests for Phase 6B Commander-to-backlog conversion.

Owner: Claude Code (Phase 6B Task 0)
Expected before implementation: ``ModuleNotFoundError`` for ``commander_backlog``.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.fixtures.phase6b_commander import (
    make_commander_proposal,
    make_commander_response,
)
from tests.helpers import init_git_repo


class CommanderBacklogConversionTests(unittest.TestCase):
    """Commander proposals convert into backlog drafts, never tasks."""

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
            self.conn, "Backlog goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, self.goal_id, "active")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_commander_task_proposal_converts_to_backlog_draft(self) -> None:
        from local_cli_coordinator.commander_backlog import proposal_to_backlog_draft

        proposal = make_commander_proposal(
            title="Add loop status helper",
            goal="Show generation results in /loop",
            rationale="Operator visibility",
            acceptance_criteria=["/loop shows generation count"],
            verification_commands=["true"],
        )
        draft = proposal_to_backlog_draft(proposal)

        self.assertEqual(draft.source, "commander")
        self.assertEqual(draft.title, proposal.title)
        self.assertEqual(draft.rationale, proposal.goal)
        self.assertEqual(draft.acceptance_criteria, proposal.acceptance_criteria)
        self.assertEqual(draft.verification_commands, proposal.verification_commands)
        self.assertEqual(draft.execution_policy, "normal")
        self.assertEqual(draft.priority, 50)

    def test_generation_never_creates_task_directly(self) -> None:
        from local_cli_coordinator.commander_backlog import commander_response_to_backlog

        response = make_commander_response(make_commander_proposal(title="Backlog only"))
        tasks_before = self.conn.execute("select count(*) from tasks").fetchone()[0]

        generation = commander_response_to_backlog(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            response=response,
            max_items=3,
        )
        self.conn.commit()

        tasks_after = self.conn.execute("select count(*) from tasks").fetchone()[0]
        self.assertEqual(tasks_before, tasks_after)
        self.assertEqual(len(generation.inserted_ids), 1)
        backlog_count = self.conn.execute(
            "select count(*) from project_backlog_items where project_id = ?",
            (self.project_id,),
        ).fetchone()[0]
        self.assertEqual(backlog_count, 1)

    def test_generation_caps_to_configured_max(self) -> None:
        from local_cli_coordinator.commander_backlog import commander_response_to_backlog

        proposals = [
            make_commander_proposal(title=f"Slice {index}")
            for index in range(1, 6)
        ]
        response = make_commander_response(*proposals)

        generation = commander_response_to_backlog(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            response=response,
            max_items=2,
        )
        self.conn.commit()

        self.assertEqual(len(generation.inserted_ids), 2)
        backlog_count = self.conn.execute(
            "select count(*) from project_backlog_items where project_id = ?",
            (self.project_id,),
        ).fetchone()[0]
        self.assertEqual(backlog_count, 2)


if __name__ == "__main__":
    unittest.main()