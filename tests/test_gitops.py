import tempfile
import unittest
import os
from pathlib import Path

from tests.helpers import init_git_repo, run
from local_cli_coordinator.gitops import (
    collect_changed_files,
    commit_all,
    create_worktree,
    diff_patch,
    is_git_repo,
    push_branch,
)
from local_cli_coordinator.reporting import ExecutionEvent


class RecordingReporter:
    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)


class GitOpsTests(unittest.TestCase):
    def test_create_worktree_resolves_relative_roots_before_git_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_cwd = Path.cwd()
            root = Path(tmp)
            try:
                os.chdir(root)
                repo = Path("repo")
                init_git_repo(repo)

                worktree = create_worktree(
                    repo_path=repo,
                    worktrees_root=Path("worktrees"),
                    task_id="task-relative",
                    branch_name="coord/task-relative",
                )

                self.assertTrue(worktree.is_absolute())
                self.assertEqual(worktree.parent, (root / "worktrees").resolve())
                self.assertTrue(is_git_repo(worktree))
            finally:
                os.chdir(original_cwd)

    def test_create_worktree_and_collect_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            worktrees = root / "worktrees"
            init_git_repo(repo)

            worktree = create_worktree(
                repo_path=repo,
                worktrees_root=worktrees,
                task_id="task-abc",
                branch_name="coord/task-abc-demo",
            )

            self.assertTrue(is_git_repo(worktree))
            (worktree / "feature.txt").write_text("hello\n")
            self.assertEqual(collect_changed_files(worktree), ["feature.txt"])
            self.assertIn("feature.txt", diff_patch(worktree))

    def test_changed_files_and_patch_include_rename_delete_and_untracked_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            worktrees = root / "worktrees"
            init_git_repo(repo)

            (repo / "old name.txt").write_text("old\n")
            run("git", "add", "old name.txt", cwd=repo)
            commit = run("git", "commit", "-m", "add old spaced file", cwd=repo)
            self.assertEqual(commit.returncode, 0, commit.stderr)

            worktree = create_worktree(
                repo_path=repo,
                worktrees_root=worktrees,
                task_id="task-audit",
                branch_name="coord/task-audit-demo",
            )

            rename = run("git", "mv", "old name.txt", "new name.txt", cwd=worktree)
            self.assertEqual(rename.returncode, 0, rename.stderr)
            (worktree / "README.md").unlink()
            (worktree / "new file.txt").write_text("new\n")

            self.assertEqual(
                collect_changed_files(worktree),
                ["README.md", "new file.txt", "new name.txt", "old name.txt"],
            )

            patch = diff_patch(worktree)
            self.assertIn("diff --git a/README.md b/README.md", patch)
            self.assertIn("deleted file mode", patch)
            self.assertIn("diff --git a/old name.txt b/new name.txt", patch)
            self.assertIn("diff --git a/new file.txt b/new file.txt", patch)

    def test_pipeline_git_commands_emit_reporter_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            worktrees = root / "worktrees"
            init_git_repo(repo)
            reporter = RecordingReporter()

            worktree = create_worktree(
                repo_path=repo,
                worktrees_root=worktrees,
                task_id="task-report",
                branch_name="coord/task-report",
                reporter=reporter,
            )
            (worktree / "feature.txt").write_text("hello\n")
            commit_hash = commit_all(
                worktree,
                "add feature",
                reporter=reporter,
                task_id="task-report",
            )

            started = [event for event in reporter.events if event.kind == "started"]
            completed = [event for event in reporter.events if event.kind == "completed"]
            self.assertGreaterEqual(len(started), 2)
            self.assertGreaterEqual(len(completed), 2)
            self.assertTrue(all(event.stage == "git" for event in started))
            self.assertTrue(all(event.task_id == "task-report" for event in started))
            self.assertTrue(any("worktree add" in event.command for event in started))
            self.assertTrue(any("commit" in event.command for event in started))
            self.assertTrue(all(event.exit_code == 0 for event in completed))
            self.assertTrue(all(event.elapsed_seconds >= 0 for event in completed))
            self.assertTrue(commit_hash)

    def test_failed_push_preserves_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            reporter = RecordingReporter()
            worktree = create_worktree(
                repo_path=repo,
                worktrees_root=root / "worktrees",
                task_id="task-fail",
                branch_name="coord/task-fail",
                reporter=reporter,
            )
            (worktree / "feature.txt").write_text("hello\n")
            commit_all(worktree, "add feature", reporter=reporter, task_id="task-fail")

            with self.assertRaises(RuntimeError):
                push_branch(
                    worktree,
                    "missing-remote",
                    "coord/task-fail",
                    reporter=reporter,
                    task_id="task-fail",
                )

            self.assertTrue(worktree.is_dir())
            self.assertTrue((worktree / "feature.txt").exists())
            failed = [event for event in reporter.events if event.kind == "completed" and event.exit_code != 0]
            self.assertTrue(failed)
