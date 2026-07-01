"""Phase 18 artifact registry tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.artifact_registry import (
    ArtifactRegistryError,
    canonicalize_artifact_path,
    register_artifact,
    register_task_kind_artifact,
)
from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class ArtifactRegistryTests(unittest.TestCase):
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
        self.artifact_dir = self.paths.data_dir / "runs" / "task-1"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.artifact_dir / "attempt.log"
        self.log_path.write_text("agent output\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migration_creates_warehouse_tables(self) -> None:
        tables = {
            row["name"]
            for row in self.conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertIn("artifacts", tables)
        self.assertIn("evidence_exports", tables)
        self.assertIn("retention_runs", tables)
        self.assertIn("task_artifacts", tables)

    def test_register_artifact_computes_checksum(self) -> None:
        artifact = register_artifact(
            self.conn,
            paths=self.paths,
            project_id=self.project_id,
            artifact_type="log",
            path=self.log_path,
            task_id="task-1",
        )
        self.assertTrue(artifact.sha256)
        self.assertGreater(artifact.size_bytes, 0)
        self.assertEqual(artifact.artifact_type, "log")

    def test_rejects_path_outside_controlled_roots(self) -> None:
        outside = Path("/etc/passwd")
        with self.assertRaises(ArtifactRegistryError) as ctx:
            canonicalize_artifact_path(
                outside,
                allowed_roots=[self.paths.data_dir.resolve()],
            )
        self.assertEqual(ctx.exception.code, "path_outside_roots")

    def test_register_task_kind_maps_diff_to_patch(self) -> None:
        patch_path = self.artifact_dir / "change.patch"
        patch_path.write_text("diff content\n", encoding="utf-8")
        artifact = register_task_kind_artifact(
            self.conn,
            paths=self.paths,
            project_id=self.project_id,
            task_id="task-1",
            kind="diff",
            path=patch_path,
            commit=True,
        )
        assert artifact is not None
        self.assertEqual(artifact.artifact_type, "patch")

    def test_duplicate_path_updates_existing_row(self) -> None:
        first = register_artifact(
            self.conn,
            paths=self.paths,
            project_id=self.project_id,
            artifact_type="log",
            path=self.log_path,
            task_id="task-1",
        )
        self.log_path.write_text("updated output\n", encoding="utf-8")
        second = register_artifact(
            self.conn,
            paths=self.paths,
            project_id=self.project_id,
            artifact_type="log",
            path=self.log_path,
            task_id="task-1",
        )
        self.assertEqual(first.id, second.id)
        self.assertNotEqual(first.sha256, second.sha256)


if __name__ == "__main__":
    unittest.main()