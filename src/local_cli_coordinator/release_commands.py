"""CLI handlers for backup, restore, upgrade preflight, extensions, and release checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .admin_json import emit_envelope, envelope
from .backup_manager import (
    create_backup,
    get_latest_backup_record,
    mark_backup_verified,
    restore_backup,
    verify_backup,
)
from .db import connect, init_db
from .extension_loader import list_extensions
from .release_checks import run_release_checks
from .runtime_paths import resolve_runtime_paths
from .upgrade_preflight import run_upgrade_preflight


def _resolve_backup_path(args: argparse.Namespace, conn) -> Path:
    if getattr(args, "latest", False):
        record = get_latest_backup_record(conn)
        if record is None:
            raise ValueError("no backup records found")
        return Path(record.backup_path)
    if getattr(args, "backup_path", None):
        return Path(args.backup_path)
    raise ValueError("backup path or --latest is required")


def run_backup_create_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        result = create_backup(conn, paths)
    except (OSError, ValueError) as exc:
        if args.json:
            return emit_envelope(
                envelope(command="backup.create", ok=False, errors=[str(exc)])
            )
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    if args.json:
        return emit_envelope(envelope(command="backup.create", ok=True, data=result))
    print(f"backup created: {result['backup_id']} -> {result['backup_path']}")
    return 0


def run_backup_verify_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        backup_path = _resolve_backup_path(args, conn)
        result = verify_backup(backup_path)
        if result["ok"] and result.get("backup_id"):
            mark_backup_verified(conn, str(result["backup_id"]))
    except ValueError as exc:
        if args.json:
            return emit_envelope(
                envelope(command="backup.verify", ok=False, errors=[str(exc)])
            )
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    if args.json:
        return emit_envelope(
            envelope(command="backup.verify", ok=result["ok"], data=result)
        )
    status = "verified" if result["ok"] else "failed"
    print(f"backup {result.get('backup_id')}: {status}")
    if result.get("errors"):
        for item in result["errors"]:
            print(f"  - {item}", file=sys.stderr)
    return 0 if result["ok"] else 1


def run_restore_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    try:
        backup_path = Path(args.backup_path)
        dry_run = not getattr(args, "apply", False)
        result = restore_backup(
            backup_path,
            paths,
            dry_run=dry_run,
            apply=not dry_run,
            force_compatible_risk=getattr(args, "force_compatible_risk", False),
        )
    except ValueError as exc:
        if args.json:
            return emit_envelope(
                envelope(command="restore", ok=False, errors=[str(exc)])
            )
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        return emit_envelope(envelope(command="restore", ok=True, data=result))
    mode = result.get("mode", "dry_run")
    if mode == "dry_run":
        print(
            f"restore dry-run: would restore {result.get('would_restore_files', 0)} file(s)"
        )
        if result.get("blocked"):
            print("restore blocked by compatibility checks", file=sys.stderr)
            return 1
    else:
        print(f"restore applied: {result.get('restored_count', 0)} file(s)")
    return 0


def run_upgrade_preflight_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        result = run_upgrade_preflight(conn, paths)
    finally:
        conn.close()

    if args.json:
        ok = result["status"] != "fail"
        return emit_envelope(
            envelope(command="upgrade.preflight", ok=ok, data=result)
        )
    print(f"upgrade preflight: {result['status']}")
    for finding in result.get("findings") or []:
        print(f"  [{finding.get('severity')}] {finding.get('message')}")
    return 0 if result["status"] != "fail" else 1


def run_extensions_list_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        result = list_extensions(conn, paths, reload=True)
    finally:
        conn.close()

    if args.json:
        return emit_envelope(
            envelope(command="extensions.list", ok=True, data=result)
        )
    extensions = result.get("extensions") or result.get("enabled") or []
    print(f"extensions ({len(extensions)}):")
    for item in extensions:
        print(
            f"  {item.get('id')} {item.get('name')}@{item.get('version')} "
            f"[{item.get('status')}]"
        )
    return 0


def run_release_check_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        result = run_release_checks(conn, paths)
    finally:
        conn.close()

    if args.json:
        return emit_envelope(
            envelope(command="release.check", ok=result["ok"], data=result)
        )
    print(f"release check: {result['status']}")
    for check in result.get("checks") or []:
        mark = "ok" if check.get("ok") else "fail"
        print(f"  [{mark}] {check.get('name')}")
    return 0 if result["ok"] else 1