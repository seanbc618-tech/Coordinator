"""Phase 10 operator inbox contract tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from tests.helpers import init_git_repo


def _config(repo: Path) -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "worker": AgentConfig(
                id="worker",
                command="true",
                capabilities=["code"],
                max_concurrency=1,
            )
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=repo,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=["true"],
            )
        },
        policy=PolicyConfig(
            require_single_repo=False,
            require_acceptance_criteria=False,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=20,
            max_expected_minutes=60,
            max_attempts=3,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
            max_task_runtime_seconds=60,
        ),
    )


class OperatorInboxPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.conn = connect(self.tmp / "data.db")
        init_db(self.conn)
        self.project_id = "proj-1"
        self.conn.execute(
            "insert into projects(id, canonical_path, repo_id) values (?, ?, ?)",
            (self.project_id, str(self.repo.resolve()), "demo"),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_upsert_operator_item_dedupes_open_items(self) -> None:
        from local_cli_coordinator.operator_inbox import upsert_operator_item

        first = upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="task",
            source_id="task-1",
            severity="warning",
            title="Awaiting human",
            dedupe_key="task:task-1:awaiting_human",
        )
        second = upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="task",
            source_id="task-1",
            severity="warning",
            title="Awaiting human (updated)",
            dedupe_key="task:task-1:awaiting_human",
        )
        self.conn.commit()
        self.assertEqual(first.id, second.id)
        rows = self.conn.execute(
            "select count(*) as cnt from operator_items where project_id = ?",
            (self.project_id,),
        ).fetchone()["cnt"]
        self.assertEqual(rows, 1)

    def test_resolve_operator_item_marks_resolved_not_deleted(self) -> None:
        from local_cli_coordinator.operator_inbox import (
            resolve_operator_item,
            upsert_operator_item,
        )

        item = upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="task",
            source_id="task-1",
            severity="info",
            title="Done",
            dedupe_key="task:task-1:done",
        )
        resolve_operator_item(self.conn, item_id=item.id)
        self.conn.commit()
        row = self.conn.execute(
            "select status, resolved_at from operator_items where id = ?",
            (item.id,),
        ).fetchone()
        self.assertEqual(row["status"], "resolved")
        self.assertIsNotNone(row["resolved_at"])


class OperatorInboxCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.conn = connect(self.tmp / "data.db")
        init_db(self.conn)
        self.project_id = "proj-1"
        self.conn.execute(
            "insert into projects(id, canonical_path, repo_id) values (?, ?, ?)",
            (self.project_id, str(self.repo.resolve()), "demo"),
        )
        from local_cli_coordinator.db import transition_task

        self.task_id = create_task(
            self.conn,
            title="Human gate task",
            repo="demo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        transition_task(self.conn, self.task_id, "awaiting_human", "needs review")
        self.conn.commit()
        self.config = _config(self.repo)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_refresh_creates_awaiting_human_item(self) -> None:
        from local_cli_coordinator.operator_inbox import list_operator_items, refresh_operator_inbox

        refresh_operator_inbox(
            self.conn,
            project_id=self.project_id,
            config=self.config,
            repo_root=self.repo,
        )
        self.conn.commit()
        items = list_operator_items(self.conn, project_id=self.project_id)
        self.assertTrue(any(item.source_type == "task" for item in items))
        self.assertTrue(all(item.project_id == self.project_id for item in items))

    def test_refresh_resolves_item_when_task_leaves_awaiting_human(self) -> None:
        from local_cli_coordinator.operator_inbox import (
            list_operator_items,
            refresh_operator_inbox,
        )
        from local_cli_coordinator.db import transition_task

        refresh_operator_inbox(
            self.conn,
            project_id=self.project_id,
            config=self.config,
            repo_root=self.repo,
        )
        self.conn.commit()
        transition_task(self.conn, self.task_id, "done", "approved")
        refresh_operator_inbox(
            self.conn,
            project_id=self.project_id,
            config=self.config,
            repo_root=self.repo,
        )
        self.conn.commit()
        open_items = list_operator_items(
            self.conn, project_id=self.project_id, status="open"
        )
        self.assertFalse(
            any(
                item.source_id == self.task_id and item.source_type == "task"
                for item in open_items
            )
        )


if __name__ == "__main__":
    unittest.main()