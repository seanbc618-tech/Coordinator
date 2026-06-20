"""Safe legacy state migration to global Coordinator paths.

Copies database, config, runs, and tasks from a legacy single-root
installation to the global runtime directory structure.
"""

from __future__ import annotations

import hashlib
import shutil
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


def _copy_legacy(source: Path, paths: RuntimePaths) -> None:
    """Copy known legacy paths to global directories."""
    paths.create()

    for name in _KNOWN_PATHS:
        src = source / name
        if not src.exists():
            continue

        if name == "coordinator.db":
            dst = paths.database
        elif name == "config":
            dst = paths.config_dir
        elif name == "runs":
            dst = paths.data_dir / "runs"
        elif name == "state":
            dst = paths.state_dir
        elif name == "tasks":
            dst = paths.data_dir / "tasks"
        else:
            continue

        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _validate_database(paths: RuntimePaths) -> None:
    """Open the copied database and run migrations to validate."""
    conn = connect(paths.database)
    try:
        init_db(conn)
    finally:
        conn.close()


def _backup_existing(paths: RuntimePaths) -> Path:
    """Back up existing data directory before overwrite."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = paths.data_dir.parent / f"backup-{timestamp}"
    if paths.data_dir.exists():
        shutil.copytree(paths.data_dir, backup)
    return backup


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
        return MigrationResult(status="dry_run")

    # Back up existing destination
    backup_path = None
    if paths.data_dir.exists():
        backup_path = _backup_existing(paths)

    # Copy legacy state
    _copy_legacy(source, paths)

    # Validate the copied database
    _validate_database(paths)

    # Write migration marker
    db_hash = _database_hash(paths.database) if paths.database.exists() else ""
    _write_marker(paths, source, db_hash)

    return MigrationResult(status="migrated", backup_path=backup_path)
