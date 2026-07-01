"""Phase 15 red tests: fleet scan and selective rollout.

Owner: Grok (Phase 15 Task 0)
Expected before implementation: fleet_rollout module missing.
"""

from __future__ import annotations

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config_runtime import load_config_for_paths
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


def _write_config(config_dir: Path, repo_path: Path, repo_id: str = "registered") -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text(textwrap.dedent("""
        [agents.worker]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"
    """).strip())
    (config_dir / "repos.toml").write_text(textwrap.dedent(f"""
        [repos.{repo_id}]
        path = "{repo_path}"
        default_branch = "main"
        allow_push = false
        merge_policy = "no_push"
        review_policy = "tests_only"
    """).strip())
    (config_dir / "policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = false
        require_acceptance_criteria = false
        require_verification_commands = false
        require_handoff_summary = false
        max_files_touched = 20
        max_expected_minutes = 60
        max_attempts = 3
        split_if_touches_multiple_subsystems = false
        split_if_research_and_code_are_mixed = false
    """).strip())


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


class FleetRolloutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.fleet_root = self.tmp / "fleet"
        self.fleet_root.mkdir()
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)

        self.repo_a = self.fleet_root / "repo-a"
        init_git_repo(self.repo_a)
        (self.repo_a / "pyproject.toml").write_text("[project]\nname='a'\n")
        _commit_all(self.repo_a, "python a")

        self.repo_b = self.fleet_root / "repo-b"
        init_git_repo(self.repo_b)
        (self.repo_b / "package.json").write_text('{"name":"b","scripts":{"test":"vitest"}}')
        _commit_all(self.repo_b, "node b")

        nested = self.repo_a / "vendor" / "nested-repo"
        init_git_repo(nested)
        (nested / "README.md").write_text("nested\n")
        _commit_all(nested, "nested")

        cache_repo = self.fleet_root / "node_modules" / "cached-repo"
        init_git_repo(cache_repo)
        (cache_repo / "README.md").write_text("cache\n")
        _commit_all(cache_repo, "cache")

        self.registered = self.fleet_root / "registered"
        init_git_repo(self.registered)
        (self.registered / "pyproject.toml").write_text("[project]\nname='registered'\n")
        _commit_all(self.registered, "registered")
        _write_config(self.home / "config", self.registered, repo_id="registered")
        register_project(self.conn, inspect_project(self.registered), confirmed=True)
        self.conn.commit()
        self.config = load_config_for_paths(self.paths)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_finds_repos_and_skips_vendor_paths(self) -> None:
        from local_cli_coordinator.fleet_rollout import scan_fleet

        result = scan_fleet(self.fleet_root, conn=self.conn, max_depth=3)
        repo_ids = {item["repo_id"] for item in result["repos"]}
        self.assertIn("repo_a", repo_ids)
        self.assertIn("repo_b", repo_ids)
        self.assertNotIn("cached-repo", repo_ids)
        self.assertNotIn("nested-repo", {item["repo_id"] for item in result["repos"]})

    def test_scan_marks_registered_repos(self) -> None:
        from local_cli_coordinator.fleet_rollout import scan_fleet

        result = scan_fleet(self.fleet_root, conn=self.conn, max_depth=3)
        registered = next(
            item for item in result["repos"] if item["repo_id"] == "registered"
        )
        self.assertTrue(registered["registered"])
        unregistered = next(
            item for item in result["repos"] if item["repo_id"] == "repo_a"
        )
        self.assertFalse(unregistered["registered"])

    def test_scan_classifies_profiles_and_recommends_observe(self) -> None:
        from local_cli_coordinator.fleet_rollout import scan_fleet

        result = scan_fleet(self.fleet_root, conn=self.conn, max_depth=3)
        repo_a = next(item for item in result["repos"] if item["repo_id"] == "repo_a")
        repo_b = next(item for item in result["repos"] if item["repo_id"] == "repo_b")
        self.assertEqual(repo_a["detected_profile"], "python")
        self.assertEqual(repo_b["detected_profile"], "node")
        self.assertEqual(repo_a["recommended_preset"], "observe")
        self.assertEqual(repo_b["recommended_preset"], "observe")

    def test_scan_dry_run_writes_nothing(self) -> None:
        from local_cli_coordinator.fleet_rollout import scan_fleet

        repos_before = (self.paths.config_dir / "repos.toml").read_text()
        scan_fleet(self.fleet_root, conn=self.conn, max_depth=3)
        self.assertEqual(
            (self.paths.config_dir / "repos.toml").read_text(),
            repos_before,
        )

    def test_apply_touches_only_selected_repos(self) -> None:
        from local_cli_coordinator.fleet_rollout import apply_fleet_rollout

        result = apply_fleet_rollout(
            self.paths,
            self.conn,
            self.fleet_root,
            preset="observe",
            select=["repo-a"],
        )
        repos_text = (self.paths.config_dir / "repos.toml").read_text()
        self.assertIn("repo_a", repos_text)
        self.assertNotIn("repo_b", repos_text)
        applied_ids = {item["repo_id"] for item in result["applied"]}
        skipped_ids = {item["repo_id"] for item in result["skipped"]}
        self.assertEqual(applied_ids, {"repo-a"})
        self.assertIn("repo-b", skipped_ids)


if __name__ == "__main__":
    unittest.main()