import tempfile
import unittest
from pathlib import Path

from tests.helpers import init_git_repo, run
from local_cli_coordinator.discovery import (
    discover_git_recent_commits,
    list_findings,
    load_cursor,
)


class GitDiscoveryTests(unittest.TestCase):
    def _commit(self, repo: Path, filename: str, message: str) -> str:
        (repo / filename).write_text(f"{filename}\n")
        run("git", "add", filename, cwd=repo)
        result = run("git", "commit", "-m", message, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        rev = run("git", "rev-parse", "HEAD", cwd=repo)
        self.assertEqual(rev.returncode, 0, rev.stderr)
        return rev.stdout.strip()

    def test_produces_findings_with_commit_hash_and_subject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            self._commit(repo, "one.txt", "add one")
            self._commit(repo, "two.txt", "add two")

            findings = discover_git_recent_commits(
                root=root,
                source_id="recent_commits",
                repo_id="demo",
                repo_path=repo,
                enabled_repos={"demo": True},
            )

            self.assertEqual(len(findings), 2)
            self.assertEqual(findings[0].repo, "demo")
            self.assertEqual(findings[0].source, "recent_commits")
            self.assertIn("subject=add one", findings[0].evidence)
            self.assertIn("commit=", findings[0].evidence)
            self.assertIn("subject=add two", findings[1].evidence)

    def test_cursor_prevents_rediscovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            self._commit(repo, "one.txt", "add one")

            first = discover_git_recent_commits(
                root=root,
                source_id="recent_commits",
                repo_id="demo",
                repo_path=repo,
                enabled_repos={"demo": True},
            )
            self.assertEqual(len(first), 1)

            second = discover_git_recent_commits(
                root=root,
                source_id="recent_commits",
                repo_id="demo",
                repo_path=repo,
                enabled_repos={"demo": True},
            )
            self.assertEqual(second, [])

            self._commit(repo, "two.txt", "add two")
            third = discover_git_recent_commits(
                root=root,
                source_id="recent_commits",
                repo_id="demo",
                repo_path=repo,
                enabled_repos={"demo": True},
            )
            self.assertEqual(len(third), 1)
            self.assertIn("subject=add two", third[0].evidence)
            cursor = load_cursor(root, "recent_commits", "demo")
            self.assertIsNotNone(cursor)
            self.assertIn(cursor, third[0].evidence)

    def test_respects_repo_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            self._commit(repo, "one.txt", "add one")

            enabled = discover_git_recent_commits(
                root=root,
                source_id="recent_commits",
                repo_id="demo",
                repo_path=repo,
                enabled_repos={"demo": True},
            )
            disabled = discover_git_recent_commits(
                root=root,
                source_id="recent_commits",
                repo_id="demo",
                repo_path=repo,
                enabled_repos={"demo": False},
            )
            missing = discover_git_recent_commits(
                root=root,
                source_id="recent_commits",
                repo_id="demo",
                repo_path=repo,
                enabled_repos={"other": True},
            )

            self.assertEqual(len(enabled), 1)
            self.assertEqual(disabled, [])
            self.assertEqual(missing, [])

    def test_persists_findings_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            self._commit(repo, "one.txt", "add one")

            discover_git_recent_commits(
                root=root,
                source_id="recent_commits",
                repo_id="demo",
                repo_path=repo,
                enabled_repos={"demo": True},
                persist=True,
            )

            loaded = list_findings(root)
            self.assertEqual(len(loaded), 1)
            self.assertIn("subject=add one", loaded[0].evidence)