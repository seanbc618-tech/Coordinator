import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

from .config import load_config, try_load_config
from .db import (
    circuit_breaker_reason,
    connect,
    create_task,
    finish_daemon_run,
    get_task,
    init_db,
    list_tasks,
    next_ready_task,
    start_daemon_run,
    task_counts,
    transition_task,
)
from .engine import run_one_ready_task
from .locks import acquire_lock, release_lock
from .memory import LOOP_MEMORY_RELATIVE_PATH, loop_memory_path
from .policy import check_task_draft
from .readiness import check_loop_readiness
from .tasks import scan_inbox


def _db_path(root: Path, db: str) -> Path:
    path = Path(db)
    if not path.is_absolute():
        path = root / path
    return path


def _open_db(root: Path, db: str) -> sqlite3.Connection:
    conn = connect(_db_path(root, db))
    init_db(conn)
    return conn


def _plural(count: int, singular: str) -> str:
    return f"{count} {singular}" if count == 1 else f"{count} {singular}s"


def _move_to_accepted(root: Path, source_path: str) -> None:
    source = root / source_path
    accepted = root / "tasks" / "accepted" / source.name
    accepted.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(accepted))


def _cmd_doctor(args: argparse.Namespace) -> int:
    root = Path(args.root)
    config, config_error = try_load_config(root)
    print("Coordinator doctor")
    print(f"root: {args.root}")
    print(f"status: {'degraded' if config_error is not None else 'ok'}")
    print()
    print("Loop readiness")
    if config_error is not None:
        print(f"  [WARN] configuration: {config_error}")
    for check in check_loop_readiness(root, config):
        print(f"  [{check.status.upper()}] {check.name}: {check.message}")
    return 0


def _cmd_inbox_scan(args: argparse.Namespace) -> int:
    root = Path(args.root)
    config = load_config(root)
    conn = _open_db(root, args.db)
    imported = 0
    rejected: list[tuple[str, list[str]]] = []
    try:
        for draft in scan_inbox(root):
            result = check_task_draft(draft, config.policy)
            reasons = list(result.reasons)
            if draft.repo not in config.repos:
                reasons.append(f"repo is not allowlisted: {draft.repo}")
            if reasons:
                rejected.append((draft.source_path, reasons))
                continue
            create_task(
                conn,
                title=draft.title,
                repo=draft.repo,
                source_path=draft.source_path,
                priority=draft.priority,
                capabilities=draft.capabilities,
                goal=draft.goal,
                acceptance_criteria=draft.acceptance_criteria,
                verification_commands=draft.verification_commands,
            )
            _move_to_accepted(root, draft.source_path)
            imported += 1
    finally:
        conn.close()
    print(f"imported {_plural(imported, 'task')}")
    for source_path, reasons in rejected:
        print(f"rejected {source_path}: {'; '.join(reasons)}", file=sys.stderr)
    return 1 if rejected else 0


def _cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root)
    conn = _open_db(root, args.db)
    try:
        counts = task_counts(conn)
    finally:
        conn.close()
    if loop_memory_path(root).exists():
        print(f"loop memory: {LOOP_MEMORY_RELATIVE_PATH.as_posix()}")
    if not counts:
        print("no tasks")
        return 0
    for state in sorted(counts):
        print(f"{state}: {counts[state]}")
    return 0


def _cmd_task_list(args: argparse.Namespace) -> int:
    root = Path(args.root)
    conn = _open_db(root, args.db)
    try:
        rows = list_tasks(conn)
    finally:
        conn.close()
    if not rows:
        print("no tasks")
        return 0
    for row in rows:
        print(f"{row['id']} {row['state']} {row['title']}")
    return 0


def _cmd_task_show(args: argparse.Namespace) -> int:
    root = Path(args.root)
    conn = _open_db(root, args.db)
    try:
        task = get_task(conn, args.task_id)
        print(f"id: {task['id']}")
        print(f"state: {task['state']}")
        print(f"title: {task['title']}")
        print(f"repo: {task['repo']}")
    except KeyError as exc:
        print(str(exc))
        return 1
    finally:
        conn.close()
    return 0


def _cmd_task_transition(args: argparse.Namespace, state: str, note: str) -> int:
    root = Path(args.root)
    conn = _open_db(root, args.db)
    try:
        transition_task(conn, args.task_id, state, note)
    except KeyError as exc:
        print(str(exc))
        return 1
    finally:
        conn.close()
    print(f"{args.task_id} {state}")
    return 0


