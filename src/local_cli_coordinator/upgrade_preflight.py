"""Upgrade preflight checks before applying migrations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import __version__
from .backup_manager import get_latest_backup_record
from .config_runtime import REQUIRED_CONFIG_FILES, config_files_present
from .db import iter_migration_scripts
from .runtime_paths import RuntimePaths

PREFLIGHT_STATUSES = frozenset({"pass", "warn", "fail"})


@dataclass(frozen=True)
class PreflightRun:
    id: str
    from_version: str
    to_version: str
    status: str
    findings: list[dict[str, Any]]
    created_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _applied_migrations(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select version from schema_migrations").fetchall()
    return {str(row["version"]) for row in rows}


def _pending_migrations(conn: sqlite3.Connection) -> list[str]:
    applied = _applied_migrations(conn)
    return [name for name, _ in iter_migration_scripts() if name not in applied]


def _record_preflight(
    conn: sqlite3.Connection,
    *,
    from_version: str,
    to_version: str,
    status: str,
    findings: list[dict[str, Any]],
    commit: bool = True,
) -> PreflightRun:
    if status not in PREFLIGHT_STATUSES:
        raise ValueError(f"invalid preflight status: {status}")
    run_id = f"preflight-{uuid.uuid4().hex[:12]}"
    created_at = _iso_now()
    conn.execute(
        """
        insert into upgrade_preflight_runs(
            id, from_version, to_version, status, findings_json, created_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            from_version,
            to_version,
            status,
            json.dumps(findings, ensure_ascii=False),
            created_at,
        ),
    )
    if commit:
        conn.commit()
    return PreflightRun(
        id=run_id,
        from_version=from_version,
        to_version=to_version,
        status=status,
        findings=findings,
        created_at=created_at,
    )


def run_upgrade_preflight(
    conn: sqlite3.Connection,
    paths: RuntimePaths,
    *,
    to_version: str | None = None,
    record: bool = True,
) -> dict[str, Any]:
    paths.create()
    target_version = to_version or __version__
    findings: list[dict[str, Any]] = []

    pending = _pending_migrations(conn)
    if pending:
        findings.append(
            {
                "code": "pending_migrations",
                "severity": "warn",
                "message": f"{len(pending)} migration(s) pending before upgrade",
                "details": pending,
            }
        )

    missing_config = [
        name
        for name in REQUIRED_CONFIG_FILES
        if not (paths.config_dir / name).is_file()
    ]
    if missing_config:
        findings.append(
            {
                "code": "missing_config",
                "severity": "fail",
                "message": "required config files are missing",
                "details": missing_config,
            }
        )
    elif not config_files_present(paths):
        findings.append(
            {
                "code": "missing_config",
                "severity": "fail",
                "message": "config directory is incomplete",
                "details": list(REQUIRED_CONFIG_FILES),
            }
        )

    latest_backup = get_latest_backup_record(conn)
    if latest_backup is None:
        findings.append(
            {
                "code": "backup_recommended",
                "severity": "warn",
                "message": "no backup record found; create a backup before upgrading",
                "details": [],
            }
        )
    elif latest_backup.status not in {"created", "verified"}:
        findings.append(
            {
                "code": "backup_recommended",
                "severity": "warn",
                "message": "latest backup is not verified",
                "details": [latest_backup.id],
            }
        )

    if not paths.database.is_file():
        findings.append(
            {
                "code": "missing_database",
                "severity": "fail",
                "message": "coordinator database does not exist",
                "details": [str(paths.database)],
            }
        )

    severities = {item["severity"] for item in findings}
    if "fail" in severities:
        status = "fail"
    elif "warn" in severities:
        status = "warn"
    else:
        status = "pass"

    payload = {
        "from_version": __version__,
        "to_version": target_version,
        "status": status,
        "findings": findings,
        "pending_migrations": pending,
        "backup_recommended": any(
            item["code"] == "backup_recommended" for item in findings
        ),
    }

    if record:
        run = _record_preflight(
            conn,
            from_version=__version__,
            to_version=target_version,
            status=status,
            findings=findings,
        )
        payload["run_id"] = run.id
        payload["created_at"] = run.created_at

    return payload