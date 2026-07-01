"""Coordinator home backup, verify, and restore with dry-run default."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .db import connect, init_db, iter_migration_scripts
from .runtime_paths import RuntimePaths

BACKUP_STATUSES = frozenset({"created", "verified", "failed"})
_CONFIG_FILES = ("agents.toml", "repos.toml", "policy.toml")
_ARCHIVE_NAME = "payload.tar.gz"
_MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class BackupRecord:
    id: str
    backup_path: str
    status: str
    manifest: dict[str, Any]
    created_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, content: str) -> None:
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
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _coordinator_home(paths: RuntimePaths) -> Path:
    return paths.coordinator_home


def _list_schema_migrations(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "select version from schema_migrations order by version"
    ).fetchall()
    return [str(row["version"]) for row in rows]


def _package_migrations() -> list[str]:
    return [name for name, _ in iter_migration_scripts()]


def _collect_runs_manifest(paths: RuntimePaths) -> list[dict[str, Any]]:
    runs_dir = paths.data_dir / "runs"
    if not runs_dir.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for child in sorted(runs_dir.iterdir()):
        if not child.is_dir():
            continue
        meta = child / "run.json"
        if meta.is_file():
            try:
                payload = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
        else:
            payload = {"run_id": child.name}
        entries.append({"run_id": child.name, "metadata": payload})
    return entries


def _backup_targets(paths: RuntimePaths) -> list[tuple[str, Path]]:
    targets: list[tuple[str, Path]] = []
    if paths.database.is_file():
        targets.append(("data/coordinator.db", paths.database))
    for name in _CONFIG_FILES:
        config_path = paths.config_dir / name
        if config_path.is_file():
            targets.append((f"config/{name}", config_path))
    marker = paths.data_dir / ".migrated"
    if marker.is_file():
        targets.append(("data/.migrated", marker))
    return targets


def _record_backup(
    conn: sqlite3.Connection,
    *,
    backup_id: str,
    backup_path: Path,
    status: str,
    manifest: Mapping[str, Any],
    commit: bool = True,
) -> BackupRecord:
    if status not in BACKUP_STATUSES:
        raise ValueError(f"invalid backup status: {status}")
    created_at = _iso_now()
    conn.execute(
        """
        insert into backup_records(id, backup_path, status, manifest_json, created_at)
        values (?, ?, ?, ?, ?)
        """,
        (
            backup_id,
            str(backup_path),
            status,
            json.dumps(dict(manifest), ensure_ascii=False),
            created_at,
        ),
    )
    if commit:
        conn.commit()
    return BackupRecord(
        id=backup_id,
        backup_path=str(backup_path),
        status=status,
        manifest=dict(manifest),
        created_at=created_at,
    )


def get_latest_backup_record(conn: sqlite3.Connection) -> BackupRecord | None:
    row = conn.execute(
        """
        select id, backup_path, status, manifest_json, created_at
        from backup_records
        order by created_at desc
        limit 1
        """
    ).fetchone()
    if row is None:
        return None
    return BackupRecord(
        id=str(row["id"]),
        backup_path=str(row["backup_path"]),
        status=str(row["status"]),
        manifest=json.loads(str(row["manifest_json"] or "{}")),
        created_at=str(row["created_at"]),
    )


def create_backup(
    conn: sqlite3.Connection,
    paths: RuntimePaths,
) -> dict[str, Any]:
    paths.create()
    backup_id = f"backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    backup_root = paths.backups_dir / backup_id
    backup_root.mkdir(parents=True, exist_ok=True)

    files: dict[str, dict[str, Any]] = {}
    archive_members: list[tuple[str, Path]] = []
    for rel_path, source in _backup_targets(paths):
        files[rel_path] = {
            "sha256": _sha256_file(source),
            "size": source.stat().st_size,
        }
        archive_members.append((rel_path, source))

    manifest = {
        "backup_id": backup_id,
        "coordinator_version": __version__,
        "created_at": _iso_now(),
        "schema_migrations": _list_schema_migrations(conn),
        "package_migrations": _package_migrations(),
        "files": files,
        "runs_manifest": _collect_runs_manifest(paths),
        "coordinator_home": str(_coordinator_home(paths)),
    }

    archive_path = backup_root / _ARCHIVE_NAME
    with tarfile.open(archive_path, "w:gz") as archive:
        for rel_path, source in archive_members:
            archive.add(source, arcname=rel_path)

    manifest_path = backup_root / _MANIFEST_NAME
    _atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))

    record = _record_backup(
        conn,
        backup_id=backup_id,
        backup_path=backup_root,
        status="created",
        manifest=manifest,
    )
    return {
        "backup_id": record.id,
        "backup_path": record.backup_path,
        "status": record.status,
        "manifest": record.manifest,
        "file_count": len(files),
    }


def _load_manifest(backup_path: Path) -> dict[str, Any]:
    manifest_path = backup_path / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(f"backup manifest missing: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def verify_backup(backup_path: Path) -> dict[str, Any]:
    backup_root = backup_path.resolve()
    manifest = _load_manifest(backup_root)
    archive_path = backup_root / _ARCHIVE_NAME
    if not archive_path.is_file():
        raise ValueError(f"backup archive missing: {archive_path}")

    files = manifest.get("files") or {}
    if not isinstance(files, dict) or not files:
        raise ValueError("backup manifest has no file checksums")

    checksum_errors: list[str] = []
    with tarfile.open(archive_path, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        for rel_path, meta in files.items():
            if rel_path not in members:
                checksum_errors.append(f"missing archived file: {rel_path}")
                continue
            extracted = archive.extractfile(members[rel_path])
            if extracted is None:
                checksum_errors.append(f"unable to read archived file: {rel_path}")
                continue
            digest = hashlib.sha256()
            while True:
                chunk = extracted.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            expected = str(meta.get("sha256", ""))
            actual = digest.hexdigest()
            if expected != actual:
                checksum_errors.append(
                    f"checksum mismatch for {rel_path}: expected {expected}, got {actual}"
                )

    ok = not checksum_errors
    return {
        "backup_id": manifest.get("backup_id"),
        "backup_path": str(backup_root),
        "status": "verified" if ok else "failed",
        "ok": ok,
        "errors": checksum_errors,
        "manifest": manifest,
    }


def check_restore_compatibility(
    manifest: Mapping[str, Any],
    *,
    package_migrations: list[str] | None = None,
) -> tuple[bool, list[str]]:
    package = package_migrations or _package_migrations()
    backup_migrations = {
        str(item) for item in (manifest.get("schema_migrations") or [])
    }
    unknown = sorted(backup_migrations - set(package))
    if unknown:
        return False, [
            "backup schema migrations are newer than this Coordinator install",
            f"unknown migrations: {', '.join(unknown)}",
        ]
    return True, []


def plan_restore(
    backup_path: Path,
    paths: RuntimePaths,
    *,
    force_compatible_risk: bool = False,
) -> dict[str, Any]:
    backup_root = backup_path.resolve()
    verification = verify_backup(backup_root)
    manifest = verification["manifest"]
    compatible, compatibility_errors = check_restore_compatibility(manifest)
    blocked = not compatible and not force_compatible_risk

    actions: list[str] = []
    for rel_path in sorted((manifest.get("files") or {}).keys()):
        actions.append(f"restore {rel_path}")

    return {
        "mode": "dry_run",
        "backup_id": manifest.get("backup_id"),
        "backup_path": str(backup_root),
        "compatible": compatible,
        "blocked": blocked,
        "compatibility_errors": compatibility_errors,
        "verification_ok": verification["ok"],
        "actions": actions if not blocked else [],
        "would_restore_files": 0 if blocked else len(actions),
    }


def apply_restore(
    backup_path: Path,
    paths: RuntimePaths,
    *,
    force_compatible_risk: bool = False,
) -> dict[str, Any]:
    plan = plan_restore(
        backup_path,
        paths,
        force_compatible_risk=force_compatible_risk,
    )
    if plan["blocked"]:
        raise ValueError("; ".join(plan["compatibility_errors"]))

    verification = verify_backup(backup_path.resolve())
    if not verification["ok"]:
        raise ValueError("backup verification failed: " + "; ".join(verification["errors"]))

    manifest = verification["manifest"]
    archive_path = backup_path.resolve() / _ARCHIVE_NAME
    restored: list[str] = []

    with tempfile.TemporaryDirectory(prefix="coord-restore-") as staging_name:
        staging = Path(staging_name)
        with tarfile.open(archive_path, "r:gz") as archive:
            try:
                archive.extractall(path=staging, filter="data")
            except TypeError:
                archive.extractall(path=staging)

        for rel_path in sorted((manifest.get("files") or {}).keys()):
            source = staging / rel_path
            if not source.is_file():
                raise ValueError(f"staged restore file missing: {rel_path}")

            if rel_path == "data/coordinator.db":
                target = paths.database
            elif rel_path.startswith("config/"):
                target = paths.config_dir / rel_path.removeprefix("config/")
            elif rel_path.startswith("data/"):
                target = paths.data_dir / rel_path.removeprefix("data/")
            else:
                raise ValueError(f"unsupported restore path: {rel_path}")

            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_target = target.with_suffix(target.suffix + ".restore.tmp")
            shutil.copy2(source, tmp_target)
            os.replace(tmp_target, target)
            restored.append(rel_path)

    return {
        "mode": "apply",
        "backup_id": manifest.get("backup_id"),
        "backup_path": str(backup_path.resolve()),
        "restored_files": restored,
        "restored_count": len(restored),
        "compatible": plan["compatible"],
        "forced": force_compatible_risk and not plan["compatible"],
    }


def restore_backup(
    backup_path: Path,
    paths: RuntimePaths,
    *,
    dry_run: bool = True,
    apply: bool = False,
    force_compatible_risk: bool = False,
) -> dict[str, Any]:
    if apply:
        return apply_restore(
            backup_path,
            paths,
            force_compatible_risk=force_compatible_risk,
        )
    return plan_restore(
        backup_path,
        paths,
        force_compatible_risk=force_compatible_risk,
    )


def mark_backup_verified(conn: sqlite3.Connection, backup_id: str) -> None:
    conn.execute(
        "update backup_records set status = 'verified' where id = ?",
        (backup_id,),
    )
    conn.commit()


def open_paths_database(paths: RuntimePaths) -> sqlite3.Connection:
    conn = connect(paths.database)
    init_db(conn)
    return conn