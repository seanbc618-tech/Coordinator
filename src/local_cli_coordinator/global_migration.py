"""Safe legacy state migration to global Coordinator paths.

Copies database, config, runs, and tasks from a legacy single-root
installation to the global runtime directory structure.

Safety guarantees:
- Copies to a staging directory first; validates before touching live dirs.
- Backs up all three target dirs (config, data, state) before overwrite.
- Rolls back to backup if validation fails.
- Never deletes the source.
"""

from __future__ import annotations

import hashlib
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


def _validate_staged_database(staged_root: Path) -> None:
    """Open the staged database and run migrations to validate."""
    db_path = staged_root / "data" / "coordinator.db"
    if not db_path.exists():
        return
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


def _restore_backup(backup_root: Path, paths: RuntimePaths) -> None:
    """Restore all three directories from a backup."""
    for label, directory in [
        ("config", paths.config_dir),
        ("data", paths.data_dir),
        ("state", paths.state_dir),
    ]:
        src = backup_root / label
        if src.exists():
            if directory.exists():
                shutil.rmtree(directory)
            shutil.copytree(src, directory)


def _promote_staged(staged_root: Path, paths: RuntimePaths) -> None:
    """Move staged files to final locations."""
    for label, directory in [
        ("config", paths.config_dir),
        ("data", paths.data_dir),
        ("state", paths.state_dir),
    ]:
        src = staged_root / label
        if not src.exists():
            continue
        if directory.exists():
            shutil.rmtree(directory)
        shutil.copytree(src, directory)


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
        dry_run: If True, validate only without writing.

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
        # Validate source has a readable database
        src_db = source / "coordinator.db"
        if src_db.exists():
            conn = connect(src_db)
            try:
                init_db(conn)
            finally:
                conn.close()
        return MigrationResult(status="dry_run")

    # Stage the copy in a temp directory first
    staging = None
    backup_path = None
    try:
        staging = Path(tempfile.mkdtemp(prefix="coord-migrate-"))
        _copy_known_paths(source, staging, paths)

        # Validate staged database
        _validate_staged_database(staging)

        # Back up existing target dirs
        if paths.data_dir.exists() or paths.config_dir.exists() or paths.state_dir.exists():
            backup_path = _backup_existing(paths)

        # Promote staged to live
        _promote_staged(staging, paths)

        # Write migration marker
        db_hash = ""
        if paths.database.exists():
            db_hash = _database_hash(paths.database)
        _write_marker(paths, source, db_hash)

        return MigrationResult(status="migrated", backup_path=backup_path)

    except Exception:
        # Rollback: restore from backup if we have one
        if backup_path is not None:
            _restore_backup(backup_path, paths)
        raise
    finally:
        # Clean up staging
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
