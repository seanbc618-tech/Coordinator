"""Safe legacy state migration to global Coordinator paths.

Copies database, config, runs, and tasks from a legacy single-root
installation to the global runtime directory structure.

Safety guarantees:
- Each target directory gets its own staging in the same parent directory,
  so rename is always same-filesystem (atomic).
- Promote uses rename chain: old→backup, staging→live (both atomic).
- A migration journal records progress; interrupted migrations can be
  resumed or rolled back on next run. Journal source is validated on resume.
- Journal is written atomically (tmpfile+fsync+os.replace).
- Corrupt journal halts migration and requires manual recovery.
- dry_run operates on a temporary DB copy, never touches the source.
- Never deletes the source.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import connect, init_db
from .runtime_paths import RuntimePaths, resolve_runtime_paths


@dataclass(frozen=True)
class MigrationResult:
    """Immutable result of a migration attempt."""

    status: str  # "migrated", "already_migrated", or "dry_run"
    backup_path: Path | None = None


_MARKER_FILE = ".migrated"
_JOURNAL_FILE = ".migration-journal.json"

# Each target gets its own staging dir in its parent, guaranteeing same-fs rename.
_STAGING_PREFIX = ".coord-staging-"
_BACKUP_PREFIX = "backup-"

_KNOWN_PATHS = (
    "coordinator.db",
    "config",
    "runs",
    "state",
    "tasks",
)

_DEV_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _database_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Marker
# ---------------------------------------------------------------------------

def _write_marker(paths: RuntimePaths, source: Path, db_hash: str) -> None:
    marker = paths.data_dir / _MARKER_FILE
    _atomic_write(marker, (
        f"source: {source}\n"
        f"database_hash: {db_hash}\n"
        f"completed_at: {datetime.now(timezone.utc).isoformat()}\n"
    ))


def _read_marker(paths: RuntimePaths) -> dict[str, str] | None:
    marker = paths.data_dir / _MARKER_FILE
    if not marker.exists():
        return None
    result = {}
    for line in marker.read_text().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


# ---------------------------------------------------------------------------
# Atomic file write (tmpfile + fsync + rename)
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via tmpfile+fsync+os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, str(path))
    except Exception:
        os.close(fd) if not _fd_closed(fd) else None
        _unlink_ignore(tmp)
        raise


def _fd_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
        return False
    except OSError:
        return True


def _unlink_ignore(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def _journal_path(paths: RuntimePaths) -> Path:
    return paths.data_dir.parent / _JOURNAL_FILE


def _write_journal(
    paths: RuntimePaths,
    *,
    source: str,
    backup_path: str | None,
    existed_before: dict[str, bool],
    completed_steps: list[str],
    staging_map: dict[str, str],
) -> None:
    data = {
        "source": source,
        "backup_path": backup_path,
        "existed_before": existed_before,
        "completed_steps": completed_steps,
        "staging_map": staging_map,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write(_journal_path(paths), json.dumps(data, indent=2) + "\n")


def _read_journal(paths: RuntimePaths) -> dict | None:
    jp = _journal_path(paths)
    if not jp.exists():
        return None
    try:
        data = json.loads(jp.read_text())
        if not isinstance(data, dict):
            raise ValueError("journal is not a dict")
        if "source" not in data:
            raise ValueError("journal missing source")
        return data
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        # Corrupt journal — halt, don't silently ignore
        raise RuntimeError(
            f"Migration journal is corrupt ({exc}). "
            f"Delete {_journal_path(paths)} and retry, or restore from backup."
        ) from exc


def _clear_journal(paths: RuntimePaths) -> None:
    jp = _journal_path(paths)
    jp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Per-directory staging helpers
# ---------------------------------------------------------------------------

def _staging_dir_for(target: Path) -> Path:
    """Return a staging directory in the same parent as target."""
    return target.parent / f"{_STAGING_PREFIX}{target.name}"


def _backup_dir_for(target: Path, timestamp: str) -> Path:
    return target.parent / f"{_BACKUP_PREFIX}{timestamp}" / target.name


def _cleanup_staging(paths: RuntimePaths) -> None:
    """Remove any leftover staging directories."""
    for directory in [paths.config_dir, paths.data_dir, paths.state_dir]:
        staging = _staging_dir_for(directory)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

def _copy_known_paths(source: Path, dest_root: Path, paths: RuntimePaths) -> None:
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


def _copy_to_per_target_staging(source: Path, paths: RuntimePaths) -> None:
    """Copy known paths into per-target staging directories.

    Layout: staging dir directly contains the content that will become
    the live directory after rename.
      - config staging: .coord-staging-config/ ← source/config/*
      - data staging:   .coord-staging-data/   ← coordinator.db, runs/, tasks/
      - state staging:  .coord-staging-state/  ← source/state/*
    """
    data_staging = _staging_dir_for(paths.data_dir)
    config_staging = _staging_dir_for(paths.config_dir)
    state_staging = _staging_dir_for(paths.state_dir)

    for name in _KNOWN_PATHS:
        src = source / name
        if not src.exists():
            continue

        if name == "coordinator.db":
            dst = data_staging / "coordinator.db"
        elif name == "config":
            # Copy contents of config/ into config_staging/
            dst = config_staging  # will be handled as directory copy
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            continue
        elif name == "runs":
            dst = data_staging / "runs"
        elif name == "state":
            dst = state_staging
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            continue
        elif name == "tasks":
            dst = data_staging / "tasks"
        else:
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_database_copy(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        init_db(conn)
    finally:
        conn.close()


def _validate_staged_database(paths: RuntimePaths) -> None:
    staging = _staging_dir_for(paths.data_dir)
    db_path = staging / "coordinator.db"
    if not db_path.exists():
        return
    _validate_database_copy(db_path)


# ---------------------------------------------------------------------------
# Atomic rename
# ---------------------------------------------------------------------------

def _atomic_rename(src: Path, dst: Path) -> None:
    os.rename(str(src), str(dst))


# ---------------------------------------------------------------------------
# Promote with per-target rename chain
# ---------------------------------------------------------------------------

def _promote_with_rename_chain(
    paths: RuntimePaths,
    backup_timestamp: str,
    existed_before: dict[str, bool],
    journal: dict,
) -> None:
    """Promote each target using rename chain: old→backup, staging→live.

    Each staging dir is in the same parent as its target, so rename is
    always same-filesystem (atomic). Only EXDEV falls back to copy.
    """
    targets = [
        ("config", paths.config_dir),
        ("data", paths.data_dir),
        ("state", paths.state_dir),
    ]

    completed = list(journal.get("completed_steps", []))
    staging_map = journal.get("staging_map", {})

    for label, live_dir in targets:
        step_key = f"promote:{label}"
        if step_key in completed:
            continue

        staging = _staging_dir_for(live_dir)
        if not staging.exists():
            continue

        # Ensure staging has content (the copy step puts content under
        # staging/<label>/ for dirs, or staging/<file> for DB)
        staging_content = staging / label if (staging / label).is_dir() else staging
        if not any(staging.iterdir()):
            continue

        live_dir.parent.mkdir(parents=True, exist_ok=True)

        if live_dir.exists():
            backup_dir = _backup_dir_for(live_dir, backup_timestamp)
            backup_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                _atomic_rename(live_dir, backup_dir)
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    shutil.copytree(live_dir, backup_dir)
                    shutil.rmtree(live_dir)
                else:
                    raise

        # Rename staging → live
        # For dirs: staging/config → config_dir
        # For data: staging is a sibling of data_dir, we need to rename
        # staging itself to become the live dir
        try:
            _atomic_rename(staging, live_dir)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                shutil.copytree(staging, live_dir)
                shutil.rmtree(staging)
            else:
                raise

        completed.append(step_key)
        journal["completed_steps"] = completed
        _write_journal(
            paths,
            source=journal["source"],
            backup_path=journal.get("backup_path"),
            existed_before=journal["existed_before"],
            completed_steps=completed,
            staging_map=staging_map,
        )


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def _rollback(
    paths: RuntimePaths,
    existed_before: dict[str, bool],
    backup_timestamp: str | None,
) -> None:
    for label, directory in [
        ("config", paths.config_dir),
        ("data", paths.data_dir),
        ("state", paths.state_dir),
    ]:
        if backup_timestamp is not None:
            backup_dir = _backup_dir_for(directory, backup_timestamp)
            if backup_dir.exists():
                if directory.exists():
                    shutil.rmtree(directory)
                try:
                    _atomic_rename(backup_dir, directory)
                except OSError:
                    shutil.copytree(backup_dir, directory)
                continue

        if directory.exists() and not existed_before.get(label, False):
            shutil.rmtree(directory)

    _cleanup_staging(paths)


# ---------------------------------------------------------------------------
# First-run detection and prompting
# ---------------------------------------------------------------------------

def _is_legacy_root(path: Path) -> bool:
    return any((path / name).exists() for name in _KNOWN_PATHS)


def _is_same_as_global(legacy: Path, paths: RuntimePaths) -> bool:
    legacy = legacy.resolve()
    home = os.environ.get("COORDINATOR_HOME")
    if home and legacy == Path(home).resolve():
        return True

    legacy_db = legacy / "coordinator.db"
    if legacy_db.exists() and paths.database.exists():
        if legacy_db.resolve() == paths.database.resolve():
            return True
    return False


def _development_root() -> Path | None:
    if _is_legacy_root(_DEV_ROOT):
        return _DEV_ROOT
    return None


def detect_legacy_root(paths: RuntimePaths | None = None) -> Path | None:
    """Return a legacy single-root installation when one is detectable."""
    resolved_paths = paths or resolve_runtime_paths()

    env_root = os.environ.get("COORDINATOR_LEGACY_ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if _is_legacy_root(candidate) and not _is_same_as_global(candidate, resolved_paths):
            return candidate
        return None

    if os.environ.get("COORDINATOR_HOME"):
        return None

    dev_root = _development_root()
    if dev_root is not None and not _is_same_as_global(dev_root, resolved_paths):
        return dev_root
    return None


def is_global_state_empty(paths: RuntimePaths) -> bool:
    """Return True when global Coordinator state has never been activated."""
    if (paths.data_dir / _MARKER_FILE).exists():
        return False
    return not paths.database.exists()


def needs_first_run_migration(paths: RuntimePaths) -> bool:
    """Return True when an empty global install should offer legacy migration."""
    if not is_global_state_empty(paths):
        return False
    return detect_legacy_root(paths) is not None


def format_migration_summary(source: Path, paths: RuntimePaths) -> str:
    """Render a human-readable summary of a pending legacy migration."""
    source = source.resolve()
    lines = [
        "Legacy Coordinator installation detected.",
        f"Source: {source}",
        f"Target: {paths.data_dir.parent}",
        "",
        "The following will be migrated:",
    ]
    for name in _KNOWN_PATHS:
        if (source / name).exists():
            lines.append(f"  - {name}")
    lines.extend(
        [
            "",
            "A timestamped backup is created before overwriting existing global state.",
            "The original installation is never deleted automatically.",
        ]
    )
    return "\n".join(lines)


def prompt_migration_or_exit(
    source: Path,
    paths: RuntimePaths,
    *,
    interactive: bool = True,
    input_func: Callable[[str], str] | None = None,
) -> MigrationResult | None:
    """Validate, summarize, and optionally execute first-run migration."""
    source = Path(source).resolve()
    summary = format_migration_summary(source, paths)
    print(summary)

    try:
        dry_run = migrate_legacy_root(source, paths, dry_run=True)
    except Exception as exc:
        print(f"migration validation failed: {exc}", file=sys.stderr)
        return None

    print(f"validation: {dry_run.status}")

    if not interactive:
        print("Refusing to migrate without interactive confirmation.")
        print(f"Run: coordinator migrate --source {source} --yes")
        return None

    prompt = "Migrate legacy Coordinator state? [y/N] "
    if input_func is not None:
        print(prompt, end="")
        answer = input_func(prompt).strip().casefold()
    else:
        answer = input(prompt).strip().casefold()
    if answer not in {"y", "yes"}:
        print("Migration declined.")
        return None

    try:
        return migrate_legacy_root(source, paths)
    except Exception as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Artifact path remapping
# ---------------------------------------------------------------------------

def _remap_artifact_path(path_str: str, source: Path, data_dir: Path) -> str:
    source = source.resolve()
    data_dir = data_dir.resolve()
    path = Path(path_str)
    resolved = path.resolve() if path.is_absolute() else path

    if path.is_absolute():
        try:
            relative = resolved.relative_to(source)
        except ValueError:
            legacy_runs = (source / "runs").resolve()
            if legacy_runs in resolved.parents:
                return str(data_dir / "runs" / resolved.relative_to(legacy_runs))
            return path_str
        return str(data_dir / relative)

    if path_str.startswith("runs/"):
        return str(data_dir / path_str)
    return str(data_dir / path_str)


def _remap_artifact_paths(paths: RuntimePaths, source: Path) -> None:
    if not paths.database.exists():
        return

    conn = connect(paths.database)
    try:
        init_db(conn)
        rows = conn.execute("select id, path from artifacts").fetchall()
        for row in rows:
            new_path = _remap_artifact_path(row["path"], source, paths.data_dir)
            if new_path != row["path"]:
                conn.execute(
                    "update artifacts set path = ? where id = ?",
                    (new_path, row["id"]),
                )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def migrate_legacy_root(
    source: Path,
    paths: RuntimePaths,
    *,
    dry_run: bool = False,
) -> MigrationResult:
    source = Path(source).resolve()
    if not source.exists():
        raise FileNotFoundError(f"legacy root not found: {source}")

    # Reconcile journal and marker before deciding
    journal = _read_journal(paths)  # None if no journal; raises on corrupt
    marker = _read_marker(paths)

    marker_source = marker.get("source") if marker else None
    journal_source = journal.get("source") if journal else None
    requested = str(source)

    # Case 1: marker + journal + requested all agree → fully migrated,
    # journal wasn't cleaned (crash after marker, before cleanup).
    if marker_source == requested and journal_source == requested:
        _clear_journal(paths)
        _cleanup_staging(paths)
        return MigrationResult(status="already_migrated")

    # Case 2: marker matches, no journal → already migrated cleanly
    if marker_source == requested and journal is None:
        return MigrationResult(status="already_migrated")

    # Case 3: journal exists for this source but no marker → incomplete
    if journal_source == requested and marker_source != requested:
        return _resume_migration(source, paths, journal)

    # Case 4: journal exists for a different source → conflict
    if journal_source is not None and journal_source != requested:
        raise RuntimeError(
            f"Migration journal was started from a different source: "
            f"{journal_source!r} != {requested!r}. "
            f"Delete {_journal_path(paths)} to start fresh."
        )

    # Case 5: no marker, no journal → fresh migration
    if dry_run:
        return _dry_run_validate(source)

    return _migrate_write(source, paths)


def _dry_run_validate(source: Path) -> MigrationResult:
    """Validate: copy DB to temp, run init_db, assert source hash unchanged."""
    src_db = source / "coordinator.db"
    if not src_db.exists():
        # No DB to validate — check source has at least one known path
        has_any = any((source / name).exists() for name in _KNOWN_PATHS)
        if not has_any:
            return MigrationResult(status="dry_run")
        # Has content but no DB — still valid (config-only migration)
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
    existing_journal = _read_journal(paths)
    if existing_journal is not None:
        return _resume_migration(source, paths, existing_journal)

    existed_before = {
        "config": paths.config_dir.exists(),
        "data": paths.data_dir.exists(),
        "state": paths.state_dir.exists(),
    }

    backup_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Create per-target staging dirs (same filesystem as each target)
    for directory in [paths.config_dir, paths.data_dir, paths.state_dir]:
        staging = _staging_dir_for(directory)
        staging.mkdir(parents=True, exist_ok=True)

    journal = {
        "source": str(source),
        "backup_path": backup_timestamp,
        "existed_before": existed_before,
        "completed_steps": [],
        "staging_map": {},
    }

    try:
        # Phase 1: Copy to per-target staging
        _copy_to_per_target_staging(source, paths)
        _validate_staged_database(paths)

        # Phase 2: Write journal before touching live dirs
        _write_journal(
            paths,
            source=str(source),
            backup_path=backup_timestamp,
            existed_before=existed_before,
            completed_steps=[],
            staging_map={},
        )

        # Phase 3: Rename-chain promote
        _promote_with_rename_chain(
            paths, backup_timestamp, existed_before, journal
        )

        # Phase 4: Remap artifact paths and write marker
        _remap_artifact_paths(paths, source)
        db_hash = ""
        if paths.database.exists():
            db_hash = _database_hash(paths.database)
        _write_marker(paths, source, db_hash)
        _clear_journal(paths)

        return MigrationResult(
            status="migrated",
            backup_path=paths.data_dir.parent / f"{_BACKUP_PREFIX}{backup_timestamp}",
        )

    except Exception:
        _rollback(paths, existed_before, backup_timestamp)
        _clear_journal(paths)
        raise
    finally:
        _cleanup_staging(paths)


def _resume_migration(
    source: Path,
    paths: RuntimePaths,
    journal: dict,
) -> MigrationResult:
    # Source validation already done by caller
    backup_timestamp = journal.get("backup_path", "")
    existed_before = journal.get("existed_before", {})

    # Check staging dirs still exist
    has_staging = any(
        _staging_dir_for(d).exists()
        for d in [paths.config_dir, paths.data_dir, paths.state_dir]
    )
    if not has_staging:
        _clear_journal(paths)
        return _migrate_write(source, paths)

    try:
        _promote_with_rename_chain(
            paths, backup_timestamp, existed_before, journal
        )

        _remap_artifact_paths(paths, source)
        db_hash = ""
        if paths.database.exists():
            db_hash = _database_hash(paths.database)
        _write_marker(paths, source, db_hash)
        _clear_journal(paths)

        return MigrationResult(
            status="migrated",
            backup_path=paths.data_dir.parent / f"{_BACKUP_PREFIX}{backup_timestamp}",
        )

    except Exception:
        _rollback(paths, existed_before, backup_timestamp)
        _clear_journal(paths)
        raise
    finally:
        _cleanup_staging(paths)
