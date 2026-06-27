import argparse
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

from .config import load_config, try_load_config
from .db import (
    active_lease_count,
    circuit_breaker_reason,
    connect,
    create_task,
    finish_daemon_run,
    get_task,
    init_db,
    list_task_artifacts,
    list_task_events,
    list_tasks,
    start_daemon_run,
    task_counts,
    transition_task,
)

from .discovery import (
    CommandDiscoveryResult,
    discover_from_command,
    discover_git_recent_commits,
    list_findings,
)
from .commander_service import (
    abandon_goal,
    confirm_goal,
    create_and_preview_goal,
    goal_status,
    pause_goal,
    resume_goal,
    send_chat_message,
)
from .engine import run_continuous_daemon, run_daemon_cycle
from .goals import active_goal
from .reporting import ConsoleReporter, NullReporter
from .locks import acquire_lock, lockfile_path, release_lock
from .commander_memory import (
    COMMANDER_MEMORY_RELATIVE_PATH,
    commander_memory_path,
    goal_status_summary,
)
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
    if getattr(args, "loop", False):
        return _cmd_status_loop(args)
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


def _last_daemon_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "select * from daemon_runs where ended_at is not null "
        "order by id desc limit 1"
    ).fetchone()


def _daily_budget_used(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "select coalesce(sum(tasks_processed), 0) as total "
        "from daemon_runs where date(started_at) = date('now')"
    ).fetchone()
    return row["total"]


def _cmd_status_loop(args: argparse.Namespace) -> int:
    root = Path(args.root)
    config, config_error = try_load_config(root)
    conn = _open_db(root, args.db)
    try:
        counts = task_counts(conn)
        leases = active_lease_count(conn)
        cb_reason = circuit_breaker_reason(conn, config.policy) if config else None
        last_run = _last_daemon_run(conn)
        daily_used = _daily_budget_used(conn)

        print("Loop Status")
        print()

        # Readiness
        print("Readiness:")
        if config_error is not None:
            print(f"  config: degraded ({config_error})")
        else:
            for check in check_loop_readiness(root, config):
                print(f"  {check.name}: [{check.status.upper()}] {check.message}")
        print()

        # Lock
        lock = lockfile_path(root)
        if lock.exists():
            print(f"Lock: {lock}")
        else:
            print("Lock: not held")
        print()

        # Budget / circuit breaker
        if config is not None:
            if cb_reason:
                print(f"Circuit breaker: {cb_reason}")
            else:
                print("Circuit breaker: ok")
            print(
                f"  budget: {daily_used}/{config.policy.max_tasks_per_day} tasks today"
            )
            print(f"  max_consecutive_failures: {config.policy.max_consecutive_failures}")
        else:
            print("Circuit breaker: no config")
        print()

        # Last run / next run
        if last_run is not None:
            print(f"Last run: {last_run['started_at']}")
            interval = config.daemon_policy.loop_interval_seconds if config else 300
            print(f"Next run: ~{interval}s after last run")
        else:
            print("Last run: none")
            print("Next run: not scheduled")
        print()

        # Active leases
        print(f"Active leases: {leases}")
        print()

        # Goal status
        goal_headline, goal_detail = goal_status_summary(conn)
        print(goal_headline)
        print(f"  {goal_detail}")
        print()

        # Task counts
        if counts:
            print("Tasks:")
            for state in sorted(counts):
                print(f"  {state}: {counts[state]}")
            awaiting = counts.get("awaiting_human", 0)
            print(f"\nHuman review pending: {awaiting}")
        else:
            print("Tasks: none")
            print("\nHuman review pending: 0")

        # Memory
        memory_lines: list[str] = []
        if loop_memory_path(root).exists():
            memory_lines.append(f"Loop memory: {LOOP_MEMORY_RELATIVE_PATH.as_posix()}")
        if commander_memory_path(root).exists():
            memory_lines.append(
                f"Commander memory: {COMMANDER_MEMORY_RELATIVE_PATH.as_posix()}"
            )
        if memory_lines:
            print()
            print("\n".join(memory_lines))
    finally:
        conn.close()
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


