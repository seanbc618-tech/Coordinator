"""Phase 15 red tests: onboarding dry-run and apply planning.

Owner: Grok (Phase 15 Task 0)
Expected before implementation: onboarding_plan module missing.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config_runtime import load_config_for_paths
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


def _write_config(config_dir: Path, repo_path: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text(textwrap.dedent("""
        [agents.worker]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"
    """).strip())
    (config_dir / "repos.toml").write_text(textwrap.dedent(f"""
        [repos.existing-repo]
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

        [notifications]
        allow_command_sink = false
    """).strip())


class OnboardingPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "new-repo"
        init_git_repo(self.repo)
        (self.repo / "pyproject.toml").write_text("[project]\nname='new-repo'\n")
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.config = load_config_for_paths(self.paths)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_plan_includes_config_diff_and_preset(self) -> None:
        from local_cli_coordinator.onboarding_plan import build_onboarding_plan

        plan = build_onboarding_plan(
            self.paths,
            self.conn,
            self.repo,
            preset="observe",
            dry_run=True,
        )
        self.assertEqual(plan["preset"], "observe")
        self.assertFalse(plan["autonomy_enabled"])
        self.assertIn("repo_entry", plan)
        self.assertIn("current_repo_entry", plan)
        self.assertIn("proposed_repo_entry", plan)
        self.assertIn("config_diff", plan)
        self.assertIn("verify_commands", plan)
        self.assertIn("warnings", plan)

    def test_dry_run_writes_no_config_files(self) -> None:
        from local_cli_coordinator.onboarding_plan import build_onboarding_plan

        repos_before = (self.paths.config_dir / "repos.toml").read_text()
        agents_before = (self.paths.config_dir / "agents.toml").read_text()
        policy_before = (self.paths.config_dir / "policy.toml").read_text()
        build_onboarding_plan(
            self.paths,
            self.conn,
            self.repo,
            preset="observe",
            dry_run=True,
        )
        self.assertEqual(
            (self.paths.config_dir / "repos.toml").read_text(),
            repos_before,
        )
        self.assertEqual(
            (self.paths.config_dir / "agents.toml").read_text(),
            agents_before,
        )
        self.assertEqual(
            (self.paths.config_dir / "policy.toml").read_text(),
            policy_before,
        )

    def test_dry_run_records_onboarding_run(self) -> None:
        from local_cli_coordinator.onboarding_plan import build_onboarding_plan

        plan = build_onboarding_plan(
            self.paths,
            self.conn,
            self.repo,
            preset="observe",
            dry_run=True,
        )
        self.conn.commit()
        row = self.conn.execute(
            "select mode, status, preset_name from onboarding_runs where id = ?",
            (plan["run_id"],),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["mode"], "dry_run")
        self.assertEqual(row["status"], "planned")
        self.assertEqual(row["preset_name"], "observe")

    def test_apply_plan_snapshots_before_write(self) -> None:
        from local_cli_coordinator.onboarding_plan import apply_onboarding_plan

        result = apply_onboarding_plan(
            self.paths,
            self.conn,
            self.repo,
            preset="observe",
        )
        self.assertIn("snapshot_id", result)
        snapshot = self.conn.execute(
            "select scope, config_dir, files_json from config_snapshots where id = ?",
            (result["snapshot_id"],),
        ).fetchone()
        self.assertIsNotNone(snapshot)
        files = json.loads(snapshot["files_json"])
        self.assertIn("repos.toml", files)
        self.assertIn("agents.toml", files)
        self.assertIn("policy.toml", files)

    def test_apply_keeps_autonomy_disabled_for_observe(self) -> None:
        from local_cli_coordinator.onboarding_plan import apply_onboarding_plan

        result = apply_onboarding_plan(
            self.paths,
            self.conn,
            self.repo,
            preset="observe",
        )
        self.assertFalse(result["autonomy_enabled"])
        row = self.conn.execute(
            "select status, preset_name from onboarding_runs where id = ?",
            (result["run_id"],),
        ).fetchone()
        self.assertEqual(row["status"], "applied")
        self.assertEqual(row["preset_name"], "observe")

    def test_apply_preserves_custom_agents_and_policy(self) -> None:
        from local_cli_coordinator.onboarding_plan import apply_onboarding_plan

        custom_agents = textwrap.dedent("""
            [agents.reviewer]
            command = "echo reviewer"
            capabilities = ["review"]
            max_concurrency = 1
            role = "reviewer"
        """).strip()
        custom_policy = textwrap.dedent("""
            [task_policy]
            require_single_repo = true
            require_acceptance_criteria = true
            require_verification_commands = true
            require_handoff_summary = true
            max_files_touched = 5
            max_expected_minutes = 15
            max_attempts = 2
            split_if_touches_multiple_subsystems = true
            split_if_research_and_code_are_mixed = true
        """).strip()
        (self.paths.config_dir / "agents.toml").write_text(custom_agents + "\n")
        (self.paths.config_dir / "policy.toml").write_text(custom_policy + "\n")
        apply_onboarding_plan(
            self.paths,
            self.conn,
            self.repo,
            preset="observe",
        )
        self.assertIn("agents.reviewer", (self.paths.config_dir / "agents.toml").read_text())
        self.assertIn(
            "require_acceptance_criteria = true",
            (self.paths.config_dir / "policy.toml").read_text(),
        )


if __name__ == "__main__":
    unittest.main()