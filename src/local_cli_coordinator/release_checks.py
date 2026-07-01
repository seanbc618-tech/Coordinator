"""Release readiness diagnostics for Coordinator installs."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import __version__
from .backup_manager import _package_migrations
from .config_runtime import REQUIRED_CONFIG_FILES, config_files_present
from .db import iter_migration_scripts
from .extension_loader import load_extensions
from .runtime_paths import RuntimePaths
from .upgrade_preflight import run_upgrade_preflight


def _repo_migrations_mirror() -> Path | None:
    """Return repo-root migrations/ only when running from a source checkout."""
    package_dir = Path(__file__).resolve().parent
    if package_dir.name != "local_cli_coordinator":
        return None
    mirror = package_dir.parent.parent / "migrations"
    if mirror.is_dir():
        return mirror
    return None


def _migration_mirror_check() -> dict[str, Any]:
    package = dict(iter_migration_scripts())
    if not package:
        return {
            "ok": False,
            "errors": ["no packaged migrations found"],
            "skipped": False,
            "package_count": 0,
        }

    mirror = _repo_migrations_mirror()
    if mirror is None:
        return {
            "ok": True,
            "errors": [],
            "skipped": True,
            "reason": "source checkout mirror not present (wheel install)",
            "package_count": len(package),
        }

    mirror_files = {
        path.name: path.read_text(encoding="utf-8") for path in mirror.glob("*.sql")
    }
    errors: list[str] = []
    if set(package) != set(mirror_files):
        errors.append("migration mirror file set mismatch")
    for name, body in package.items():
        if mirror_files.get(name) != body:
            errors.append(f"migration mirror mismatch: {name}")
    return {
        "ok": not errors,
        "errors": errors,
        "skipped": False,
        "package_count": len(package),
        "mirror_path": str(mirror),
    }


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

    mirror = _migration_mirror_check()
    checks.append(
        {
            "name": "migration_mirror",
            "ok": mirror["ok"],
            "details": mirror,
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