def _cmd_task_events(args: argparse.Namespace) -> int:
    root = Path(args.root)
    conn = _open_db(root, args.db)
    try:
        try:
            get_task(conn, args.task_id)
        except KeyError as exc:
            print(str(exc))
            return 1
        events = list_task_events(conn, args.task_id)
    finally:
        conn.close()
    if not events:
        print("no events")
        return 0
    for event in events:
        print(f"{event['created_at']}  {event['old_state']} -> {event['new_state']}  {event['note']}")
    return 0


def _cmd_task_artifacts(args: argparse.Namespace) -> int:
    root = Path(args.root)
    conn = _open_db(root, args.db)
    try:
        try:
            get_task(conn, args.task_id)
        except KeyError as exc:
            print(str(exc))
            return 1
        artifacts = list_task_artifacts(conn, args.task_id)
    finally:
        conn.close()
    if not artifacts:
        print("no artifacts")
        return 0
    for artifact in artifacts:
        print(f"{artifact['kind']}: {artifact['path']}")
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


def _format_daemon_cycle_message(result) -> str:
    parts: list[str] = []
    if result.imported_tasks:
        parts.append(_plural(result.imported_tasks, "imported task"))
    if result.planned_tasks:
        parts.append(_plural(result.planned_tasks, "planned task"))
    if result.tasks_processed:
        parts.append(_plural(result.tasks_processed, "processed task"))
    if result.failures:
        parts.append(_plural(result.failures, "failed task"))
    if result.blocked:
        parts.append(_plural(result.blocked, "blocked task"))
    if result.skipped:
        parts.append(_plural(result.skipped, "skipped task"))
    if result.commander_tasks_admitted:
        parts.append(f"{result.commander_tasks_admitted} commander tasks admitted")
    if result.commander_status and result.commander_status.startswith("replenishment_error:"):
        parts.append(result.commander_status)
    elif result.commander_status == "all_rejected":
        parts.append("commander proposals rejected by admission policy")
    if parts:
        return ", ".join(parts)
    if result.stop_reason and result.stop_reason != "no ready tasks":
        return f"stopped: {result.stop_reason}"
    return "no ready tasks"


def _cmd_daemon(args: argparse.Namespace) -> int:
    root = Path(args.root)
    config = load_config(root)

    lock_result = acquire_lock(root, force=getattr(args, "force_lock", False))
    if isinstance(lock_result, str):
        print(lock_result, file=sys.stderr)
        return 1

    reporter = NullReporter() if getattr(args, "quiet", False) else ConsoleReporter()

    conn = _open_db(root, args.db)
    run_id = start_daemon_run(conn)
    tasks_processed = 0
    failures = 0
    stop_reason = None
    message = "no ready tasks"
    try:
        if args.once:
            result = run_daemon_cycle(conn, config, root, reporter=reporter)
            tasks_processed = result.tasks_processed
            failures = result.failures
            stop_reason = result.stop_reason
            message = _format_daemon_cycle_message(result)
        else:
            continuous = run_continuous_daemon(
                conn,
                config,
                root,
                sleep_fn=time.sleep,
                monotonic_fn=time.monotonic,
                reporter=reporter,
            )
            tasks_processed = continuous.tasks_processed
            failures = continuous.failures
            stop_reason = continuous.stop_reason
            message = continuous.message
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


