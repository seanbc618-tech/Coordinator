"""Phase 15 red tests: config snapshot and rollback contracts.

Owner: Grok (Phase 15 Task 0)
Expected before implementation: config_snapshots module missing.
"""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

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
        [repos.demo]
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


class ConfigSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_snapshot_captures_config_files(self) -> None:
        from local_cli_coordinator.config_snapshots import create_config_snapshot

        snapshot_id = create_config_snapshot(
            self.paths,
            self.conn,
            scope="global",
            project_id=None,
            reason="onboarding apply",
        )
        self.conn.commit()
        row = self.conn.execute(
            "select files_json, config_dir from config_snapshots where id = ?",
            (snapshot_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["config_dir"], str(self.paths.config_dir))
        files = json.loads(row["files_json"])
        for name in ("agents.toml", "repos.toml", "policy.toml"):
            self.assertIn(name, files)
            self.assertIn("[", files[name])

    def test_rollback_restores_previous_config_atomically(self) -> None:
        from local_cli_coordinator.config_snapshots import (
            create_config_snapshot,
            rollback_config_snapshot,
        )

        original_repos = (self.paths.config_dir / "repos.toml").read_text()
        snapshot_id = create_config_snapshot(
            self.paths,
            self.conn,
            scope="global",
            project_id=None,
            reason="before mutation",
        )
        (self.paths.config_dir / "repos.toml").write_text("# mutated\n")
        result = rollback_config_snapshot(self.paths, self.conn, snapshot_id)
        self.assertTrue(result["restored"])
        self.assertEqual(
            (self.paths.config_dir / "repos.toml").read_text(),
            original_repos,
        )

    def test_rollback_records_onboarding_run(self) -> None:
        from local_cli_coordinator.config_snapshots import (
            create_config_snapshot,
            rollback_config_snapshot,
        )

        snapshot_id = create_config_snapshot(
            self.paths,
            self.conn,
            scope="global",
            project_id=None,
            reason="rollback test",
        )
        (self.paths.config_dir / "policy.toml").write_text("# changed\n")
        result = rollback_config_snapshot(self.paths, self.conn, snapshot_id)
        self.conn.commit()
        row = self.conn.execute(
            "select mode, status, snapshot_id from onboarding_runs where id = ?",
            (result["run_id"],),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["mode"], "rollback")
        self.assertEqual(row["status"], "rolled_back")
        self.assertEqual(row["snapshot_id"], snapshot_id)

    def test_rollback_refuses_foreign_coordinator_home(self) -> None:
        from local_cli_coordinator.config_snapshots import (
            create_config_snapshot,
            rollback_config_snapshot,
        )

        snapshot_id = create_config_snapshot(
            self.paths,
            self.conn,
            scope="global",
            project_id=None,
            reason="foreign home test",
        )
        other_home = self.tmp / "other-home"
        other_paths = RuntimePaths(
            other_home / "config", other_home / "data", other_home / "state"
        )
        other_paths.create()
        other_conn = connect(other_paths.database)
        init_db(other_conn)
        try:
            with self.assertRaises(ValueError):
                rollback_config_snapshot(other_paths, other_conn, snapshot_id)
        finally:
            other_conn.close()

    def test_rollback_does_not_delete_project_history(self) -> None:
        from local_cli_coordinator.config_snapshots import (
            create_config_snapshot,
            rollback_config_snapshot,
        )
        from local_cli_coordinator.projects import inspect_project, register_project

        project_id = register_project(
            self.conn, inspect_project(self.repo), confirmed=True
        )
        self.conn.commit()
        snapshot_id = create_config_snapshot(
            self.paths,
            self.conn,
            scope="project",
            project_id=project_id,
            reason="preserve db history",
        )
        (self.paths.config_dir / "repos.toml").write_text("# changed\n")
        rollback_config_snapshot(self.paths, self.conn, snapshot_id)
        row = self.conn.execute(
            "select id from projects where id = ?",
            (project_id,),
        ).fetchone()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()