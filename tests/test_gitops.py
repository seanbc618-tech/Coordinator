import tempfile
import unittest
from pathlib import Path

from tests.helpers import init_git_repo
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
