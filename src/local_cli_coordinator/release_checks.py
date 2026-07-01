"""Release readiness diagnostics for Coordinator installs."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import __version__
from .backup_manager import _package_migrations, verify_backup
from .config_runtime import REQUIRED_CONFIG_FILES, config_files_present
from .db import iter_migration_scripts
from .extension_loader import load_extensions
from .runtime_paths import RuntimePaths
from .upgrade_preflight import run_upgrade_preflight

ROOT = Path(__file__).resolve().parents[2]
MIRROR_MIGRATIONS = ROOT / "migrations"
PACKAGE_MIGRATIONS = ROOT / "src" / "local_cli_coordinator" / "migrations"


def _migration_mirror_ok() -> tuple[bool, list[str]]:
    if not MIRROR_MIGRATIONS.is_dir() or not PACKAGE_MIGRATIONS.is_dir():
        return False, ["migration directories missing"]
    auth = {p.name: p.read_bytes() for p in PACKAGE_MIGRATIONS.glob("*.sql")}
    mir = {p.name: p.read_bytes() for p in MIRROR_MIGRATIONS.glob("*.sql")}
    errors: list[str] = []
    if set(auth) != set(mir):
        errors.append("migration mirror file set mismatch")
    for name, body in auth.items():
        if mir.get(name) != body:
            errors.append(f"migration mirror mismatch: {name}")
    return not errors, errors


def _pending_migrations(conn: sqlite3.Connection) -> list[str]:
    applied = {
        str(row["version"])
        for row in conn.execute("select version from schema_migrations").fetchall()
    }
    return [name for name, _ in iter_migration_scripts() if name not in applied]


def run_release_checks(
    conn: sqlite3.Connection,
    paths: RuntimePaths,
) -> dict[str, Any]:
    paths.create()
    checks: list[dict[str, Any]] = []

    checks.append(
        {
            "name": "version",
            "ok": bool(__version__),
            "details": {"version": __version__},
        }
    )

    config_ok = config_files_present(paths)
    checks.append(
        {
            "name": "config",
            "ok": config_ok,
            "details": {
                "required": list(REQUIRED_CONFIG_FILES),
                "present": config_ok,
            },
        }
    )

    pending = _pending_migrations(conn)
    checks.append(
        {
            "name": "schema",
            "ok": not pending,
            "details": {
                "pending_migrations": pending,
                "package_migrations": _package_migrations(),
            },
        }
    )

    mirror_ok, mirror_errors = _migration_mirror_ok()
    checks.append(
        {
            "name": "migration_mirror",
            "ok": mirror_ok,
            "details": {"errors": mirror_errors},
        }
    )

    preflight = run_upgrade_preflight(conn, paths, record=False)
    checks.append(
        {
            "name": "upgrade_preflight",
            "ok": preflight["status"] != "fail",
            "details": preflight,
        }
    )

    extensions = load_extensions(conn, paths, commit=True)
    checks.append(
        {
            "name": "extensions",
            "ok": not extensions["invalid"],
            "details": extensions,
        }
    )

    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "version": __version__,
        "checks": checks,
        "status": "pass" if ok else "fail",
    }