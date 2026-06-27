"""Durable autonomous run session persistence for Phase 6C."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

ACTIVE_RUN_STATUSES = ("running", "paused")
TERMINAL_RUN_STATUSES = ("stopping", "stopped", "completed", "failed", "expired")
IDLE_WAIT_REASON_MARKERS = ("running task", "no active goal", "no backlog ready")
PRODUCTIVE_DECISIONS = ("admit", "evaluate", "generate")


@dataclass(frozen=True)
class AutonomousRunOptions:
    max_iterations: int = 100
    max_runtime_seconds: int = 28800
    idle_backoff_seconds: int = 30
    max_idle_iterations: int = 12
    mode: str = "continuous"


@dataclass(frozen=True)
class AutonomousRunSnapshot:
    id: str
    project_id: str
    goal_id: int | None
    status: str
    mode: str
    iteration_count: int
    idle_iteration_count: int
    failure_count: int
    last_decision: str | None
    last_reason: str | None
    next_tick_after: str | None
    stop_reason: str | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _row_to_snapshot(row: sqlite3.Row) -> AutonomousRunSnapshot:
    return AutonomousRunSnapshot(
        id=row["id"],
        project_id=row["project_id"],
        goal_id=row["goal_id"],
        status=row["status"],
        mode=row["mode"],
        iteration_count=int(row["iteration_count"]),
        idle_iteration_count=int(row["idle_iteration_count"]),
        failure_count=int(row["failure_count"]),
        last_decision=row["last_decision"],
        last_reason=row["last_reason"],
        next_tick_after=row["next_tick_after"],
        stop_reason=row["stop_reason"],
    )


def _fetch_session(conn: sqlite3.Connection, run_id: str) -> AutonomousRunSnapshot:
    row = conn.execute(
        "select * from autonomous_run_sessions where id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"autonomous run session {run_id!r} not found")
    return _row_to_snapshot(row)


def _is_idle_wait(decision: str, reason: str) -> bool:
    if decision != "wait":
        return False
    lowered = reason.lower()
    return any(marker in lowered for marker in IDLE_WAIT_REASON_MARKERS)


def start_run_session(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    options: AutonomousRunOptions,
    started_by: str = "operator",
) -> AutonomousRunSnapshot:
    """Create one running session per project or return the existing active session."""
    existing = get_active_run_session(conn, project_id=project_id)
    if existing is not None:
        return existing

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    now = _iso_now()
    conn.execute(
        """
        insert into autonomous_run_sessions(
            id, project_id, goal_id, status, mode, started_by,
            max_iterations, max_runtime_seconds, idle_backoff_seconds,
            max_idle_iterations, started_at, updated_at, last_heartbeat_at
        ) values (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            project_id,
            goal_id,
            options.mode,
            started_by,
            options.max_iterations,
            options.max_runtime_seconds,
            options.idle_backoff_seconds,
            options.max_idle_iterations,
            now,
            now,
            now,
        ),
    )
    return _fetch_session(conn, run_id)


