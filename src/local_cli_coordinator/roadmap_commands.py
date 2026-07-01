"""CLI handlers for roadmap graph commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .admin_json import AdminError, emit_envelope, envelope
from .db import connect, init_db
from .projects import find_project_by_path, inspect_project
from .roadmap_graph import set_roadmap_graph_enabled
from .roadmap_import import import_roadmap_markdown
from .roadmap_readiness import select_next_best_work
from .roadmap_reports import build_roadmap_blocked_report, build_roadmap_status_report
from .runtime_paths import resolve_runtime_paths


def _resolve_project_from_cwd(
    conn,
    *,
    cwd: Path | None = None,
) -> tuple[str, Path]:
    root = Path(cwd or ".").resolve()
    row = find_project_by_path(conn, root)
    if row is None:
        raise ValueError(f"project not registered for {root}")
    return str(row["id"]), Path(str(row["canonical_path"]))


def _project_not_registered_error(message: str) -> AdminError:
    return AdminError(
        code="project_not_registered",
        message=message,
        hint="Run `coordinator project add` or `coordinator init`.",
    )


def _roadmap_value_error(exc: ValueError) -> AdminError:
    message = str(exc)
    if message.startswith("project not registered"):
        return _project_not_registered_error(message)
    return AdminError(code="invalid_request", message=message)


def run_roadmap_status_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        project_id, _ = _resolve_project_from_cwd(conn, cwd=Path(args.root))
        payload = build_roadmap_status_report(conn, project_id=project_id)
    except ValueError as exc:
        if args.json:
            return emit_envelope(
                envelope(
                    command="roadmap.status",
                    ok=False,
                    errors=[_roadmap_value_error(exc)],
                )
            )
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    if args.json:
        return emit_envelope(envelope(command="roadmap.status", ok=True, data=payload))
    print(f"roadmap status: {payload['node_count']} nodes, ready={payload['ready_count']}")
    return 0


def run_roadmap_next_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        project_id, _ = _resolve_project_from_cwd(conn, cwd=Path(args.root))
        payload = select_next_best_work(conn, project_id=project_id)
    except ValueError as exc:
        if args.json:
            return emit_envelope(
                envelope(
                    command="roadmap.next",
                    ok=False,
                    errors=[_roadmap_value_error(exc)],
                )
            )
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    if args.json:
        return emit_envelope(envelope(command="roadmap.next", ok=True, data=payload))
    for item in payload.get("items") or []:
        print(f"  {item['title']} ({item['reason']})")
    return 0


def run_roadmap_blocked_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        project_id, _ = _resolve_project_from_cwd(conn, cwd=Path(args.root))
        payload = build_roadmap_blocked_report(conn, project_id=project_id)
    except ValueError as exc:
        if args.json:
            return emit_envelope(
                envelope(
                    command="roadmap.blocked",
                    ok=False,
                    errors=[_roadmap_value_error(exc)],
                )
            )
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    if args.json:
        return emit_envelope(envelope(command="roadmap.blocked", ok=True, data=payload))
    for item in payload.get("items") or []:
        print(f"  {item['title']}: {item['reason']}")
    return 0


def run_roadmap_import_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        project_id, repo_root = _resolve_project_from_cwd(conn, cwd=Path(args.root))
        apply = bool(getattr(args, "apply", False))
        if getattr(args, "dry_run", False):
            apply = False
        payload = import_roadmap_markdown(
            conn,
            project_id=project_id,
            repo_root=repo_root,
            path=Path(args.path),
            apply=apply,
        )
    except ValueError as exc:
        if args.json:
            return emit_envelope(
                envelope(
                    command="roadmap.import",
                    ok=False,
                    errors=[_roadmap_value_error(exc)],
                )
            )
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    command = "roadmap.import"
    if args.json:
        return emit_envelope(envelope(command=command, ok=True, data=payload))
    mode = "applied" if payload.get("applied") else "dry-run"
    print(f"roadmap import {mode}: {len(payload.get('proposed_nodes') or [])} node(s)")
    return 0


def run_roadmap_enable_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        project_id, _ = _resolve_project_from_cwd(conn, cwd=Path(args.root))
        set_roadmap_graph_enabled(conn, project_id=project_id, enabled=True)
        payload = {"project_id": project_id, "graph_enabled": True}
    except ValueError as exc:
        if args.json:
            return emit_envelope(
                envelope(
                    command="roadmap.enable",
                    ok=False,
                    errors=[_roadmap_value_error(exc)],
                )
            )
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    if args.json:
        return emit_envelope(envelope(command="roadmap.enable", ok=True, data=payload))
    print(f"roadmap graph enabled for {payload['project_id']}")
    return 0


def run_roadmap_disable_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    conn = connect(paths.database)
    init_db(conn)
    try:
        project_id, _ = _resolve_project_from_cwd(conn, cwd=Path(args.root))
        set_roadmap_graph_enabled(conn, project_id=project_id, enabled=False)
        payload = {"project_id": project_id, "graph_enabled": False}
    except ValueError as exc:
        if args.json:
            return emit_envelope(
                envelope(
                    command="roadmap.disable",
                    ok=False,
                    errors=[_roadmap_value_error(exc)],
                )
            )
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    if args.json:
        return emit_envelope(envelope(command="roadmap.disable", ok=True, data=payload))
    print(f"roadmap graph disabled for {payload['project_id']}")
    return 0