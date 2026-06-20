"""Safe legacy state migration to global Coordinator paths.

Copies database, config, runs, and tasks from a legacy single-root
installation to the global runtime directory structure.

Safety guarantees:
- Copies to a staging directory first; validates before touching live dirs.
- dry_run operates on a temporary DB copy, never touches the source.
- Promote uses same-filesystem rename for atomicity.
- Backs up all three target dirs (config, data, state) before overwrite.
- Rollback deletes directories that were newly created by this migration.
- Never deletes the source.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import connect, init_db
from .runtime_paths import RuntimePaths


@dataclass(frozen=True)
class MigrationResult:
    """Immutable result of a migration attempt."""

    status: str  # "migrated", "already_migrated", or "dry_run"
    backup_path: Path | None = None


_MARKER_FILE = ".migrated"


def _database_hash(path: Path) -> str:
    """SHA-256 hex digest of the database file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_marker(paths: RuntimePaths, source: Path, db_hash: str) -> None:
    """Write a migration marker with provenance."""
    marker = paths.data_dir / _MARKER_FILE
    marker.write_text(
        f"source: {source}\n"
        f"database_hash: {db_hash}\n"
        f"completed_at: {datetime.now(timezone.utc).isoformat()}\n"
    )


def _read_marker(paths: RuntimePaths) -> dict[str, str] | None:
    """Read migration marker if it exists."""
    marker = paths.data_dir / _MARKER_FILE
    if not marker.exists():
        return None
    result = {}
    for line in marker.read_text().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


_KNOWN_PATHS = (
    "coordinator.db",
    "config",
    "runs",
    "state",
    "tasks",
)


def _copy_known_paths(source: Path, dest_root: Path, paths: RuntimePaths) -> None:
    """Copy known legacy paths into a destination root directory."""
    for name in _KNOWN_PATHS:
        src = source / name
        if not src.exists():
            continue

        if name == "coordinator.db":
            dst = dest_root / "data" / "coordinator.db"
        elif name == "config":
            dst = dest_root / "config"
        elif name == "runs":
            dst = dest_root / "data" / "runs"
        elif name == "state":
            dst = dest_root / "state"
        elif name == "tasks":
            dst = dest_root / "data" / "tasks"
        else:
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def _validate_database_copy(db_path: Path) -> None:
    """Open a database copy and run migrations to validate."""
    conn = connect(db_path)
    try:
        init_db(conn)
    finally:
        conn.close()


