"""Agent health snapshots computed from durable attempt records."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import CoordinatorConfig
from .operator_hardening import record_agent_health_snapshot


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_bounds(hours: int = 24) -> tuple[str, str]:
    finished = datetime.now(timezone.utc)
    started = finished - timedelta(hours=hours)
    return started.isoformat(), finished.isoformat()


def _recommendation(
    *,
    failures: int,
    attempts_total: int,
    command_available: bool,
) -> str:
    if not command_available:
        return "check_command"
    if attempts_total and failures / attempts_total >= 0.5:
        if failures >= 3:
            return "review_failures"
        return "reduce_concurrency"
    return "ok"


def _health_status(recommendation: str) -> str:
    if recommendation == "ok":
        return "healthy"
    if recommendation in {"check_command", "reduce_concurrency"}:
        return "degraded"
    if recommendation == "disable_temporarily":
        return "unavailable"
    return "degraded"


def compute_agent_health(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    project_id: str | None = None,
    window_hours: int = 24,
) -> list[dict[str, Any]]:
    window_started_at, window_finished_at = _window_bounds(window_hours)
    snapshots: list[dict[str, Any]] = []
    for agent_id, agent in config.agents.items():
        query = """
            select exit_code, started_at, ended_at
            from attempts
            where agent_id = ?
        """
        params: list[Any] = [agent_id]
        if project_id is not None:
            query += " and task_id in (select id from tasks where project_id = ?)"
            params.append(project_id)
        rows = conn.execute(query, params).fetchall()
        successes = sum(1 for row in rows if row["exit_code"] == 0)
        failures = sum(
            1 for row in rows if row["exit_code"] not in (None, 0)
        )
        active_leases = conn.execute(
            """
            select count(*) as cnt
            from task_leases tl
            join tasks t on t.id = tl.task_id
            where tl.agent_id = ?
              and tl.released_at is null
              and tl.expires_at > datetime('now')
            """
            + (" and t.project_id = ?" if project_id else ""),
            (agent_id, project_id) if project_id else (agent_id,),
        ).fetchone()["cnt"]
        last_started = max((str(row["started_at"]) for row in rows), default="")
        ended_values = [str(row["ended_at"]) for row in rows if row["ended_at"]]
        last_completed = max(ended_values, default="")
        command = agent.command.split()[0] if agent.command else ""
        command_available = bool(command and shutil.which(command))
        recommendation = _recommendation(
            failures=failures,
            attempts_total=len(rows),
            command_available=command_available,
        )
        snapshots.append(
            {
                "agent_id": agent_id,
                "role": agent.role,
                "project_id": project_id,
                "status": _health_status(recommendation),
                "window_started_at": window_started_at,
                "window_finished_at": window_finished_at,
                "max_concurrency": agent.max_concurrency,
                "recommendation": recommendation,
                "metrics": {
                    "attempts_total": len(rows),
                    "successes": successes,
                    "failures": failures,
                    "timeouts": 0,
                    "active_leases": int(active_leases),
                    "last_started_at": last_started,
                    "last_completed_at": last_completed,
                    "command_available": command_available,
                },
            }
        )
    return snapshots


def routing_health_penalty(status: str) -> float:
    """Return score penalty used by the agent router for health status."""
    if status == "healthy":
        return 0.0
    if status == "degraded":
        return -25.0
    return -100.0


def snapshot_agent_health(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    project_id: str | None = None,
    commit: bool = True,
) -> bool:
    saved = False
    for item in compute_agent_health(conn, config=config, project_id=project_id):
        record_agent_health_snapshot(
            conn,
            agent_id=item["agent_id"],
            role=str(item.get("role") or ""),
            project_id=project_id,
            status=str(item["status"]),
            window_started_at=str(item["window_started_at"]),
            window_finished_at=str(item["window_finished_at"]),
            metrics=item.get("metrics") or {},
            recommendation=str(item.get("recommendation") or ""),
            commit=False,
        )
        saved = True
    if commit:
        conn.commit()
    return saved