def _cmd_goal(args: argparse.Namespace) -> int:
    root = Path(args.root)
    config = load_config(root)
    conn = _open_db(root, args.db)
    try:
        subcommand = args.goal_subcommand
        if subcommand == "confirm":
            print(confirm_goal(conn, config, root))
        elif subcommand == "status":
            print(goal_status(conn))
        elif subcommand == "pause":
            print(pause_goal(conn))
        elif subcommand == "resume":
            print(resume_goal(conn))
        elif subcommand == "abandon":
            print(abandon_goal(conn))
        else:
            # Treat as objective text
            objective = " ".join(args.goal_args)
            if not objective:
                print("usage: coordinator goal <objective text>")
                print("       coordinator goal confirm|status|pause|resume|abandon")
                return 1
            preview = create_and_preview_goal(conn, config, root, objective)
            if preview.error:
                print(f"Goal draft {preview.goal_id}: preview failed: {preview.error}")
                print("Fix the Commander error, then abandon this draft and create the goal again.")
                return 1
            print(f"Goal draft {preview.goal_id}: {preview.progress_summary}")
            for i, task in enumerate(preview.proposals, 1):
                print(f"  {i}. {task.title} ({task.repo})")
            print(f"\nRun 'coordinator goal confirm' to activate.")
    finally:
        conn.close()
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    root = Path(args.root)
    config = load_config(root)
    conn = _open_db(root, args.db)
    try:
        goal = active_goal(conn)
        if goal is None:
            print("No goal. Create one first with: coordinator goal <objective>")
            return 1
        if goal["status"] in {"completed", "failed", "abandoned"}:
            print(f"Goal is {goal['status']}. Chat is unavailable for terminal goals.")
            return 1

        print(f"Goal: {goal['title']} ({goal['status']})")
        if goal["status"] == "draft":
            print("Commands: /status, /start, /pause, /resume, /quit")
        else:
            print("Commands: /status, /pause, /resume, /quit")
        print()
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line == "/quit":
                break
            elif line == "/status":
                print(goal_status(conn))
            elif line == "/start":
                if goal["status"] != "draft":
                    print("Goal is already active. Use /status, /pause, or /resume.")
                    continue
                result = confirm_goal(conn, config, root)
                print(result)
                if "activated" in result:
                    break
            elif line == "/pause":
                print(pause_goal(conn))
            elif line == "/resume":
                print(resume_goal(conn))
            else:
                print(send_chat_message(conn, config, root, goal["id"], line))
    finally:
        conn.close()
    return 0


def _cmd_digest(args: argparse.Namespace) -> int:
    from .digest import write_daily_digest

    root = Path(args.root)
    db_path = _db_path(root, args.db)
    if not db_path.exists():
        print(f"database does not exist: {db_path}", file=sys.stderr)
        return 1

    conn = _open_db(root, args.db)
    try:
        out_path = write_daily_digest(conn, root)
    finally:
        conn.close()
    print(f"wrote daily digest to {out_path}")
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


def _cmd_cleanup_worktrees(args: argparse.Namespace) -> int:
    from .cli_admin_ops import run_cleanup_worktrees

    apply = getattr(args, "apply", False)
    dry_run = getattr(args, "dry_run", False) or not apply
    code, output = run_cleanup_worktrees(
        Path(args.root),
        args.db,
        dry_run=dry_run,
        apply=apply,
        confirm=getattr(args, "confirm", None),
        force=getattr(args, "force", False),
        project_id=getattr(args, "project", None),
    )
    print(output)
    return code


def _cmd_task_rollback(args: argparse.Namespace) -> int:
    from .cli_admin_ops import run_task_rollback

    apply = getattr(args, "apply", False)
    dry_run = getattr(args, "dry_run", False) or not apply
    code, output = run_task_rollback(
        Path(args.root),
        args.db,
        args.task_id,
        dry_run=dry_run,
        apply=apply,
        confirm=getattr(args, "confirm", None),
    )
    print(output)
    return code


def _cmd_supervisor_drain(args: argparse.Namespace) -> int:
    from .cli_admin_ops import run_supervisor_drain

    code, output = run_supervisor_drain(
        Path(args.root),
        args.db,
        dry_run=True,
    )
    print(output)
    return code


def _cmd_discover(args: argparse.Namespace) -> int:
    root = Path(args.root)
    config, config_error = try_load_config(root)
    if config_error is not None:
        print(f"error: {config_error}", file=sys.stderr)
        return 1
    if not config.discovery_sources:
        print("no discovery sources configured")
        return 0

    discovered = 0
    skipped = 0
    failed = 0

    for source in config.discovery_sources.values():
        if source.type == "git_recent_commits":
            for repo_id, repo_config in config.repos.items():
                if not source.repos.get(repo_id, False):
                    skipped += 1
                    continue
                try:
                    findings = discover_git_recent_commits(
                        root=root,
                        source_id=source.id,
                        repo_id=repo_id,
                        repo_path=repo_config.path,
                        enabled_repos=source.repos,
                        persist=True,
                    )
                    discovered += len(findings)
                except Exception as exc:
                    print(f"error in {source.id}/{repo_id}: {exc}", file=sys.stderr)
                    failed += 1
        elif source.type in ("command", "ci_command", "issue_command"):
            for repo_id in config.repos:
                if not source.repos.get(repo_id, False):
                    skipped += 1
                    continue
                if source.command is None:
                    skipped += 1
                    continue
                result = discover_from_command(
                    root=root,
                    source_id=source.id,
                    command=source.command,
                    repo_id=repo_id,
                    enabled_repos=source.repos,
                    persist=True,
                )
                discovered += len(result.findings)
                failed += len(result.failures)
        elif source.type == "inbox":
            # Inbox is handled by `inbox scan`, not discovery
            skipped += 1
        else:
            skipped += 1

    total = list_findings(root)
    print(f"discovered: {discovered}")
    print(f"skipped: {skipped}")
    print(f"failed: {failed}")
    print(f"total findings on disk: {len(total)}")
    return 1 if failed else 0


