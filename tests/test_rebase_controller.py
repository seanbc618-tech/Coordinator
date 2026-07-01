"""Red tests for Phase 12 safe PR rebase controller.

Owner: Grok (Phase 12 Task 0)
Expected before implementation: missing rebase_controller module.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, init_db
from tests.helpers import init_git_repo


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


def _git(args: list[str], *, cwd: Path) -> None:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr)


class RebaseControllerTests(unittest.TestCase):
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
            title="Rebase task",
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
        self._create_feature_branch()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_feature_branch(self) -> None:
        _git(["checkout", "-b", "coord/task-1"], cwd=self.repo)
        (self.repo / "feature.txt").write_text("feature work\n")
        _git(["add", "feature.txt"], cwd=self.repo)
        _git(["commit", "-m", "feature"], cwd=self.repo)
        _git(["checkout", "main"], cwd=self.repo)
        (self.repo / "README.md").write_text("main moved forward\n")
        _git(["add", "README.md"], cwd=self.repo)
        _git(["commit", "-m", "advance main"], cwd=self.repo)

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
            pr_number=7,
            pr_url="https://github.com/example/coordinator/pull/7",
        )

    def test_dry_run_rebase_does_not_mutate_main_worktree(self) -> None:
        from local_cli_coordinator.rebase_controller import dry_run_rebase

        record = self._delivery()
        self.conn.commit()
        before = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        result = dry_run_rebase(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            worktrees_root=self.tmp / "rebase-worktrees",
        )
        after = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(before, after)
        self.assertIn(result.status, {"succeeded", "failed", "blocked"})
        self.assertEqual(result.action, "rebase_dry_run")

    def test_apply_rebase_blocked_without_push_policy(self) -> None:
        from local_cli_coordinator.rebase_controller import apply_rebase

        record = self._delivery()
        self.conn.commit()
        result = apply_rebase(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            worktrees_root=self.tmp / "rebase-worktrees",
        )
        self.assertEqual(result.status, "blocked")

    def test_force_rebase_rejected_by_default(self) -> None:
        from local_cli_coordinator.rebase_controller import apply_rebase

        record = self._delivery()
        self.conn.commit()
        result = apply_rebase(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            worktrees_root=self.tmp / "rebase-worktrees",
            force=True,
        )
        self.assertEqual(result.status, "blocked")
        self.assertIn("force", result.error.lower())

    def test_rebase_conflict_records_recovery_not_dirty_worktree(self) -> None:
        from local_cli_coordinator.rebase_controller import dry_run_rebase

        _git(["checkout", "coord/task-1"], cwd=self.repo)
        (self.repo / "README.md").write_text("conflict on feature\n")
        _git(["add", "README.md"], cwd=self.repo)
        _git(["commit", "-m", "conflict feature"], cwd=self.repo)
        _git(["checkout", "main"], cwd=self.repo)
        record = self._delivery()
        self.conn.commit()
        result = dry_run_rebase(
            self.conn,
            config=self.config,
            project_id=self.project_id,
            delivery_id=record.id,
            worktrees_root=self.tmp / "rebase-worktrees",
        )
        self.assertEqual(result.status, "failed")
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(porcelain, "")
        row = self.conn.execute(
            "select count(*) as c from pr_healing_attempts where action = 'rebase_dry_run'",
        ).fetchone()
        self.assertGreaterEqual(int(row["c"]), 1)


if __name__ == "__main__":
    unittest.main()