def _backup_existing(paths: RuntimePaths) -> Path:
    """Back up all three target directories before overwrite."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = paths.data_dir.parent / f"backup-{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=True)

    for label, directory in [
        ("config", paths.config_dir),
        ("data", paths.data_dir),
        ("state", paths.state_dir),
    ]:
        if directory.exists():
            shutil.copytree(directory, backup_root / label)

    return backup_root


def _restore_backup(
    backup_root: Path,
    paths: RuntimePaths,
    existed_before: dict[str, bool],
) -> None:
    """Restore all three directories from a backup.

    For directories that did not exist before migration, delete them
    instead of restoring.
    """
    for label, directory in [
        ("config", paths.config_dir),
        ("data", paths.data_dir),
        ("state", paths.state_dir),
    ]:
        if directory.exists():
            shutil.rmtree(directory)

        src = backup_root / label
        if src.exists():
            shutil.copytree(src, directory)
        elif not existed_before.get(label, True):
            # Directory was created by migration and backup doesn't have it;
            # it was already deleted above.
            pass


def _promote_staged(staged_root: Path, paths: RuntimePaths) -> None:
    """Move staged directories to final locations using rename for atomicity.

    For same-filesystem moves, os.replace is atomic. For cross-filesystem,
    falls back to copytree + rmtree.
    """
    for label, directory in [
        ("config", paths.config_dir),
        ("data", paths.data_dir),
        ("state", paths.state_dir),
    ]:
        src = staged_root / label
        if not src.exists():
            continue

        directory.parent.mkdir(parents=True, exist_ok=True)

        if directory.exists():
            shutil.rmtree(directory)

        # Try atomic rename first (same filesystem)
        try:
            os.rename(str(src), str(directory))
        except OSError:
            # Cross-filesystem: copy then remove
            shutil.copytree(src, directory)
            shutil.rmtree(src)


def migrate_legacy_root(
    source: Path,
    paths: RuntimePaths,
    *,
    dry_run: bool = False,
) -> MigrationResult:
    """Migrate a legacy Coordinator root to global paths.

    Args:
        source: Legacy root directory containing coordinator.db, config/, etc.
        paths: Target global runtime paths.
        dry_run: If True, validate a temp copy of the DB without writing.

    Returns:
        MigrationResult with status and optional backup path.

    Raises:
        FileNotFoundError: If source does not exist.
    """
    source = Path(source).resolve()
    if not source.exists():
        raise FileNotFoundError(f"legacy root not found: {source}")

    # Check if already migrated from the same source
    marker = _read_marker(paths)
    if marker is not None and marker.get("source") == str(source):
        return MigrationResult(status="already_migrated")

    if dry_run:
        return _dry_run_validate(source)

    return _migrate_write(source, paths)


def _dry_run_validate(source: Path) -> MigrationResult:
    """Validate migration without writing: copy DB to temp, run init_db,
    assert source hash unchanged."""
    src_db = source / "coordinator.db"
    if not src_db.exists():
        return MigrationResult(status="dry_run")

    source_hash = _database_hash(src_db)

    # Copy to temp and validate
    tmp_dir = Path(tempfile.mkdtemp(prefix="coord-dryrun-"))
    try:
        tmp_db = tmp_dir / "coordinator.db"
        shutil.copy2(src_db, tmp_db)
        _validate_database_copy(tmp_db)

        # Assert source hash unchanged
        assert _database_hash(src_db) == source_hash, (
            "source database was modified during dry_run"
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return MigrationResult(status="dry_run")


def _migrate_write(source: Path, paths: RuntimePaths) -> MigrationResult:
    """Perform the actual migration with staging, backup, and rollback."""
    # Track which target dirs existed before we started
    existed_before = {
        "config": paths.config_dir.exists(),
        "data": paths.data_dir.exists(),
        "state": paths.state_dir.exists(),
    }

    staging = None
    backup_path = None
    try:
        # Stage the copy in a temp directory
        staging = Path(tempfile.mkdtemp(prefix="coord-migrate-"))
        _copy_known_paths(source, staging, paths)

        # Validate staged database
        _validate_staged_database(staging)

        # Back up existing target dirs
        if any(existed_before.values()):
            backup_path = _backup_existing(paths)

        # Promote staged to live (atomic rename when possible)
        _promote_staged(staging, paths)

        # Write migration marker
        db_hash = ""
        if paths.database.exists():
            db_hash = _database_hash(paths.database)
        _write_marker(paths, source, db_hash)

        return MigrationResult(status="migrated", backup_path=backup_path)

    except Exception:
        # Rollback
        _rollback(paths, existed_before, backup_path)
        raise
    finally:
        # Clean up staging
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _validate_staged_database(staging: Path) -> None:
    """Open the staged database and run migrations to validate."""
    db_path = staging / "data" / "coordinator.db"
    if not db_path.exists():
        return
    _validate_database_copy(db_path)


def _rollback(
    paths: RuntimePaths,
    existed_before: dict[str, bool],
    backup_path: Path | None,
) -> None:
    """Rollback: restore from backup or delete newly-created directories."""
    if backup_path is not None:
        # Restore from backup
        for label, directory in [
            ("config", paths.config_dir),
            ("data", paths.data_dir),
            ("state", paths.state_dir),
        ]:
            if directory.exists():
                shutil.rmtree(directory)

            src = backup_root = backup_path / label
            if src.exists():
                shutil.copytree(src, directory)
            elif not existed_before[label]:
                # Was newly created, no backup to restore — already deleted
                pass
    else:
        # No backup (fresh migration that failed) — delete newly-created dirs
        for label, directory in [
            ("config", paths.config_dir),
            ("data", paths.data_dir),
            ("state", paths.state_dir),
        ]:
            if directory.exists() and not existed_before[label]:
                shutil.rmtree(directory)