def _cmd_digest(args: argparse.Namespace) -> int:
    from .digest import write_daily_digest
    root = Path(args.root).resolve()
    db_path = _db_path(root, args.db)
    if not db_path.exists():
        print(f"error: {db_path} does not exist. run doctor?", file=sys.stderr)
        return 1

    conn = _open_db(root, args.db)
    try:
        out_path = write_daily_digest(conn, root)
        print(f"wrote daily digest to {out_path}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def _cmd_supervisor_start(args: argparse.Namespace) -> int:
    import logging
    import os
    import threading
    from datetime import datetime, timezone

    from .runtime_paths import resolve_runtime_paths
    from .supervisor_identity import build_ping_result

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log = logging.getLogger("supervisor")

    paths = resolve_runtime_paths()
    paths.create()
    if not args.foreground:
        from .supervisor_process import (
            SupervisorReadinessError,
            ensure_supervisor,
            missing_config_file,
        )

        missing = missing_config_file(paths)
        if missing is not None:
            print(f"error: missing config file: {missing}", file=sys.stderr)
            return 1
        try:
            result = ensure_supervisor(paths)
        except SupervisorReadinessError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if result.attached:
            print("Supervisor is running")
        else:
            print("Supervisor started")
            print("Supervisor is running")
        print(f"socket: {paths.socket}")
        if result.pid is not None:
            print(f"pid: {result.pid}")
        return 0

    from .supervisor_server import SupervisorServer, SupervisorServerError
    from .supervisor_events import EventBroker
    from .supervisor_capacity import SharedCapacity
    from .supervisor_methods import SupervisorMethods
    from .supervisor_scheduler import FairProjectScheduler
    from .supervisor import MultiProjectSupervisor
    from .db import connect, init_db

    from .config_runtime import load_config_for_paths

    config = load_config_for_paths(paths)

    # Discover registered projects from tasks and the projects registry
    from .projects import list_projects

    conn = connect(paths.database)
    init_db(conn)
    project_ids = sorted({
        row["project_id"]
        for row in conn.execute("select distinct project_id from tasks").fetchall()
    } | {row["id"] for row in list_projects(conn)})
    conn.close()

    if not project_ids:
        project_ids = ["legacy-default"]

    # Assemble shared components
    broker = EventBroker()
    capacity = SharedCapacity()
    methods = SupervisorMethods(broker=broker, config=config, paths=paths)
    scheduler = FairProjectScheduler(project_ids)

    sup = MultiProjectSupervisor(
        paths=paths,
        scheduler=scheduler,
        broker=broker,
        capacity=capacity,
        methods=methods,
        config=config,
    )

    # Build handler that delegates to methods + system commands
    from .supervisor_protocol import RequestEnvelope, ResponseEnvelope, PROTOCOL_VERSION

    supervisor_pid = os.getpid()
    supervisor_started_at = datetime.now(timezone.utc).isoformat()

    def handler(request: RequestEnvelope) -> ResponseEnvelope:
        if request.method == "system.ping":
            status = sup.status()
            return ResponseEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request.request_id,
                ok=True,
                result=build_ping_result(
                    pid=supervisor_pid,
                    started_at=supervisor_started_at,
                    active_workers=int(status.get("active_tasks", 0)),
                    extra=status,
                ),
                error=None,
            )
        if request.method == "system.shutdown":
            sup.request_shutdown()
            server.request_shutdown()
            return ResponseEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request.request_id,
                ok=True,
                result={"shutting_down": True},
                error=None,
            )
        conn = connect(paths.database)
        init_db(conn)
        try:
            return methods.handle(conn, request)
        finally:
            conn.close()

    try:
        server = SupervisorServer(paths, handler=handler, methods=methods)
    except SupervisorServerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    log.info("Supervisor listening on %s", paths.socket)
    log.info("Projects: %s", ", ".join(project_ids))

    # Tick loop in a background thread
    import time

    def tick_loop():
        while not sup.is_shutdown_requested():
            try:
                sup.tick()
            except Exception:
                log.exception("tick failed")
            time.sleep(1)

    tick_thread = threading.Thread(target=tick_loop, daemon=True)
    tick_thread.start()

    def ownership_watchdog() -> None:
        while not sup.is_shutdown_requested():
            if server._server_pid is None:
                time.sleep(0.2)
                continue
            if not server.owns_runtime():
                log.warning("supervisor lost runtime ownership; shutting down")
                sup.request_shutdown()
                server.request_shutdown()
                return
            time.sleep(2.0)

    watchdog_thread = threading.Thread(target=ownership_watchdog, daemon=True)
    watchdog_thread.start()

    try:
        server.serve_forever()
    except SupervisorServerError as exc:
        log.error("server error: %s", exc)
        return 1
    finally:
        log.info("Shutting down, waiting for workers...")
        sup.request_shutdown()
        if not sup.join_workers(timeout=30.0, shutdown=True):
            log.error("Shutdown incomplete: workers did not finish in time")
            return 1
        log.info("Shutdown complete")

    return 0


