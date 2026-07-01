"""CLI handlers for project onboarding and fleet rollout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config_runtime import load_config_for_paths
from .config_snapshots import rollback_config_snapshot
from .db import connect, init_db
from .fleet_rollout import apply_fleet_rollout, scan_fleet
from .onboarding_plan import apply_onboarding_plan, build_onboarding_plan
from .onboarding_profiles import record_profile_run
from .project_inspector import inspect_project_shape
from .runtime_paths import resolve_runtime_paths


def _print_json(ok: bool, result: dict[str, Any] | None = None, *, error: str | None = None) -> int:
    print(json.dumps({"ok": ok, "result": result, "error": error}, ensure_ascii=False))
    return 0 if ok else 1


def inspection_payload(inspection) -> dict[str, Any]:
    return {
        "repo_root": str(inspection.repo_root),
        "repo_id": inspection.repo_id,
        "detected_profile": inspection.detected_profile,
        "recommended_preset": inspection.recommended_preset,
        "confidence": inspection.confidence,
        "findings": inspection.findings,
        "verify_commands": inspection.verify_commands,
    }


def run_project_inspect_command(args: argparse.Namespace) -> int:
    path = Path(args.path)
    json_mode = getattr(args, "json", False)
    record = getattr(args, "record", False)
    allow_non_git = getattr(args, "allow_non_git", False)

    try:
        inspection = inspect_project_shape(path, allow_non_git=allow_non_git)
    except ValueError as exc:
        if json_mode:
            return _print_json(False, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = inspection_payload(inspection)
    if record:
        paths = resolve_runtime_paths()
        paths.create()
        conn = connect(paths.database)
        init_db(conn)
        try:
            record_profile_run(
                conn,
                repo_path=str(inspection.repo_root),
                inspection=inspection,
            )
            conn.commit()
            payload["recorded"] = True
        finally:
            conn.close()

    if json_mode:
        return _print_json(True, payload)

    print(f"canonical_path: {inspection.repo_root}")
    print(f"repo_id: {inspection.repo_id}")
    print(f"detected_profile: {inspection.detected_profile}")
    print(f"recommended_preset: {inspection.recommended_preset}")
    print(f"verify_commands: {', '.join(inspection.verify_commands) or '(none)'}")
    return 0


def run_onboard_command(args: argparse.Namespace) -> int:
    json_mode = getattr(args, "json", False)
    dry_run = getattr(args, "dry_run", False)
    apply = getattr(args, "apply", False)
    preset = getattr(args, "preset", "observe")
    enable_autonomy = getattr(args, "enable_autonomy", False)
    path = Path(args.path)

    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        if dry_run:
            plan = build_onboarding_plan(
                paths,
                conn,
                path,
                preset=preset,
                dry_run=True,
                enable_autonomy=enable_autonomy,
            )
            conn.commit()
            if json_mode:
                return _print_json(True, {"plan": plan})
            print(f"onboard dry-run preset={preset}")
            print(json.dumps(plan, indent=2))
            return 0

        if apply:
            result = apply_onboarding_plan(
                paths,
                conn,
                path,
                preset=preset,
                enable_autonomy=enable_autonomy,
                allow_delivery_policy_change=getattr(
                    args, "allow_delivery_policy_change", False
                ),
            )
            conn.commit()
            if json_mode:
                return _print_json(True, result)
            print(f"onboard applied preset={preset} project_id={result['project_id']}")
            return 0

        message = "Refusing onboard without --dry-run or --apply"
        if json_mode:
            return _print_json(False, error=message)
        print(f"error: {message}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def run_onboard_rollback_command(args: argparse.Namespace) -> int:
    json_mode = getattr(args, "json", False)
    paths = resolve_runtime_paths()
    conn = connect(paths.database)
    init_db(conn)
    try:
        result = rollback_config_snapshot(paths, conn, args.snapshot_id)
        conn.commit()
        if json_mode:
            return _print_json(True, result)
        print(f"rollback restored snapshot {args.snapshot_id}")
        return 0
    except ValueError as exc:
        if json_mode:
            return _print_json(False, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def run_fleet_command(args: argparse.Namespace) -> int:
    json_mode = getattr(args, "json", False)
    root = Path(args.root)
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        if args.fleet_command == "scan":
            result = scan_fleet(root, conn=conn, max_depth=getattr(args, "max_depth", 3))
            if json_mode:
                return _print_json(True, result)
            for entry in result["repos"]:
                print(
                    f"{entry['repo_id']}: {entry['detected_profile']} "
                    f"registered={entry['registered']}"
                )
            return 0

        if args.fleet_command == "apply":
            select = [
                item.strip()
                for item in (getattr(args, "select", "") or "").split(",")
                if item.strip()
            ]
            result = apply_fleet_rollout(
                paths,
                conn,
                root,
                preset=getattr(args, "preset", "observe"),
                select=select,
                enable_autonomy=getattr(args, "enable_autonomy", False),
            )
            conn.commit()
            if json_mode:
                return _print_json(True, result)
            print(f"fleet apply applied={len(result['applied'])} skipped={len(result['skipped'])}")
            return 0

        if json_mode:
            return _print_json(False, error="unknown fleet command")
        print("error: unknown fleet command", file=sys.stderr)
        return 1
    finally:
        conn.close()