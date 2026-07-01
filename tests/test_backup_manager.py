"""Phase 20 backup create, verify, and restore tests."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.backup_manager import (
    apply_restore,
    check_restore_compatibility,
    create_backup,
    plan_restore,
    restore_backup,
    verify_backup,
)
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.runtime_paths import RuntimePaths


def _write_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text("[agents]\n")
    (config_dir / "repos.toml").write_text("[repos]\n")
    (config_dir / "policy.toml").write_text("[task_policy]\n")


class BackupManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths(
            self.tmp / "config",
            self.tmp / "data",
            self.tmp / "state",
        )
        self.paths.create()
        _write_config(self.paths.config_dir)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.conn.execute(
            "insert into tasks(id, title, repo, state, priority, capabilities, "
            "source_path, goal, acceptance_criteria, verification_commands, project_id) "
            "values ('task-demo', 'Demo', 'repo', 'ready', 'normal', 'code', "
            "'task.md', 'goal', 'done', 'true', 'legacy-default')"
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_backup_includes_db_config_and_checksums(self) -> None:
        result = create_backup(self.conn, self.paths)
        manifest = result["manifest"]
        self.assertIn("data/coordinator.db", manifest["files"])
        self.assertIn("config/agents.toml", manifest["files"])
        self.assertTrue(manifest["files"]["data/coordinator.db"]["sha256"])
        row = self.conn.execute(
            "select status from backup_records where id = ?",
            (result["backup_id"],),
        ).fetchone()
        self.assertEqual(row["status"], "created")

    def test_verify_backup_passes_for_valid_archive(self) -> None:
        created = create_backup(self.conn, self.paths)
        verification = verify_backup(Path(created["backup_path"]))
        self.assertTrue(verification["ok"])
        self.assertEqual(verification["status"], "verified")

    def test_verify_backup_fails_when_checksum_tampered(self) -> None:
        created = create_backup(self.conn, self.paths)
        backup_root = Path(created["backup_path"])
        manifest_path = backup_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        first_key = next(iter(manifest["files"]))
        manifest["files"][first_key]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        verification = verify_backup(backup_root)
        self.assertFalse(verification["ok"])
        self.assertTrue(verification["errors"])

    def test_restore_dry_run_writes_nothing(self) -> None:
        created = create_backup(self.conn, self.paths)
        self.paths.database.write_text("mutated", encoding="utf-8")
        plan = plan_restore(Path(created["backup_path"]), self.paths)
        self.assertEqual(plan["mode"], "dry_run")
        self.assertGreater(plan["would_restore_files"], 0)
        self.assertEqual(self.paths.database.read_text(encoding="utf-8"), "mutated")

    def test_restore_apply_restores_database(self) -> None:
        created = create_backup(self.conn, self.paths)
        self.paths.database.write_text("mutated", encoding="utf-8")
        result = apply_restore(Path(created["backup_path"]), self.paths)
        self.assertEqual(result["mode"], "apply")
        self.assertIn("data/coordinator.db", result["restored_files"])
        conn = connect(self.paths.database)
        try:
            row = conn.execute(
                "select id from tasks where id = 'task-demo'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_restore_apply_refuses_incompatible_schema(self) -> None:
        created = create_backup(self.conn, self.paths)
        backup_root = Path(created["backup_path"])
        manifest_path = backup_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_migrations"] = list(manifest["schema_migrations"]) + [
            "999_future_migration.sql"
        ]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            apply_restore(backup_root, self.paths)
        plan = plan_restore(backup_root, self.paths)
        self.assertTrue(plan["blocked"])

    def test_restore_apply_allows_force_compatible_risk(self) -> None:
        created = create_backup(self.conn, self.paths)
        backup_root = Path(created["backup_path"])
        manifest_path = backup_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_migrations"] = list(manifest["schema_migrations"]) + [
            "999_future_migration.sql"
        ]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result = apply_restore(
            backup_root,
            self.paths,
            force_compatible_risk=True,
        )
        self.assertTrue(result["forced"])
        self.assertGreater(result["restored_count"], 0)

    def test_restore_backup_defaults_to_dry_run(self) -> None:
        created = create_backup(self.conn, self.paths)
        self.paths.database.write_text("mutated", encoding="utf-8")
        result = restore_backup(Path(created["backup_path"]), self.paths)
        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(self.paths.database.read_text(encoding="utf-8"), "mutated")

    def test_check_restore_compatibility_detects_unknown_migrations(self) -> None:
        compatible, errors = check_restore_compatibility(
            {"schema_migrations": ["999_future_migration.sql"]}
        )
        self.assertFalse(compatible)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()