def _cmd_supervisor_status(args: argparse.Namespace) -> int:
    from .runtime_paths import resolve_runtime_paths
    from .supervisor_server import send_request
    from .supervisor_protocol import RequestEnvelope
    paths = resolve_runtime_paths()
    if not paths.socket.exists():
        print("Supervisor is not running")
        return 1
    try:
        response = send_request(paths.socket, RequestEnvelope(
            protocol_version=1,
            request_id="status-1",
            project_id=None,
            method="system.ping",
            params={},
        ))
        if response.ok:
            print("Supervisor is running")
            print(f"socket: {paths.socket}")
        else:
            print(f"Supervisor error: {response.error}")
            return 1
    except Exception as exc:
        print(f"Cannot reach Supervisor: {exc}")
        return 1
    return 0


def _cmd_supervisor_restart(args: argparse.Namespace) -> int:
    from .runtime_paths import resolve_runtime_paths
    from .supervisor_process import (
        SupervisorProcessError,
        SupervisorReadinessError,
        restart_supervisor,
    )

    paths = resolve_runtime_paths()
    try:
        result = restart_supervisor(paths)
    except (SupervisorProcessError, SupervisorReadinessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Supervisor restarted")
    print("Supervisor is running")
    print(f"socket: {paths.socket}")
    if result.pid is not None:
        print(f"pid: {result.pid}")
    return 0


def _cmd_supervisor_stop(args: argparse.Namespace) -> int:
    from .runtime_paths import resolve_runtime_paths
    from .supervisor_server import send_request
    from .supervisor_protocol import RequestEnvelope
    paths = resolve_runtime_paths()
    if not paths.socket.exists():
        print("Supervisor is not running")
        return 1
    try:
        response = send_request(paths.socket, RequestEnvelope(
            protocol_version=1,
            request_id="stop-1",
            project_id=None,
            method="system.shutdown",
            params={},
        ))
        if response.ok:
            print("Supervisor shutting down")
        else:
            print(f"Supervisor error: {response.error}")
            return 1
    except Exception as exc:
        print(f"Cannot reach Supervisor: {exc}")
        return 1
    return 0


def _cmd_project_inspect(args: argparse.Namespace) -> int:
    from .projects import inspect_project
    path = Path(args.path).resolve()
    try:
        draft = inspect_project(path)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(f"canonical_path: {draft.canonical_path}")
    print(f"repo_id: {draft.repo_id}")
    print(f"default_branch: {draft.default_branch}")
    print(f"branch_prefix: {draft.branch_prefix}")
    return 0


def _cmd_project_add(args: argparse.Namespace) -> int:
    from .projects import inspect_project, register_project
    from .runtime_paths import resolve_runtime_paths
    if not args.yes:
        print("Refusing to register without --yes confirmation.")
        print(f"Run: coordinator project add {args.path} --yes")
        return 1
    path = Path(args.path).resolve()
    try:
        draft = inspect_project(path)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    paths = resolve_runtime_paths()
    conn = connect(paths.database)
    init_db(conn)
    try:
        project_id = register_project(conn, draft, confirmed=True)
    finally:
        conn.close()
    print(f"registered: {project_id}")
    print(f"canonical_path: {draft.canonical_path}")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    from .global_migration import migrate_legacy_root
    from .runtime_paths import resolve_runtime_paths

    source = Path(args.source).resolve()
    if not source.exists():
        print(f"error: source not found: {source}", file=sys.stderr)
        return 1

    paths = resolve_runtime_paths()

    if args.dry_run:
        try:
            result = migrate_legacy_root(source, paths, dry_run=True)
            print(f"dry run: {result.status}")
        except Exception as exc:
            print(f"dry run failed: {exc}", file=sys.stderr)
            return 1
        return 0

    if not args.yes:
        print("Refusing to migrate without --yes confirmation.")
        print(f"Run: coordinator migrate --source {args.source} --yes")
        return 1

    try:
        result = migrate_legacy_root(source, paths)
        print(f"status: {result.status}")
        if result.backup_path:
            print(f"backup: {result.backup_path}")
    except Exception as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1
    return 0


_ADMIN_COMMANDS = frozenset({
    "daemon",
    "status",
    "doctor",
    "digest",
    "discover",
    "inbox",
    "task",
    "agent",
    "repo",
    "goal",
    "chat",
    "logs",
    "supervisor",
    "project",
    "migrate",
    "config",
})

_PROMPT_FLAGS = frozenset({
    "--print",
    "-p",
    "--prompt",
    "--continue",
    "--resume",
    "--fork",
    "--no-tui",
    "--mode",
    "--tools",
    "--no-tools",
    "--exclude-tools",
})


def _skip_global_options(argv: list[str], index: int) -> int:
    """Advance past ``--root`` / ``--db`` option pairs."""
    while index < len(argv):
        if argv[index] in ("--root", "--db") and index + 1 < len(argv):
            index += 2
            continue
        break
    return index


def is_prompt_argv(argv: list[str]) -> bool:
    """Return True when *argv* should use the Pi-inspired prompt parser."""
    if not argv:
        return False
    index = _skip_global_options(argv, 0)
    if index >= len(argv):
        return False
    if argv[index] in _ADMIN_COMMANDS:
        return False
    if any(token in _PROMPT_FLAGS for token in argv):
        return True
    # Single positional string that is not an admin subcommand.
    return not argv[index].startswith("-")


def _argv_requests_mode(argv: list[str], mode: str) -> bool:
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--mode":
            if index + 1 < len(argv) and argv[index + 1] == mode:
                return True
            index += 2
            continue
        if token.startswith("--mode=") and token.split("=", 1)[1] == mode:
            return True
        index += 1
    return False


class PromptArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, rpc_mode: bool = False, **kwargs):
        self._rpc_mode = rpc_mode
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        if self._rpc_mode:
            from .cli_chat import _emit_rpc, _local_rpc_envelope

            _emit_rpc(_local_rpc_envelope(ok=False, error=message))
            raise SystemExit(2)
        super().error(message)