def pause_run_session(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> AutonomousRunSnapshot:
    """Move the active running session to paused."""
    session = get_active_run_session(conn, project_id=project_id)
    if session is None or session.status != "running":
        raise ValueError(f"no running autonomous session for project {project_id!r}")
    now = _iso_now()
    conn.execute(
        """
        update autonomous_run_sessions
        set status = 'paused', next_tick_after = null, updated_at = ?
        where id = ?
        """,
        (now, session.id),
    )
    return _fetch_session(conn, session.id)


def resume_run_session(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> AutonomousRunSnapshot:
    """Move the active paused session to running."""
    session = get_active_run_session(conn, project_id=project_id)
    if session is None or session.status != "paused":
        raise ValueError(f"no paused autonomous session for project {project_id!r}")
    now = _iso_now()
    conn.execute(
        """
        update autonomous_run_sessions
        set status = 'running', next_tick_after = null, updated_at = ?, last_heartbeat_at = ?
        where id = ?
        """,
        (now, now, session.id),
    )
    return _fetch_session(conn, session.id)


def stop_run_session(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    reason: str,
) -> AutonomousRunSnapshot:
    """Stop the active running/paused/stopping session."""
    session = get_active_run_session(conn, project_id=project_id)
    if session is None:
        raise ValueError(f"no active autonomous session for project {project_id!r}")
    now = _iso_now()
    conn.execute(
        """
        update autonomous_run_sessions
        set status = 'stopped', stop_reason = ?, ended_at = ?, updated_at = ?,
            next_tick_after = null
        where id = ?
        """,
        (reason, now, now, session.id),
    )
    return _fetch_session(conn, session.id)


def get_active_run_session(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> AutonomousRunSnapshot | None:
    """Return running or paused session for a project."""
    row = conn.execute(
        """
        select * from autonomous_run_sessions
        where project_id = ? and status in ('running', 'paused')
        order by started_at desc
        limit 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_snapshot(row)


def project_has_runnable_run_session(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    now: str | None = None,
) -> bool:
    """Return True only when a running session is due for a tick."""
    session = get_active_run_session(conn, project_id=project_id)
    if session is None or session.status != "running":
        return False
    if session.next_tick_after is None:
        return True
    current = _parse_iso(now) if now is not None else _utc_now()
    next_tick = _parse_iso(session.next_tick_after)
    if next_tick is None:
        return True
    return current >= next_tick


def record_run_step(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    decision: str,
    loop_iteration_id: str | None,
    idle_backoff_seconds: int,
    reason: str = "",
    evaluated_count: int = 0,
    admitted_count: int = 0,
    generated_count: int = 0,
    goal_id: int | None = None,
    project_id: str | None = None,
) -> AutonomousRunSnapshot:
    """Persist a run step, update heartbeat/counters, and return the updated session."""
    row = conn.execute(
        "select * from autonomous_run_sessions where id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"autonomous run session {run_id!r} not found")

    effective_project_id = project_id or row["project_id"]
    effective_goal_id = goal_id if goal_id is not None else row["goal_id"]
    step_id = f"runstep-{uuid.uuid4().hex[:12]}"
    now = _iso_now()
    conn.execute(
        """
        insert into autonomous_run_steps(
            id, run_id, project_id, goal_id, loop_iteration_id,
            decision, reason, evaluated_count, admitted_count, generated_count
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            step_id,
            run_id,
            effective_project_id,
            effective_goal_id,
            loop_iteration_id,
            decision,
            reason,
            evaluated_count,
            admitted_count,
            generated_count,
        ),
    )

    iteration_count = int(row["iteration_count"]) + 1
    idle_iteration_count = int(row["idle_iteration_count"])
    failure_count = int(row["failure_count"])
    status = row["status"]
    stop_reason: str | None = row["stop_reason"]
    ended_at: str | None = row["ended_at"]
    next_tick_after: str | None = None

    if decision in PRODUCTIVE_DECISIONS:
        idle_iteration_count = 0
    elif _is_idle_wait(decision, reason):
        idle_iteration_count += 1

    if decision == "blocked":
        failure_count += 1

    max_iterations = int(row["max_iterations"])
    max_runtime_seconds = int(row["max_runtime_seconds"])
    max_idle_iterations = int(row["max_idle_iterations"])
    started_at = _parse_iso(row["started_at"]) or _utc_now()
    runtime_seconds = (_utc_now() - started_at).total_seconds()

    if iteration_count >= max_iterations:
        status = "completed"
        stop_reason = "max iterations reached"
        ended_at = now
    elif runtime_seconds >= max_runtime_seconds:
        status = "expired"
        stop_reason = "max runtime reached"
        ended_at = now
    elif idle_iteration_count >= max_idle_iterations:
        status = "completed"
        stop_reason = "idle limit reached"
        ended_at = now
    elif decision == "pause":
        status = "paused"
        stop_reason = reason or "loop paused"
    elif decision == "blocked":
        status = "failed"
        stop_reason = reason or "loop blocked"
        ended_at = now
    elif decision == "complete":
        status = "completed"
        stop_reason = reason or "goal complete"
        ended_at = now
    elif status == "running":
        backoff = max(0, idle_backoff_seconds)
        next_tick_after = (_utc_now() + timedelta(seconds=backoff)).isoformat()

    conn.execute(
        """
        update autonomous_run_sessions
        set iteration_count = ?, idle_iteration_count = ?, failure_count = ?,
            last_decision = ?, last_reason = ?, next_tick_after = ?,
            status = ?, stop_reason = ?, ended_at = ?,
            updated_at = ?, last_heartbeat_at = ?
        where id = ?
        """,
        (
            iteration_count,
            idle_iteration_count,
            failure_count,
            decision,
            reason,
            next_tick_after,
            status,
            stop_reason,
            ended_at,
            now,
            now,
            run_id,
        ),
    )
    return _fetch_session(conn, run_id)


def run_snapshot_to_payload(snapshot: AutonomousRunSnapshot) -> dict[str, object]:
    return {
        "id": snapshot.id,
        "status": snapshot.status,
        "mode": snapshot.mode,
        "iteration_count": snapshot.iteration_count,
        "idle_iteration_count": snapshot.idle_iteration_count,
        "failure_count": snapshot.failure_count,
        "last_decision": snapshot.last_decision,
        "last_reason": snapshot.last_reason,
        "next_tick_after": snapshot.next_tick_after,
        "stop_reason": snapshot.stop_reason,
    }