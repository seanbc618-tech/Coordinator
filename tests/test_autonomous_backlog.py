"""Red tests for Phase 6 autonomous backlog governance.

These tests capture the contract for ``autonomous_backlog.py``:
``BacklogDraft``, ``compute_backlog_dedupe_key``, ``propose_backlog_items``,
and ``promote_next_backlog_item``.

Owner: Claude Code (Phase 6 Task 0)
Expected before implementation: ``ModuleNotFoundError`` for
``autonomous_backlog``.
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


class BacklogDraftTests(unittest.TestCase):
    """BacklogDraft dataclass has required fields."""

    def test_backlog_draft_import(self) -> None:
        """autonomous_backlog module exists and exports BacklogDraft."""
        from local_cli_coordinator.autonomous_backlog import BacklogDraft
        draft = BacklogDraft(
            source="operator",
            title="fix login",
            rationale="users can't log in",
            acceptance_criteria=["login works"],
            verification_commands=["pytest test_login"],
        )
        self.assertEqual(draft.source, "operator")
        self.assertEqual(draft.title, "fix login")

    def test_backlog_draft_defaults(self) -> None:
        """BacklogDraft has sensible defaults for optional fields."""
        from local_cli_coordinator.autonomous_backlog import BacklogDraft
        draft = BacklogDraft(
            source="commander",
            title="task",
            rationale="reason",
            acceptance_criteria=[],
            verification_commands=[],
        )
        self.assertEqual(draft.execution_policy, "normal")
        self.assertEqual(draft.priority, 50)


class BacklogDedupeTests(unittest.TestCase):
    """Duplicate open backlog items are rejected."""

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
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backlog_dedupes_duplicate_open_items(self) -> None:
        """Proposing the same title+criteria twice inserts only once."""
        from local_cli_coordinator.autonomous_backlog import (
            BacklogDraft,
            propose_backlog_items,
        )
        drafts = [
            BacklogDraft(
                source="operator",
                title="Fix login bug",
                rationale="users locked out",
                acceptance_criteria=["login works"],
                verification_commands=["pytest"],
            ),
        ]
        ids1 = propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            drafts=drafts,
        )
        self.assertEqual(len(ids1), 1)

        # Second proposal with same title+criteria should be deduplicated.
        ids2 = propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            drafts=drafts,
        )
        self.assertEqual(len(ids2), 0, "duplicate should be rejected")

    def test_backlog_allows_same_title_different_criteria(self) -> None:
        """Different acceptance criteria produce different dedupe keys."""
        from local_cli_coordinator.autonomous_backlog import (
            BacklogDraft,
            propose_backlog_items,
        )
        ids1 = propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            drafts=[BacklogDraft(
                source="operator", title="Fix bug", rationale="r",
                acceptance_criteria=["criterion A"],
                verification_commands=[],
            )],
        )
        ids2 = propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            drafts=[BacklogDraft(
                source="operator", title="Fix bug", rationale="r",
                acceptance_criteria=["criterion B"],
                verification_commands=[],
            )],
        )
        self.assertEqual(len(ids1), 1)
        self.assertEqual(len(ids2), 1)

    def test_dedupe_key_is_case_insensitive(self) -> None:
        """compute_backlog_dedupe_key normalizes case."""
        from local_cli_coordinator.autonomous_backlog import (
            compute_backlog_dedupe_key,
        )
        key1 = compute_backlog_dedupe_key("Fix Login", ["Works"])
        key2 = compute_backlog_dedupe_key("fix login", ["works"])
        self.assertEqual(key1, key2)


class BacklogPromoteTests(unittest.TestCase):
    """promote_next_backlog_item creates real tasks from backlog."""

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
            self.conn, "Promote goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, self.goal_id, "active")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backlog_promote_creates_project_task(self) -> None:
        """Promoting a ready backlog item creates a task in the project."""
        from local_cli_coordinator.autonomous_backlog import (
            BacklogDraft,
            propose_backlog_items,
            promote_next_backlog_item,
        )
        propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            drafts=[BacklogDraft(
                source="operator",
                title="Add tests",
                rationale="coverage",
                acceptance_criteria=["tests pass"],
                verification_commands=["pytest"],
            )],
        )
        task_ids = promote_next_backlog_item(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            repo_path=self.repo,
        )
        self.assertEqual(len(task_ids), 1)
        # Verify the task exists in the tasks table.
        row = self.conn.execute(
            "select title from tasks where id = ?", (task_ids[0],),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Add tests")

    def test_backlog_promote_marks_item_admitted(self) -> None:
        """After promotion, the backlog item status becomes 'admitted'."""
        from local_cli_coordinator.autonomous_backlog import (
            BacklogDraft,
            propose_backlog_items,
            promote_next_backlog_item,
        )
        ids = propose_backlog_items(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            drafts=[BacklogDraft(
                source="operator", title="Task X", rationale="r",
                acceptance_criteria=["c"], verification_commands=[],
            )],
        )
        promote_next_backlog_item(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            repo_path=self.repo,
        )
        row = self.conn.execute(
            "select status from project_backlog_items where id = ?",
            (ids[0],),
        ).fetchone()
        self.assertEqual(row["status"], "admitted")

    def test_backlog_promote_respects_max_items(self) -> None:
        """promote_next_backlog_item respects max_items cap."""
        from local_cli_coordinator.autonomous_backlog import (
            BacklogDraft,
            propose_backlog_items,
            promote_next_backlog_item,
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
        task_ids = promote_next_backlog_item(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            repo_path=self.repo,
            max_items=2,
        )
        self.assertEqual(len(task_ids), 2)
