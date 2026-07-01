"""Red tests for Phase 12 PR health watcher.

Owner: Grok (Phase 12 Task 0)
Expected before implementation: missing pr_watcher module and migration 022 tables.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, init_db
from tests.helpers import ROOT, init_git_repo

_FAKE_GH_PATH = ROOT / "tests" / "fixtures" / "fake_gh.py"


def _base_config(repo_path: Path, *, allow_push: bool = False) -> CoordinatorConfig:
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
                path=repo_path,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=allow_push,
                merge_policy="push_branch_only" if allow_push else "no_push",
                verify_commands=["true"],
                review_policy="tests_only",
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
        ),
    )


class PrWatcherTests(unittest.TestCase):
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
        self.task_id = create_task(
            self.conn,
            title="PR watch task",
            repo="demo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        self.conn.commit()
        self.config = _base_config(self.repo)
        self.env = os.environ.copy()
        self.env["GH_FAKE_SCENARIO"] = json.dumps(
            {
                "pr_number": 42,
                "pr_view": {
                    "number": 42,
                    "title": "Coordinator delivery",
                    "url": "https://github.com/example/coordinator/pull/42",
                    "state": "OPEN",
                    "headRefName": "coord/task-1",
                    "baseRefName": "main",
                },
                "checks": [
                    {"name": "unit", "state": "SUCCESS", "bucket": "pass"},
                    {"name": "lint", "state": "SUCCESS", "bucket": "pass"},
                ],
            }
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _delivery(self):
        from local_cli_coordinator import github_delivery

        return github_delivery.create_delivery_record(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            repo_id="demo",
            branch_name="coord/task-1",
            base_branch="main",
            status="open",
            pr_number=42,
            pr_url="https://github.com/example/coordinator/pull/42",
        )

    def test_watch_records_pr_health_for_delivery(self) -> None:
        from local_cli_coordinator.pr_watcher import watch_delivery_pr_health

        record = self._delivery()
        self.conn.commit()
        health = watch_delivery_pr_health(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            gh_executable=sys.executable,
            gh_prefix=[str(_FAKE_GH_PATH)],
            env=self.env,
        )
        self.assertEqual(health.project_id, self.project_id)
        self.assertEqual(health.delivery_id, record.id)
        self.assertEqual(health.pr_number, 42)
        self.assertIn(health.status, {"observed", "healthy", "ready"})

    def test_watch_marks_stale_when_base_advanced(self) -> None:
        from local_cli_coordinator.pr_watcher import watch_delivery_pr_health

        run = __import__("subprocess").run
        run(["git", "checkout", "-b", "coord/task-1"], cwd=self.repo, check=True)
        run(["git", "checkout", "main"], cwd=self.repo, check=True)
        record = self._delivery()
        self.conn.commit()
        (self.repo / "README.md").write_text("main advanced\n")
        run(["git", "add", "README.md"], cwd=self.repo, check=True)
        run(["git", "commit", "-m", "advance main"], cwd=self.repo, check=True)
        health = watch_delivery_pr_health(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            gh_executable=sys.executable,
            gh_prefix=[str(_FAKE_GH_PATH)],
            env=self.env,
        )
        self.assertTrue(health.stale)
        self.assertEqual(health.status, "stale")

    def test_watch_records_healing_attempt(self) -> None:
        from local_cli_coordinator.pr_watcher import watch_delivery_pr_health

        record = self._delivery()
        self.conn.commit()
        watch_delivery_pr_health(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            gh_executable=sys.executable,
            gh_prefix=[str(_FAKE_GH_PATH)],
            env=self.env,
        )
        row = self.conn.execute(
            """
            select action, status from pr_healing_attempts
            where project_id = ? and delivery_id = ?
            order by created_at desc limit 1
            """,
            (self.project_id, record.id),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["action"], "watch")
        self.assertEqual(row["status"], "succeeded")

    def test_watch_tolerates_missing_gh_with_operator_item(self) -> None:
        from local_cli_coordinator.operator_inbox import list_operator_items
        from local_cli_coordinator.pr_watcher import watch_delivery_pr_health

        record = self._delivery()
        self.conn.commit()
        broken_env = dict(self.env)
        broken_env["GH_FAKE_SCENARIO"] = json.dumps({"exit_code": 127, "stderr": "gh missing"})
        health = watch_delivery_pr_health(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            gh_executable=sys.executable,
            gh_prefix=[str(_FAKE_GH_PATH)],
            env=broken_env,
        )
        self.assertEqual(health.status, "observed")
        items = list_operator_items(self.conn, project_id=self.project_id)
        self.assertTrue(any(item.source_type == "delivery" for item in items))

    def test_watch_rejects_cross_project_delivery(self) -> None:
        from local_cli_coordinator.pr_watcher import watch_delivery_pr_health

        record = self._delivery()
        self.conn.commit()
        with self.assertRaises(ValueError):
            watch_delivery_pr_health(
                self.conn,
                config=self.config,
                project_id="other-project",
                delivery_id=record.id,
                gh_executable=sys.executable,
                gh_prefix=[str(_FAKE_GH_PATH)],
                env=self.env,
            )


if __name__ == "__main__":
    unittest.main()