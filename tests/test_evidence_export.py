"""Phase 18 evidence export tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.artifact_registry import register_artifact
from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.evidence_export import export_evidence_bundle
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class EvidenceExportTests(unittest.TestCase):
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
        self.log_path = self.paths.data_dir / "runs" / "secret.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("api_key=super-secret-value\n", encoding="utf-8")
        register_artifact(
            self.conn,
            paths=self.paths,
            project_id=self.project_id,
            artifact_type="log",
            path=self.log_path,
            task_id="task-1",
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_creates_manifest_and_bundle(self) -> None:
        result = export_evidence_bundle(
            self.conn,
            paths=self.paths,
            scope="project",
            project_id=self.project_id,
        )
        self.assertEqual(result["status"], "created")
        manifest_path = Path(result["manifest_path"])
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["project_id"], self.project_id)
        self.assertEqual(manifest["file_count"], 1)
        self.assertIn("sha256", manifest["files"][0])
        self.assertIn("redaction_summary", manifest)

    def test_export_redacts_secret_values(self) -> None:
        result = export_evidence_bundle(
            self.conn,
            paths=self.paths,
            scope="project",
            project_id=self.project_id,
        )
        bundle_file = Path(result["bundle_path"]) / "files"
        exported = next(bundle_file.iterdir())
        content = exported.read_text(encoding="utf-8")
        self.assertIn("[REDACTED]", content)
        self.assertNotIn("super-secret-value", content)

    def test_export_persisted_in_database(self) -> None:
        result = export_evidence_bundle(
            self.conn,
            paths=self.paths,
            scope="project",
            project_id=self.project_id,
        )
        row = self.conn.execute(
            "select * from evidence_exports where id = ?",
            (result["export_id"],),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "created")


if __name__ == "__main__":
    unittest.main()