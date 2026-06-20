"""Tests for safe legacy state migration."""

import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.global_migration import (
    MigrationResult,
    migrate_legacy_root,
    _read_journal,
    _journal_path,
)
from local_cli_coordinator.runtime_paths import RuntimePaths


class MigrateLegacyRootTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.legacy = Path(self.tmp.name) / "legacy"
        self.legacy.mkdir()
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

    # --- dry_run ---

    def test_dry_run_does_not_write(self) -> None:
        result = migrate_legacy_root(self.legacy, self.paths, dry_run=True)
        self.assertEqual(result.status, "dry_run")
        self.assertFalse(self.dest.exists())

    def test_dry_run_validates_on_temp_copy(self) -> None:
        source_hash = _hash_file(self.legacy / "coordinator.db")
        migrate_legacy_root(self.legacy, self.paths, dry_run=True)
        self.assertEqual(_hash_file(self.legacy / "coordinator.db"), source_hash)

    def test_dry_run_detects_corrupt_db(self) -> None:
        (self.legacy / "coordinator.db").write_bytes(b"not a database")
        with self.assertRaises(Exception):
            migrate_legacy_root(self.legacy, self.paths, dry_run=True)

    def test_dry_run_empty_source(self) -> None:
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        result = migrate_legacy_root(empty, self.paths, dry_run=True)
        self.assertEqual(result.status, "dry_run")

    def test_dry_run_no_db_but_has_config(self) -> None:
        """Source with config but no DB should succeed in dry_run."""
        no_db = Path(self.tmp.name) / "nodb"
        no_db.mkdir()
        (no_db / "config").mkdir()
        result = migrate_legacy_root(no_db, self.paths, dry_run=True)
        self.assertEqual(result.status, "dry_run")

    # --- basic migration ---

    def test_migrate_copies_database(self) -> None:
        result = migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual(result.status, "migrated")
        self.assertTrue(self.paths.database.exists())
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
        migrate_legacy_root(self.legacy, self.paths)
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

    def test_empty_target_succeeds(self) -> None:
        self.assertFalse(self.dest.exists())
        result = migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual(result.status, "migrated")
        self.assertTrue(self.paths.database.exists())

    # --- rollback ---

    def test_rollback_deletes_newly_created_dirs(self) -> None:
        with patch(
            "local_cli_coordinator.global_migration._validate_staged_database",
            side_effect=RuntimeError("simulated validation failure"),
        ):
            with self.assertRaises(RuntimeError):
                migrate_legacy_root(self.legacy, self.paths)

        self.assertFalse(self.paths.config_dir.exists())
        self.assertFalse(self.paths.data_dir.exists())
        self.assertFalse(self.paths.state_dir.exists())

    def test_rollback_preserves_preexisting_dirs(self) -> None:
        self.paths.create()
        (self.paths.config_dir / "existing.txt").write_text("original")

        with patch(
            "local_cli_coordinator.global_migration._validate_staged_database",
            side_effect=RuntimeError("simulated validation failure"),
        ):
            with self.assertRaises(RuntimeError):
                migrate_legacy_root(self.legacy, self.paths)

        self.assertTrue(self.paths.config_dir.exists())
        self.assertEqual(
            (self.paths.config_dir / "existing.txt").read_text(), "original"
        )

    # --- journal ---

    def test_journal_cleared_on_success(self) -> None:
        migrate_legacy_root(self.legacy, self.paths)
        self.assertFalse(_journal_path(self.paths).exists())

    def test_journal_cleared_on_failure(self) -> None:
        with patch(
            "local_cli_coordinator.global_migration._validate_staged_database",
            side_effect=RuntimeError("simulated"),
        ):
            with self.assertRaises(RuntimeError):
                migrate_legacy_root(self.legacy, self.paths)
        self.assertFalse(_journal_path(self.paths).exists())

    def test_journal_source_mismatch_on_resume(self) -> None:
        """Resume from different source must raise."""
        migrate_legacy_root(self.legacy, self.paths)

        # Manually write a journal with wrong source
        journal_path = _journal_path(self.paths)
        journal_path.write_text('{"source": "/wrong/source", "backup_path": "", '
                                '"existed_before": {}, "completed_steps": [], '
                                '"staging_map": {}, "timestamp": ""}')

        # Clear marker so it tries to migrate
        marker = self.paths.data_dir / ".migrated"
        marker.unlink(missing_ok=True)

        # Delete live dirs so marker check doesn't block
        import shutil
        for d in [self.paths.data_dir]:
            if d.exists():
                shutil.rmtree(d)

        with self.assertRaises(RuntimeError) as ctx:
            migrate_legacy_root(self.legacy, self.paths)
        self.assertIn("different source", str(ctx.exception))

    def test_corrupt_journal_raises(self) -> None:
        """Corrupt journal must raise, not silently ignore."""
        self.paths.create()
        jp = _journal_path(self.paths)
        jp.write_text("not valid json {{{")

        with self.assertRaises(RuntimeError) as ctx:
            migrate_legacy_root(self.legacy, self.paths)
        self.assertIn("corrupt", str(ctx.exception))

    # --- atomic promote ---

    def test_promote_uses_rename(self) -> None:
        import local_cli_coordinator.global_migration as gm
        calls = []
        original = gm._atomic_rename

        def tracking(src, dst):
            calls.append(str(dst))
            return original(src, dst)

        with patch.object(gm, "_atomic_rename", tracking):
            migrate_legacy_root(self.legacy, self.paths)

        self.assertTrue(len(calls) >= 2)

    # --- crash recovery ---

    def test_marker_written_journal_not_cleared_recovers(self) -> None:
        """Simulate crash after marker write but before journal cleanup.

        Next call should detect marker+journal agree, clean up, return
        already_migrated.
        """
        import json
        import local_cli_coordinator.global_migration as gm

        # Do a normal migration
        migrate_legacy_root(self.legacy, self.paths)

        # Verify clean state
        self.assertFalse(_journal_path(self.paths).exists())
        self.assertTrue((self.paths.data_dir / ".migrated").exists())

        # Simulate crash: write a journal that matches the marker
        marker = (self.paths.data_dir / ".migrated").read_text()
        source_line = [l for l in marker.splitlines() if l.startswith("source:")]
        marker_source = source_line[0].split(":", 1)[1].strip()

        journal_data = {
            "source": marker_source,
            "backup_path": "backup-test",
            "existed_before": {"config": True, "data": True, "state": True},
            "completed_steps": ["promote:config", "promote:data", "promote:state"],
            "staging_map": {},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        _journal_path(self.paths).write_text(json.dumps(journal_data))

        # Also create a leftover staging dir
        staging = self.paths.data_dir.parent / ".coord-staging-data"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "marker.txt").write_text("leftover")

        # Next call should recover
        result = migrate_legacy_root(self.legacy, self.paths)
        self.assertEqual(result.status, "already_migrated")

        # Journal and staging should be cleaned up
        self.assertFalse(_journal_path(self.paths).exists())
        self.assertFalse(staging.exists())


