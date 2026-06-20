"""Tests for safe legacy state migration."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.global_migration import (
    MigrationResult,
    migrate_legacy_root,
)
from local_cli_coordinator.runtime_paths import RuntimePaths


class MigrateLegacyRootTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.legacy = Path(self.tmp.name) / "legacy"
        self.legacy.mkdir()
        # Create minimal legacy structure
        db = connect(self.legacy / "coordinator.db")
        init_db(db)
        db.close()
        (self.legacy / "config").mkdir()
        (self.legacy / "config" / "agents.toml").write_text("[agents]\n")
        (self.legacy / "runs").mkdir()
        (self.legacy / "tasks").mkdir()

        self.dest = Path(self.tmp.name) / "dest"
        self.paths = RuntimePaths(
            self.dest / "config",
            self.dest / "data",
            self.dest / "state",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dry_run_does_not_write(self) -> None:
        result = migrate_legacy_root(self.legacy, self.paths, dry_run=True)
        self.assertEqual(result.status, "dry_run")
        self.assertFalse(self.dest.exists())

    def test_migrate_copies_database(self) -> None:
        result = migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual(result.status, "migrated")
        self.assertTrue(self.paths.database.exists())
        # Verify database is valid
        conn = connect(self.paths.database)
        init_db(conn)
        conn.close()

    def test_migrate_copies_config(self) -> None:
        migrate_legacy_root(self.legacy, self.paths)
        self.assertTrue((self.paths.config_dir / "agents.toml").exists())

    def test_idempotent_rerun(self) -> None:
        r1 = migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual(r1.status, "migrated")
        r2 = migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual(r2.status, "already_migrated")

    def test_backup_before_overwrite(self) -> None:
        # First migration
        migrate_legacy_root(self.legacy, self.paths)
        # Second migration with different source
        legacy2 = Path(self.tmp.name) / "legacy2"
        legacy2.mkdir()
        db = connect(legacy2 / "coordinator.db")
        init_db(db)
        db.close()
        result = migrate_legacy_root(legacy2, self.paths)
        self.assertEqual(result.status, "migrated")
        self.assertIsNotNone(result.backup_path)

    def test_missing_source_raises(self) -> None:
        missing = Path(self.tmp.name) / "missing"
        with self.assertRaises(FileNotFoundError):
            migrate_legacy_root(missing, self.paths)

    def test_preserves_original_source(self) -> None:
        original_db = (self.legacy / "coordinator.db").read_bytes()
        migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual((self.legacy / "coordinator.db").read_bytes(), original_db)

    def test_migration_result_fields(self) -> None:
        result = migrate_legacy_root(self.legacy, self.paths)
        self.assertIsInstance(result, MigrationResult)
        self.assertIn(result.status, ("migrated", "already_migrated", "dry_run"))
