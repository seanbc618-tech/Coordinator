"""Safe legacy state migration to global Coordinator paths.

Copies database, config, runs, and tasks from a legacy single-root
installation to the global runtime directory structure.

Safety guarantees:
- Staging is created on the same filesystem as the target.
- Promote uses rename chain: old→backup, staging→live (both atomic on same fs).
- A migration journal records progress so interrupted migrations can be
  completed or rolled back on next run.
- dry_run operates on a temporary DB copy, never touches the source.
- Never deletes the source.
"""

from __future__ import annotations

import errno
import hashlib
import json
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
_JOURNAL_FILE = ".migration-journal.json"


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


def _journal_path(paths: RuntimePaths) -> Path:
    """Journal lives alongside the target directories."""
    return paths.data_dir.parent / _JOURNAL_FILE


def _write_journal(
    paths: RuntimePaths,
    *,
    source: str,
    backup_path: str | None,
    existed_before: dict[str, bool],
    completed_steps: list[str],
    staging_root: str,
) -> None:
    """Write a crash-recovery journal."""
    data = {
        "source": source,
        "backup_path": backup_path,
        "existed_before": existed_before,
        "completed_steps": completed_steps,
        "staging_root": staging_root,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    jp = _journal_path(paths)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(data, indent=2) + "\n")