def build_prompt_parser(*, rpc_mode: bool = False) -> PromptArgumentParser:
    parser = PromptArgumentParser(prog="coordinator", rpc_mode=rpc_mode)
    parser.add_argument("--root", default=".")
    parser.add_argument("--db", default="coordinator.db")
    parser.add_argument("-p", "--prompt", dest="prompt_flag", default=None)
    parser.add_argument("prompt_words", nargs="*", default=[])
    parser.add_argument("--print", dest="print_mode", action="store_true")
    parser.add_argument("--mode", choices=("text", "json", "rpc"), default="text")
    session = parser.add_mutually_exclusive_group()
    session.add_argument("--continue", dest="continue_goal", action="store_true")
    session.add_argument("--resume", nargs="?", const="", default=None, metavar="GOAL_ID")
    session.add_argument("--fork", type=int, default=None, metavar="GOAL_ID")
    tool_group = parser.add_mutually_exclusive_group()
    tool_group.add_argument("--tools", default=None, metavar="TOOLS")
    tool_group.add_argument("--no-tools", dest="no_tools", action="store_true")
    parser.add_argument("--exclude-tools", dest="exclude_tools", default=None, metavar="TOOLS")
    parser.add_argument("--no-tui", dest="no_tui", action="store_true")
    return parser


