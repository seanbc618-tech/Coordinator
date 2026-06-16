import tempfile
import unittest
from pathlib import Path

from tests.helpers import init_git_repo, run
from local_cli_coordinator.gitops import collect_changed_files, create_worktree, diff_patch, is_git_repo


class GitOpsTests(unittest.TestCase):
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