def _cmd_daemon(args: argparse.Namespace) -> int:
    if not args.once:
        print("daemon requires --once")
        return 2
    root = Path(args.root)
    config = load_config(root)

    lock_result = acquire_lock(root, force=getattr(args, "force_lock", False))
    if isinstance(lock_result, str):
        print(lock_result, file=sys.stderr)
        return 1

    conn = _open_db(root, args.db)
    run_id = start_daemon_run(conn)
    tasks_processed = 0
    failures = 0
    stop_reason = None
    try:
        stop_reason = circuit_breaker_reason(conn, config.policy)
        if stop_reason is not None:
            message = f"stopped: {stop_reason}"
        else:
            candidate = next_ready_task(conn)
            processed = run_one_ready_task(conn, config, root)
            tasks_processed = int(processed)
            if processed and candidate is not None:
                failures = int(get_task(conn, candidate["id"])["state"] == "failed")
            stop_reason = None if processed else "no ready tasks"
            message = "processed 1 task" if processed else "no ready tasks"
    except Exception as exc:
        failures = 1
        stop_reason = f"daemon error: {type(exc).__name__}: {exc}"
        raise
    finally:
        finish_daemon_run(
            conn,
            run_id,
            tasks_processed=tasks_processed,
            failures=failures,
            stop_reason=stop_reason,
        )
        conn.close()
        release_lock(root)
    print(message)
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    root = Path(args.root)
    conn = _open_db(root, args.db)
    try:
        get_task(conn, args.task_id)
        attempts = conn.execute(
            "select log_path from attempts where task_id = ? order by id",
            (args.task_id,),
        ).fetchall()
        artifacts = conn.execute(
            "select kind, path from artifacts where task_id = ? order by id",
            (args.task_id,),
        ).fetchall()
    except KeyError as exc:
        print(str(exc))
        return 1
    finally:
        conn.close()
    if not attempts and not artifacts:
        print("no logs")
        return 0
    for attempt in attempts:
        print(attempt["log_path"])
    for artifact in artifacts:
        print(f"{artifact['kind']}: {artifact['path']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coordinator")
    parser.add_argument("--root", default=".")
    parser.add_argument("--db", default="coordinator.db")
    subparsers = parser.add_subparsers(dest="command")

    daemon = subparsers.add_parser("daemon")
    daemon.add_argument("--once", action="store_true")
    daemon.add_argument("--force-lock", action="store_true")
    subparsers.add_parser("status")
    subparsers.add_parser("doctor")

    inbox = subparsers.add_parser("inbox")
    inbox_subparsers = inbox.add_subparsers(dest="inbox_command")
    inbox_subparsers.required = True
    inbox_subparsers.add_parser("scan")

    task = subparsers.add_parser("task")
    task_subparsers = task.add_subparsers(dest="task_command")
    task_subparsers.required = True
    task_subparsers.add_parser("list")
    task_subparsers.add_parser("show").add_argument("task_id")
    task_subparsers.add_parser("retry").add_argument("task_id")
    task_subparsers.add_parser("block").add_argument("task_id")

    agent = subparsers.add_parser("agent")
    agent_subparsers = agent.add_subparsers(dest="agent_command")
    agent_subparsers.required = True
    agent_subparsers.add_parser("list")

    repo = subparsers.add_parser("repo")
    repo_subparsers = repo.add_subparsers(dest="repo_command")
    repo_subparsers.required = True
    repo_subparsers.add_parser("list")

    subparsers.add_parser("logs").add_argument("task_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "inbox" and args.inbox_command == "scan":
        return _cmd_inbox_scan(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "task" and args.task_command == "list":
        return _cmd_task_list(args)
    if args.command == "task" and args.task_command == "show":
        return _cmd_task_show(args)
    if args.command == "task" and args.task_command == "retry":
        return _cmd_task_transition(args, "ready", "manual retry")
    if args.command == "task" and args.task_command == "block":
        return _cmd_task_transition(args, "blocked", "manual block")
    if args.command == "daemon":
        return _cmd_daemon(args)
    if args.command == "logs":
        return _cmd_logs(args)
    if args.command is None:
        parser.print_help()
        return 0
    print(f"{args.command}: command is registered")
    return 0
    finish_daemon_run,
