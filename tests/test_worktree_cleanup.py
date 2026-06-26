import sqlite3
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db, MIGRATIONS_DIR
from local_cli_coordinator.gitops import create_worktree, list_worktrees
from tests.helpers import init_git_repo, run_cli


def _write_config(root: Path, repo_path: Path) -> None:
    config_dir = root / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "agents.toml").write_text("[agents]\n")
    (config_dir / "repos.toml").write_text(
        "[repos.demo]\n"
        f'path = "{repo_path}"\n'
        'default_branch = "main"\n'
        'branch_prefix = "coord/"\n'
        "allow_push = false\n"
        'merge_policy = "no_push"\n'
    )
    (config_dir / "policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = true
        require_acceptance_criteria = true
        require_verification_commands = true
        require_handoff_summary = false
        max_files_touched = 5
        max_expected_minutes = 30
        max_attempts = 3
        split_if_touches_multiple_subsystems = true
        split_if_research_and_code_are_mixed = true
    """).strip())


def _init_db_with_task(root: Path, task_id: str, state: str) -> None:
    db_path = root / "coordinator.db"
    conn = connect(db_path)
    init_db(conn, MIGRATIONS_DIR)
    conn.execute(
        """
        insert into tasks(id, title, repo, state, priority, capabilities,
            source_path, goal, acceptance_criteria, verification_commands)
        values (?, 'Test', 'demo', ?, 'normal', '[]', '', 'goal', '[]', '[]')
        """,
        (task_id, state),
    )
    conn.commit()
    conn.close()


class WorktreeCleanupTests(unittest.TestCase):
    def test_cleanup_worktrees_no_config_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "repo", "cleanup-worktrees")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error", result.stderr.lower())

    def test_cleanup_skips_active_task_worktree(self) -> None:
        """Active (ready/running) task worktrees must not be removed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            _write_config(root, repo)
            _init_db_with_task(root, "task-active1", "ready")

            worktrees_root = root / "worktrees" / "demo"
            create_worktree(
                repo_path=repo,
                worktrees_root=worktrees_root,
                task_id="task-active1",
                branch_name="coord/task-active1",
            )

            result = run_cli("--root", str(root), "repo", "cleanup-worktrees")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("skip (task ready)", result.stdout)
            self.assertIn("removed: 0", result.stdout)

    def test_cleanup_removes_completed_clean_worktree(self) -> None:
        """Completed task with clean worktree should be removed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            _write_config(root, repo)
            _init_db_with_task(root, "task-done1", "done")

            worktrees_root = root / "worktrees" / "demo"
            create_worktree(
                repo_path=repo,
                worktrees_root=worktrees_root,
                task_id="task-done1",
                branch_name="coord/task-done1",
            )

            result = run_cli("--root", str(root), "repo", "cleanup-worktrees")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("removed: 1", result.stdout)

    def test_cleanup_skips_completed_dirty_worktree_without_force(self) -> None:
        """Completed task with dirty worktree is skipped without --force."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            _write_config(root, repo)
            _init_db_with_task(root, "task-dirty1", "done")

            worktrees_root = root / "worktrees" / "demo"
            wt_path = create_worktree(
                repo_path=repo,
                worktrees_root=worktrees_root,
                task_id="task-dirty1",
                branch_name="coord/task-dirty1",
            )
            (wt_path / "uncommitted.txt").write_text("dirty\n")

            result = run_cli("--root", str(root), "repo", "cleanup-worktrees")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("skip (uncommitted changes)", result.stdout)
            self.assertIn("removed: 0", result.stdout)

    def test_cleanup_skips_failed_task_worktree_even_with_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            _write_config(root, repo)
            _init_db_with_task(root, "task-failed1", "failed")

            worktrees_root = root / "worktrees" / "demo"
            create_worktree(
                repo_path=repo,
                worktrees_root=worktrees_root,
                task_id="task-failed1",
                branch_name="coord/task-failed1",
            )

            result = run_cli(
                "--root", str(root), "repo", "cleanup-worktrees", "--force"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("skip (task failed)", result.stdout)
            self.assertIn("removed: 0", result.stdout)

    def test_cleanup_force_removes_completed_dirty_worktree(self) -> None:
        """--force removes a done task's dirty worktree."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            _write_config(root, repo)
            _init_db_with_task(root, "task-dirty2", "done")

            worktrees_root = root / "worktrees" / "demo"
            wt_path = create_worktree(
                repo_path=repo,
                worktrees_root=worktrees_root,
                task_id="task-dirty2",
                branch_name="coord/task-dirty2",
            )
            (wt_path / "uncommitted.txt").write_text("dirty\n")

            result = run_cli(
                "--root", str(root), "repo", "cleanup-worktrees", "--force"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("removed: 1", result.stdout)

    def test_cleanup_reports_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            _write_config(root, repo)

            result = run_cli("--root", str(root), "repo", "cleanup-worktrees")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed:", result.stdout)
        self.assertIn("skipped:", result.stdout)
        self.assertIn("errors:", result.stdout)


if __name__ == "__main__":
    unittest.main()
