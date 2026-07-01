"""Phase 18 retention policy tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_cli_coordinator.artifact_registry import register_artifact
from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.retention_policy import plan_retention
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class RetentionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config",
            self.home / "data",
            self.home / "state",
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.project_id = register_project(
            self.conn, inspect_project(self.repo), confirmed=True
        )
        create_task(
            self.conn,
            title="demo",
            repo="repo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=[],
            project_id=self.project_id,
        )
        self.conn.commit()
        self.log_path = self.paths.data_dir / "runs" / "old.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("stale log\n", encoding="utf-8")
        stale_artifact = register_artifact(
            self.conn,
            paths=self.paths,
            project_id=self.project_id,
            artifact_type="log",
            path=self.log_path,
            task_id="task-1",
        )
        self.stale_artifact_id = stale_artifact.id
        stale_time = (
            datetime.now(timezone.utc) - timedelta(days=45)
        ).replace(microsecond=0).isoformat()
        self.conn.execute(
            "update artifacts set created_at = ? where id = ?",
            (stale_time, stale_artifact.id),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_default_does_not_delete_files(self) -> None:
        result = plan_retention(
            self.conn,
            paths=self.paths,
            scope="project",
            project_id=self.project_id,
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["mode"], "dry_run")
        self.assertTrue(self.log_path.is_file())
        self.assertEqual(result["plan"]["candidate_count"], 1)
        self.assertEqual(result["result"]["deleted_files"], 0)

    def test_dry_run_preserves_db_history(self) -> None:
        before = self.conn.execute("select count(*) as c from artifacts").fetchone()["c"]
        plan_retention(
            self.conn,
            paths=self.paths,
            scope="project",
            project_id=self.project_id,
        )
        after = self.conn.execute("select count(*) as c from artifacts").fetchone()["c"]
        self.assertEqual(before, after)

    def test_apply_exports_before_deleting_files(self) -> None:
        result = plan_retention(
            self.conn,
            paths=self.paths,
            scope="project",
            project_id=self.project_id,
            mode="apply",
        )
        self.assertEqual(result["mode"], "apply")
        self.assertFalse(self.log_path.is_file())
        self.assertIsNotNone(result["result"]["export_id"])
        row = self.conn.execute(
            "select count(*) as c from artifacts where id = ?",
            (result["plan"]["candidates"][0]["artifact_id"],),
        ).fetchone()
        self.assertEqual(row["c"], 1)
        run = self.conn.execute(
            "select plan_json, result_json from retention_runs where id = ?",
            (result["retention_run_id"],),
        ).fetchone()
        self.assertIsNotNone(run)
        result_json = json.loads(run["result_json"])
        self.assertEqual(result_json["deleted_files"], 1)

    def test_apply_exports_exact_deletion_candidates_not_newest(self) -> None:
        fresh_path = self.paths.data_dir / "runs" / "fresh.log"
        fresh_path.write_text("fresh log\n", encoding="utf-8")
        fresh_artifact = register_artifact(
            self.conn,
            paths=self.paths,
            project_id=self.project_id,
            artifact_type="log",
            path=fresh_path,
            task_id="task-2",
        )
        result = plan_retention(
            self.conn,
            paths=self.paths,
            scope="project",
            project_id=self.project_id,
            mode="apply",
        )

        manifest_path = Path(result["result"]["manifest_path"])
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_ids = {entry["artifact_id"] for entry in manifest["files"]}
        manifest_paths = {entry["source_path"] for entry in manifest["files"]}

        self.assertEqual(result["plan"]["candidate_count"], 1)
        self.assertIn(self.stale_artifact_id, manifest_ids)
        self.assertTrue(
            any("old.log" in path for path in manifest_paths),
            f"stale artifact path missing from manifest: {manifest_paths}",
        )
        self.assertNotIn(fresh_artifact.id, manifest_ids)
        self.assertFalse(self.log_path.is_file())
        self.assertTrue(fresh_path.is_file())
        self.assertEqual(result["result"]["deleted_files"], 1)


if __name__ == "__main__":
    unittest.main()