class MigrateCliIntegrationTest(TestCase):
    """Subprocess integration tests for coordinator migrate."""

    def setUp(self) -> None:
        import os
        import sys
        from tests.helpers import ROOT, SRC

        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name) / "home"
        self.legacy = Path(self._tmpdir.name) / "legacy"
        self.legacy.mkdir()

        # Create minimal legacy
        db = connect(self.legacy / "coordinator.db")
        init_db(db)
        db.close()
        (self.legacy / "config").mkdir()
        (self.legacy / "config" / "agents.toml").write_text("[agents]\n")

        self._env = os.environ.copy()
        self._env["PYTHONPATH"] = str(SRC)
        self._env["COORDINATOR_HOME"] = str(self.home)
        self._root = ROOT

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        import subprocess
        import sys
        return subprocess.run(
            [sys.executable, "-m", "local_cli_coordinator", *args],
            cwd=self._root,
            env=self._env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_migrate_yes(self) -> None:
        result = self._run("migrate", "--source", str(self.legacy), "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("migrated", result.stdout)

    def test_migrate_dry_run(self) -> None:
        result = self._run("migrate", "--source", str(self.legacy), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry_run", result.stdout)

    def test_migrate_refuses_without_yes(self) -> None:
        result = self._run("migrate", "--source", str(self.legacy))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Refusing", result.stdout)

    def test_migrate_missing_source(self) -> None:
        result = self._run("migrate", "--source", "/nonexistent", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)

    def test_migrate_idempotent(self) -> None:
        r1 = self._run("migrate", "--source", str(self.legacy), "--yes")
        self.assertEqual(r1.returncode, 0)
        r2 = self._run("migrate", "--source", str(self.legacy), "--yes")
        self.assertEqual(r2.returncode, 0)
        self.assertIn("already_migrated", r2.stdout)


import subprocess as _subprocess


def _hash_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()
