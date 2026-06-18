import tempfile
import unittest
from pathlib import Path

from tests.helpers import run_cli


class WorktreeCleanupTests(unittest.TestCase):
    def test_cleanup_worktrees_no_config_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", str(tmp), "repo", "cleanup-worktrees")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error", result.stderr.lower())

    def test_cleanup_worktrees_reports_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "agents.toml").write_text("[agents]\n")
            (config_dir / "repos.toml").write_text(
                "[repos.demo]\n"
                f'path = "{root / "repo"}"\n'
                'default_branch = "main"\n'
                'branch_prefix = "coord/"\n'
                "allow_push = false\n"
                'merge_policy = "no_push"\n'
            )
            (config_dir / "policy.toml").write_text(
                "[task_policy]\n"
                "require_single_repo = true\n"
                "require_acceptance_criteria = true\n"
                "require_verification_commands = true\n"
                "require_handoff_summary = true\n"
                "max_files_touched = 5\n"
                "max_expected_minutes = 30\n"
                "max_attempts = 3\n"
                "split_if_touches_multiple_subsystems = true\n"
                "split_if_research_and_code_are_mixed = true\n"
            )
            # Create a bare repo so list_worktrees doesn't fail
            repo = root / "repo"
            repo.mkdir()
            import subprocess
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)

            result = run_cli("--root", str(root), "repo", "cleanup-worktrees")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed:", result.stdout)
        self.assertIn("skipped:", result.stdout)
        self.assertIn("errors:", result.stdout)