class PromptNormalizeError(ValueError):
    """Raised when prompt flag normalization fails."""


def normalize_prompt_args(args: argparse.Namespace) -> None:
    """Derive ``prompt_text`` and apply headless mode flags."""
    from .execution_policy import parse_tool_csv

    if args.print_mode or args.mode in {"json", "rpc"}:
        args.no_tui = True
    try:
        args.tools = parse_tool_csv(getattr(args, "tools", None))
        args.exclude_tools = parse_tool_csv(getattr(args, "exclude_tools", None))
    except ValueError as exc:
        raise PromptNormalizeError(str(exc)) from exc
    context_tokens: list[str] = []
    prompt_parts: list[str] = []
    for word in args.prompt_words:
        if word.startswith("@@"):
            prompt_parts.append(word[1:])
        elif word.startswith("@") and len(word) > 1:
            context_tokens.append(word[1:])
        else:
            prompt_parts.append(word)
    args.context_file_tokens = context_tokens
    parts: list[str] = []
    if context_tokens:
        if args.prompt_flag:
            parts.append(args.prompt_flag)
        parts.extend(prompt_parts)
    else:
        parts.extend(prompt_parts)
        if args.prompt_flag:
            parts.append(args.prompt_flag)
    args.prompt_text = " ".join(parts).strip()
    args.command = "prompt"