def _read_journal(paths: RuntimePaths) -> dict | None:
    """Read an existing journal, or None."""
    jp = _journal_path(paths)
    if not jp.exists():
        return None
    try:
        return json.loads(jp.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _clear_journal(paths: RuntimePaths) -> None:
    """Remove the journal after successful completion."""
    jp = _journal_path(paths)
    jp.unlink(missing_ok=True)


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


def _validate_staged_database(staging: Path) -> None:
    """Open the staged database and run migrations to validate."""
    db_path = staging / "data" / "coordinator.db"
    if not db_path.exists():
        return
    _validate_database_copy(db_path)


def _atomic_rename(src: Path, dst: Path) -> None:
    """Rename src to dst atomically. Raises OSError on cross-filesystem."""
    os.rename(str(src), str(dst))


def _promote_with_rename_chain(
    staging_root: Path,
    paths: RuntimePaths,
    backup_root: Path,
    existed_before: dict[str, bool],
    journal: dict,
) -> None:
    """Promote staged dirs using a rename chain.

    For each directory:
      1. If live exists: rename live → backup (atomic)
      2. Rename staging → live (atomic)

    On EXDEV (cross-filesystem), falls back to copy+remove.
    """
    steps = [
        ("config", paths.config_dir, staging_root / "config"),
        ("data", paths.data_dir, staging_root / "data"),
        ("state", paths.state_dir, staging_root / "state"),
    ]

    completed = list(journal.get("completed_steps", []))

    # Ensure backup root exists before any rename into it
    backup_root.mkdir(parents=True, exist_ok=True)

    for label, live_dir, staged_dir in steps:
        step_key = f"promote:{label}"

        if step_key in completed:
            continue  # Already done (crash recovery)

        if not staged_dir.exists():
            continue

        # Ensure parent exists
        live_dir.parent.mkdir(parents=True, exist_ok=True)

        if live_dir.exists():
            # Step 1: rename live → backup
            backup_dir = backup_root / label
            try:
                _atomic_rename(live_dir, backup_dir)
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    shutil.copytree(live_dir, backup_dir)
                    shutil.rmtree(live_dir)
                else:
                    raise

        # Step 2: rename staging → live
        try:
            _atomic_rename(staged_dir, live_dir)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                shutil.copytree(staged_dir, live_dir)
                shutil.rmtree(staged_dir)
            else:
                raise

        completed.append(step_key)
        journal["completed_steps"] = completed
        _write_journal(
            paths,
            source=journal["source"],
            backup_path=str(backup_root),
            existed_before=journal["existed_before"],
            completed_steps=completed,
            staging_root=str(staging_root),
        )


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

    tmp_dir = Path(tempfile.mkdtemp(prefix="coord-dryrun-"))
    try:
        tmp_db = tmp_dir / "coordinator.db"
        shutil.copy2(src_db, tmp_db)
        _validate_database_copy(tmp_db)

        assert _database_hash(src_db) == source_hash, (
            "source database was modified during dry_run"
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return MigrationResult(status="dry_run")


def _migrate_write(source: Path, paths: RuntimePaths) -> MigrationResult:
    """Perform the actual migration with staging, rename-chain promote,
    journal-based crash recovery, and rollback."""

    # Check for incomplete previous migration
    existing_journal = _read_journal(paths)
    if existing_journal is not None:
        return _resume_migration(source, paths, existing_journal)

    existed_before = {
        "config": paths.config_dir.exists(),
        "data": paths.data_dir.exists(),
        "state": paths.state_dir.exists(),
    }

    # Create staging on same filesystem as target (for atomic rename)
    staging_parent = paths.data_dir.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(
        prefix="coord-migrate-",
        dir=staging_parent,
    ))

    backup_root = paths.data_dir.parent / (
        "backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )

    journal = {
        "source": str(source),
        "backup_path": str(backup_root),
        "existed_before": existed_before,
        "completed_steps": [],
        "staging_root": str(staging_root),
    }

    try:
        # Phase 1: Copy to staging
        _copy_known_paths(source, staging_root, paths)
        _validate_staged_database(staging_root)

        # Phase 2: Write journal before touching live dirs
        _write_journal(
            paths,
            source=str(source),
            backup_path=str(backup_root),
            existed_before=existed_before,
            completed_steps=[],
            staging_root=str(staging_root),
        )

        # Phase 3: Rename-chain promote (atomic per directory)
        _promote_with_rename_chain(
            staging_root, paths, backup_root, existed_before, journal
        )

        # Phase 4: Write marker and clean up
        db_hash = ""
        if paths.database.exists():
            db_hash = _database_hash(paths.database)
        _write_marker(paths, source, db_hash)
        _clear_journal(paths)

        return MigrationResult(status="migrated", backup_path=backup_root)

    except Exception:
        _rollback(paths, existed_before, backup_root)
        _clear_journal(paths)
        raise
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)


def _resume_migration(
    source: Path,
    paths: RuntimePaths,
    journal: dict,
) -> MigrationResult:
    """Resume an interrupted migration using the journal."""
    staging_root = Path(journal["staging_root"])
    backup_path = Path(journal["backup_path"]) if journal.get("backup_path") else None
    existed_before = journal.get("existed_before", {})

    # If staging is gone, we can't resume — clean up and start over
    if not staging_root.exists():
        _clear_journal(paths)
        return _migrate_write(source, paths)

    try:
        # Continue the rename chain from where we left off
        _promote_with_rename_chain(
            staging_root, paths, backup_path or Path(), existed_before, journal
        )

        # Write marker and clean up
        db_hash = ""
        if paths.database.exists():
            db_hash = _database_hash(paths.database)
        _write_marker(paths, source, db_hash)
        _clear_journal(paths)

        return MigrationResult(status="migrated", backup_path=backup_path)

    except Exception:
        _rollback(paths, existed_before, backup_path)
        _clear_journal(paths)
        raise
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)


def _rollback(
    paths: RuntimePaths,
    existed_before: dict[str, bool],
    backup_path: Path | None,
) -> None:
    """Rollback: restore from backup or delete newly-created directories.

    Only touches directories that were modified by the migration.
    Pre-existing directories that weren't renamed away are left alone.
    """
    for label, directory in [
        ("config", paths.config_dir),
        ("data", paths.data_dir),
        ("state", paths.state_dir),
    ]:
        # If backup exists, restore from it (renamed-away dir)
        if backup_path is not None:
            backup_dir = backup_path / label
            if backup_dir.exists():
                if directory.exists():
                    shutil.rmtree(directory)
                # Rename back (atomic on same fs)
                try:
                    _atomic_rename(backup_dir, directory)
                except OSError:
                    shutil.copytree(backup_dir, directory)
                continue

        # No backup: only delete dirs that were newly created
        if directory.exists() and not existed_before.get(label, False):
            shutil.rmtree(directory)
