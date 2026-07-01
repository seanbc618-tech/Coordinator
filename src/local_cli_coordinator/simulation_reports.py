"""Persistence helpers for autonomy simulation runs and forecast events."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

SIMULATION_SCOPES = frozenset({"global", "project"})
SIMULATION_STATUSES = frozenset({"completed", "partial", "failed"})
SIMULATION_EVENT_TYPES = frozenset({
    "would_schedule",
    "would_skip",
    "would_require_approval",
    "would_hit_budget",
    "would_block",
    "would_use_agent",
})


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def create_simulation_run(
    conn: sqlite3.Connection,
    *,
    scope: str,
    horizon_hours: float = 8.0,
    project_id: str | None = None,
    inputs: dict[str, Any] | None = None,
    commit: bool = False,
) -> str:
    if scope not in SIMULATION_SCOPES:
        raise ValueError(f"unsupported simulation scope: {scope!r}")
    if scope == "project" and not project_id:
        raise ValueError("project_id is required for project scope")
    run_id = f"sim-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        insert into simulation_runs(
            id, scope, project_id, horizon_hours, status,
            inputs_json, report_json, created_at, finished_at
        ) values (?, ?, ?, ?, 'partial', ?, '{}', ?, null)
        """,
        (
            run_id,
            scope,
            project_id,
            float(horizon_hours),
            json.dumps(inputs or {}),
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return run_id


def record_simulation_event(
    conn: sqlite3.Connection,
    *,
    simulation_run_id: str,
    event_type: str,
    project_id: str | None = None,
    task_id: str | None = None,
    data: dict[str, Any] | None = None,
    commit: bool = False,
) -> str:
    if event_type not in SIMULATION_EVENT_TYPES:
        raise ValueError(f"unsupported simulation event type: {event_type!r}")
    event_id = f"sim-evt-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        insert into simulation_events(
            id, simulation_run_id, event_type, project_id, task_id,
            data_json, created_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            simulation_run_id,
            event_type,
            project_id,
            task_id,
            json.dumps(data or {}),
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return event_id


def finish_simulation_run(
    conn: sqlite3.Connection,
    *,
    simulation_run_id: str,
    status: str,
    report: dict[str, Any],
    commit: bool = False,
) -> None:
    if status not in SIMULATION_STATUSES:
        raise ValueError(f"unsupported simulation status: {status!r}")
    conn.execute(
        """
        update simulation_runs
        set status = ?, report_json = ?, finished_at = ?
        where id = ?
        """,
        (status, json.dumps(report), _iso_now(), simulation_run_id),
    )
    if commit:
        conn.commit()


def get_simulation_run(
    conn: sqlite3.Connection,
    *,
    simulation_run_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "select * from simulation_runs where id = ?",
        (simulation_run_id,),
    ).fetchone()
    if row is None:
        return None
    return _run_row_to_dict(row)


def list_simulation_events(
    conn: sqlite3.Connection,
    *,
    simulation_run_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select * from simulation_events
        where simulation_run_id = ?
        order by created_at, id
        """,
        (simulation_run_id,),
    ).fetchall()
    return [_event_row_to_dict(row) for row in rows]


def get_simulation_report(
    conn: sqlite3.Connection,
    *,
    simulation_run_id: str,
) -> dict[str, Any] | None:
    run = get_simulation_run(conn, simulation_run_id=simulation_run_id)
    if run is None:
        return None
    events = list_simulation_events(conn, simulation_run_id=simulation_run_id)
    return {
        "run": run,
        "events": events,
        "report": run.get("report") or {},
        "forecast": True,
    }


def list_simulation_runs(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if project_id is None:
        rows = conn.execute(
            """
            select * from simulation_runs
            order by created_at desc, id desc
            limit ?
            """,
            (max(1, limit),),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select * from simulation_runs
            where project_id = ? or scope = 'global'
            order by created_at desc, id desc
            limit ?
            """,
            (project_id, max(1, limit)),
        ).fetchall()
    return [_run_row_to_dict(row) for row in rows]


def _run_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "scope": row["scope"],
        "project_id": row["project_id"],
        "horizon_hours": float(row["horizon_hours"]),
        "status": row["status"],
        "inputs": json.loads(row["inputs_json"]),
        "report": json.loads(row["report_json"]),
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }


def _event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "simulation_run_id": row["simulation_run_id"],
        "event_type": row["event_type"],
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "data": json.loads(row["data_json"]),
        "created_at": row["created_at"],
    }