def _cmd_prompt(args: argparse.Namespace) -> int:
    from .cli_chat import run_cli_prompt

    return run_cli_prompt(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coordinator")
    parser.add_argument("--root", default=".")
    parser.add_argument("--db", default="coordinator.db")
    subparsers = parser.add_subparsers(dest="command")

    daemon = subparsers.add_parser("daemon")
    daemon.add_argument("--once", action="store_true")
    daemon.add_argument("--force-lock", action="store_true")
    daemon.add_argument("--quiet", action="store_true")
    status = subparsers.add_parser("status")
    status.add_argument("--loop", action="store_true")
    subparsers.add_parser("doctor")
    subparsers.add_parser("digest")
    discover = subparsers.add_parser("discover")
    discover.add_argument("--once", action="store_true")

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
    task_subparsers.add_parser("events").add_argument("task_id")
    task_subparsers.add_parser("artifacts").add_argument("task_id")
    task_rollback = task_subparsers.add_parser("rollback")
    task_rollback.add_argument("task_id")
    task_rollback.add_argument("--dry-run", action="store_true")
    task_rollback.add_argument("--apply", action="store_true")
    task_rollback.add_argument("--confirm")

    agent = subparsers.add_parser("agent")
    agent_subparsers = agent.add_subparsers(dest="agent_command")
    agent_subparsers.required = True
    agent_subparsers.add_parser("list")

    repo = subparsers.add_parser("repo")
    repo_subparsers = repo.add_subparsers(dest="repo_command")
    repo_subparsers.required = True
    repo_subparsers.add_parser("list")
    cleanup = repo_subparsers.add_parser("cleanup-worktrees")
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.add_argument("--apply", action="store_true")
    cleanup.add_argument("--confirm")
    cleanup.add_argument("--force", action="store_true")
    cleanup.add_argument("--project")

    # Goal command with nargs="*"
    goal = subparsers.add_parser("goal")
    goal.add_argument("goal_args", nargs="*", default=[])

    # Chat command
    subparsers.add_parser("chat")

    subparsers.add_parser("config")

    subparsers.add_parser("logs").add_argument("task_id")

    # Supervisor commands
    supervisor = subparsers.add_parser("supervisor")
    supervisor_subparsers = supervisor.add_subparsers(dest="supervisor_command")
    supervisor_subparsers.required = True
    start = supervisor_subparsers.add_parser("start")
    start.add_argument("--foreground", action="store_true")
    supervisor_subparsers.add_parser("status")
    supervisor_subparsers.add_parser("stop")
    supervisor_subparsers.add_parser("restart")
    supervisor_drain = supervisor_subparsers.add_parser("drain")
    supervisor_drain.add_argument("--dry-run", action="store_true")

    # Project commands
    project = subparsers.add_parser("project")
    project_subparsers = project.add_subparsers(dest="project_command")
    project_subparsers.required = True
    project_subparsers.add_parser("inspect").add_argument("path")
    project_add = project_subparsers.add_parser("add")
    project_add.add_argument("path")
    project_add.add_argument("--yes", action="store_true")

    # Migrate command
    migrate = subparsers.add_parser("migrate")
    migrate.add_argument("--source", required=True, help="Legacy root directory")
    migrate.add_argument("--dry-run", action="store_true", help="Validate without writing")
    migrate.add_argument("--yes", action="store_true", help="Confirm migration")

    return parser


def main(argv: list[str] | None = None) -> int:
    cli_argv = list(argv) if argv is not None else sys.argv[1:]
    if is_prompt_argv(cli_argv):
        rpc_mode = _argv_requests_mode(cli_argv, "rpc")
        prompt_parser = build_prompt_parser(rpc_mode=rpc_mode)
        try:
            args = prompt_parser.parse_args(cli_argv)
        except SystemExit as exc:
            code = exc.code
            return int(code) if isinstance(code, int) else 1
        try:
            normalize_prompt_args(args)
        except PromptNormalizeError as exc:
            if args.mode == "rpc":
                from .cli_chat import _emit_rpc, _local_rpc_envelope

                _emit_rpc(_local_rpc_envelope(ok=False, error=str(exc)))
                return 2
            prompt_parser.error(str(exc))
        return _cmd_prompt(args)

    parser = build_parser()
    args = parser.parse_args(cli_argv)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "digest":
        return _cmd_digest(args)
    if args.command == "discover":
        return _cmd_discover(args)
    if args.command == "repo" and args.repo_command == "cleanup-worktrees":
        return _cmd_cleanup_worktrees(args)
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
    if args.command == "task" and args.task_command == "events":
        return _cmd_task_events(args)
    if args.command == "task" and args.task_command == "artifacts":
        return _cmd_task_artifacts(args)
    if args.command == "task" and args.task_command == "rollback":
        return _cmd_task_rollback(args)
    if args.command == "daemon":
        return _cmd_daemon(args)
    if args.command == "goal":
        # Parse subcommand from first arg
        if args.goal_args and args.goal_args[0] in ("confirm", "status", "pause", "resume", "abandon"):
            args.goal_subcommand = args.goal_args[0]
            args.goal_args = args.goal_args[1:]
        else:
            args.goal_subcommand = None
        return _cmd_goal(args)
    if args.command == "chat":
        return _cmd_chat(args)
    if args.command == "logs":
        return _cmd_logs(args)
    if args.command == "supervisor" and args.supervisor_command == "start":
        return _cmd_supervisor_start(args)
    if args.command == "supervisor" and args.supervisor_command == "status":
        return _cmd_supervisor_status(args)
    if args.command == "supervisor" and args.supervisor_command == "stop":
        return _cmd_supervisor_stop(args)
    if args.command == "supervisor" and args.supervisor_command == "restart":
        return _cmd_supervisor_restart(args)
    if args.command == "supervisor" and args.supervisor_command == "drain":
        return _cmd_supervisor_drain(args)
    if args.command == "project" and args.project_command == "inspect":
        return _cmd_project_inspect(args)
    if args.command == "project" and args.project_command == "add":
        return _cmd_project_add(args)
    if args.command == "migrate":
        return _cmd_migrate(args)
    if args.command is None:
        if not cli_argv:
            from .tui_launcher import launch_tui

            return launch_tui(start_path=Path(args.root).resolve())
        parser.error(f"unknown command: {cli_argv[0]}")
    if args.command == "config":
        from .cli_config import run_config_command

        return run_config_command()
    print(f"{args.command}: command is registered")
    return 0
