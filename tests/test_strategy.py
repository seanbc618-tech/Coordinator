"""Red tests for Phase 7 strategy milestones.

These tests capture the contract for ``strategy.py``:
project-scoped milestones, CRUD helpers, and milestone-linked summaries.

Owner: Grok (Phase 7 Task 0)
Expected before implementation: ``ModuleNotFoundError`` for ``strategy``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class MilestoneModuleTests(unittest.TestCase):
    """strategy module exports milestone helpers."""

    def test_strategy_module_import(self) -> None:
        from local_cli_coordinator.strategy import (
            Milestone,
            complete_milestone,
            create_milestone,
            get_active_milestone,
            list_milestones,
        )
        self.assertTrue(callable(create_milestone))
        self.assertTrue(callable(list_milestones))
        self.assertTrue(callable(get_active_milestone))
        self.assertTrue(callable(complete_milestone))
        self.assertTrue(hasattr(Milestone, "id"))


class MilestoneCRUDTests(unittest.TestCase):
    """Milestones are durable and project-scoped."""

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
        self.goal_id = create_goal(
            self.conn, "Strategy goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, self.goal_id, "active")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_milestone_persists_row(self) -> None:
        from local_cli_coordinator.strategy import create_milestone, list_milestones

        milestone_id = create_milestone(
            self.conn,
            project_id=self.project_id,
            title="Ship login flow",
            goal_id=self.goal_id,
            success_criteria=["users can log in"],
        )
        self.assertIsInstance(milestone_id, int)
        rows = list_milestones(self.conn, project_id=self.project_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Ship login flow")
        self.assertEqual(rows[0].status, "active")

    def test_get_active_milestone_returns_highest_priority(self) -> None:
        from local_cli_coordinator.strategy import (
            create_milestone,
            get_active_milestone,
        )

        create_milestone(
            self.conn,
            project_id=self.project_id,
            title="Lower priority",
            priority=1,
        )
        create_milestone(
            self.conn,
            project_id=self.project_id,
            title="Higher priority",
            priority=10,
        )
        active = get_active_milestone(self.conn, project_id=self.project_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active.title, "Higher priority")

    def test_milestones_are_project_scoped(self) -> None:
        from local_cli_coordinator.strategy import create_milestone, list_milestones

        repo_b = self.tmp / "repo-b"
        init_git_repo(repo_b)
        draft_b = inspect_project(repo_b)
        register_project(self.conn, draft_b, confirmed=True)
        self.conn.commit()
        project_b = self.conn.execute(
            "select id from projects where id != ? limit 1",
            (self.project_id,),
        ).fetchone()["id"]

        create_milestone(
            self.conn,
            project_id=self.project_id,
            title="Project A milestone",
        )
        create_milestone(
            self.conn,
            project_id=project_b,
            title="Project B secret milestone",
        )

        rows_a = list_milestones(self.conn, project_id=self.project_id)
        titles_a = {row.title for row in rows_a}
        self.assertIn("Project A milestone", titles_a)
        self.assertNotIn("Project B secret milestone", titles_a)


class MilestoneBacklogLinkTests(unittest.TestCase):
    """Backlog drafts may carry milestone_id."""

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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backlog_draft_accepts_milestone_id(self) -> None:
        from local_cli_coordinator.autonomous_backlog import BacklogDraft
        from local_cli_coordinator.strategy import create_milestone

        milestone_id = create_milestone(
            self.conn,
            project_id=self.project_id,
            title="Linked milestone",
        )
        draft = BacklogDraft(
            source="commander",
            title="fix auth",
            rationale="needed for milestone",
            acceptance_criteria=["auth works"],
            verification_commands=["pytest"],
            milestone_id=milestone_id,
        )
        self.assertEqual(draft.milestone_id, milestone_id)

    def test_propose_backlog_persists_milestone_id(self) -> None:
        from local_cli_coordinator.autonomous_backlog import (
            BacklogDraft,
            propose_backlog_items,
        )
        from local_cli_coordinator.strategy import create_milestone

        milestone_id = create_milestone(
            self.conn,
            project_id=self.project_id,
            title="Backlog link",
        )
        propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=None,
            drafts=[
                BacklogDraft(
                    source="operator",
                    title="milestone task",
                    rationale="r",
                    acceptance_criteria=["done"],
                    verification_commands=[],
                    milestone_id=milestone_id,
                )
            ],
        )
        row = self.conn.execute(
            "select milestone_id from project_backlog_items where project_id = ?",
            (self.project_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["milestone_id"], milestone_id)


class MilestoneLoopStatusTests(unittest.TestCase):
    """Loop status reports current milestone without cross-project leakage."""

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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_strategy_summary_is_project_scoped(self) -> None:
        from local_cli_coordinator.strategy import (
            build_strategy_summary,
            create_milestone,
        )

        repo_b = self.tmp / "repo-b"
        init_git_repo(repo_b)
        draft_b = inspect_project(repo_b)
        register_project(self.conn, draft_b, confirmed=True)
        self.conn.commit()
        project_b = self.conn.execute(
            "select id from projects where id != ? limit 1",
            (self.project_id,),
        ).fetchone()["id"]

        create_milestone(
            self.conn,
            project_id=self.project_id,
            title="Visible milestone",
        )
        create_milestone(
            self.conn,
            project_id=project_b,
            title="Secret other-project milestone",
        )

        summary = build_strategy_summary(self.conn, project_id=self.project_id)
        serialized = json.dumps(summary)
        self.assertIn("Visible milestone", serialized)
        self.assertNotIn("Secret other-project milestone", serialized)


if __name__ == "__main__":
    unittest.main()