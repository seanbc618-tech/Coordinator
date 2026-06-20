"""Tests for safe legacy state migration."""

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

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

    def test_dry_run_validates_on_temp_copy(self) -> None:
        """dry_run should run init_db on a temp copy, not the source."""
        source_hash = _hash_file(self.legacy / "coordinator.db")
        result = migrate_legacy_root(self.legacy, self.paths, dry_run=True)
        self.assertEqual(result.status, "dry_run")
        # Source DB hash must be unchanged
        self.assertEqual(_hash_file(self.legacy / "coordinator.db"), source_hash)

    def test_dry_run_detects_corrupt_db(self) -> None:
        """dry_run should raise on a corrupt database."""
        (self.legacy / "coordinator.db").write_bytes(b"not a database")
        with self.assertRaises(Exception):
            migrate_legacy_root(self.legacy, self.paths, dry_run=True)

    def test_dry_run_empty_source(self) -> None:
        """dry_run on source without DB should succeed silently."""
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        result = migrate_legacy_root(empty, self.paths, dry_run=True)
        self.assertEqual(result.status, "dry_run")

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

    def test_rollback_deletes_newly_created_dirs(self) -> None:
        """If migration fails, dirs that didn't exist before should be deleted."""
        with patch(
            "local_cli_coordinator.global_migration._validate_staged_database",
            side_effect=RuntimeError("simulated validation failure"),
        ):
            with self.assertRaises(RuntimeError):
                migrate_legacy_root(self.legacy, self.paths)

        # None of the target dirs should exist (they were all new)
        self.assertFalse(self.paths.config_dir.exists())
        self.assertFalse(self.paths.data_dir.exists())
        self.assertFalse(self.paths.state_dir.exists())

    def test_rollback_preserves_preexisting_dirs(self) -> None:
        """If target dirs existed before migration, rollback restores them."""
        # Pre-populate config dir
        self.paths.create()
        (self.paths.config_dir / "existing.txt").write_text("original")

        with patch(
            "local_cli_coordinator.global_migration._validate_staged_database",
            side_effect=RuntimeError("simulated validation failure"),
        ):
            with self.assertRaises(RuntimeError):
                migrate_legacy_root(self.legacy, self.paths)

        # Config dir should be restored with original content
        self.assertTrue(self.paths.config_dir.exists())
        self.assertEqual(
            (self.paths.config_dir / "existing.txt").read_text(), "original"
        )

    def test_first_dir_promote_then_failure_rolls_back(self) -> None:
        """If promote succeeds for first dir but fails later, rollback works."""
        # Pre-populate so we have something to back up
        self.paths.create()
        (self.paths.data_dir / "marker.txt").write_text("pre-existing")

        original_write = Path.write_text
        write_count = 0

        def failing_write(self_path, *args, **kwargs):
            nonlocal write_count
            write_count += 1
            # Let staging writes through, fail on marker write
            if str(self_path).endswith(".migrated"):
                raise OSError("simulated disk full")
            return original_write(self_path, *args, **kwargs)

        with patch.object(Path, "write_text", failing_write):
            with self.assertRaises(OSError):
                migrate_legacy_root(self.legacy, self.paths)

        # Pre-existing data should be restored
        self.assertTrue(self.paths.data_dir.exists())
        self.assertEqual(
            (self.paths.data_dir / "marker.txt").read_text(), "pre-existing"
        )

    def test_empty_target_succeeds(self) -> None:
        """Migration to completely empty target should succeed."""
        self.assertFalse(self.dest.exists())
        result = migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual(result.status, "migrated")
        self.assertTrue(self.paths.database.exists())
        self.assertTrue(self.paths.config_dir.exists())


def _hash_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()
