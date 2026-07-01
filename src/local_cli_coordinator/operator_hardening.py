"""Persistence helpers for Phase 14 daily operator hardening tables."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

VALID_DIAGNOSTIC_SCOPES = frozenset({"global", "project"})
VALID_DIAGNOSTIC_MODES = frozenset({"check", "repair_dry_run", "repair_apply"})
VALID_DIAGNOSTIC_STATUSES = frozenset({
    "pass",
    "warn",
    "fail",
    "repaired",
    "error",
})
VALID_REPAIR_AUDIT_STATUSES = frozenset({"planned", "applied", "skipped", "failed"})
VALID_GLOBAL_CONTROL_ACTIONS = frozenset({"pause", "resume", "drain", "restart"})
VALID_GLOBAL_CONTROL_SCOPES = frozenset({"global", "project"})
VALID_GLOBAL_CONTROL_STATUSES = frozenset({"completed", "partial", "failed"})
VALID_AGENT_HEALTH_STATUSES = frozenset({
    "healthy",
    "degraded",
    "unavailable",
    "disabled",
})
VALID_HANDOFF_SCOPES = frozenset({"global", "project"})


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _validate_enum(value: str, allowed: frozenset[str], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"invalid {field}: {value!r}")
    return value


@dataclass(frozen=True)
class DiagnosticRun:
    id: str
    scope: str
    project_id: str | None
    mode: str
    status: str
    findings: list[dict[str, Any]]
    repairs: list[dict[str, Any]]
    started_at: str
    finished_at: str | None


@dataclass(frozen=True)
class GlobalControlEvent:
    id: str
    action: str
    scope: str
    project_id: str | None
    reason: str
    status: str
    affected_projects: list[str]
    created_at: str


def record_diagnostic_run(
    conn: sqlite3.Connection,
    *,
    scope: str,
    mode: str,
    status: str,
    findings: list[Mapping[str, Any]] | None = None,
    repairs: list[Mapping[str, Any]] | None = None,
    project_id: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    commit: bool = False,
) -> str:
    _validate_enum(scope, VALID_DIAGNOSTIC_SCOPES, "diagnostic scope")
    _validate_enum(mode, VALID_DIAGNOSTIC_MODES, "diagnostic mode")
    _validate_enum(status, VALID_DIAGNOSTIC_STATUSES, "diagnostic status")
    run_id = _new_id("diag")
    started = started_at or _iso_now()
    conn.execute(
        """
        insert into diagnostic_runs(
            id, scope, project_id, mode, status,
            findings_json, repairs_json, started_at, finished_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            scope,
            project_id,
            mode,
            status,
            json.dumps(list(findings or [])),
            json.dumps(list(repairs or [])),
            started,
            finished_at,
        ),
    )
    if commit:
        conn.commit()
    return run_id


def finish_diagnostic_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    repairs: list[Mapping[str, Any]] | None = None,
    commit: bool = False,
) -> None:
    _validate_enum(status, VALID_DIAGNOSTIC_STATUSES, "diagnostic status")
    conn.execute(
        """
        update diagnostic_runs
        set status = ?, repairs_json = ?, finished_at = ?
        where id = ?
        """,
        (
            status,
            json.dumps(list(repairs or [])),
            _iso_now(),
            run_id,
        ),
    )
    if commit:
        conn.commit()


def get_diagnostic_run(conn: sqlite3.Connection, run_id: str) -> DiagnosticRun | None:
    row = conn.execute(
        "select * from diagnostic_runs where id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return DiagnosticRun(
        id=str(row["id"]),
        scope=str(row["scope"]),
        project_id=str(row["project_id"]) if row["project_id"] else None,
        mode=str(row["mode"]),
        status=str(row["status"]),
        findings=json.loads(row["findings_json"]),
        repairs=json.loads(row["repairs_json"]),
        started_at=str(row["started_at"]),
        finished_at=str(row["finished_at"]) if row["finished_at"] else None,
    )


def record_repair_audit_event(
    conn: sqlite3.Connection,
    *,
    diagnostic_run_id: str,
    repair_key: str,
    mode: str,
    status: str,
    project_id: str | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    error: str = "",
    commit: bool = False,
) -> str:
    _validate_enum(mode, VALID_DIAGNOSTIC_MODES, "repair mode")
    _validate_enum(status, VALID_REPAIR_AUDIT_STATUSES, "repair audit status")
    event_id = _new_id("repaud")
    conn.execute(
        """
        insert into repair_audit_events(
            id, diagnostic_run_id, project_id, repair_key, mode, status,
            before_json, after_json, error, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            diagnostic_run_id,
            project_id,
            repair_key,
            mode,
            status,
            json.dumps(dict(before or {})),
            json.dumps(dict(after or {})),
            error,
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return event_id


def record_global_control_event(
    conn: sqlite3.Connection,
    *,
    action: str,
    scope: str,
    status: str,
    affected_projects: list[str] | None = None,
    project_id: str | None = None,
    reason: str = "",
    commit: bool = False,
) -> str:
    _validate_enum(action, VALID_GLOBAL_CONTROL_ACTIONS, "global control action")
    _validate_enum(scope, VALID_GLOBAL_CONTROL_SCOPES, "global control scope")
    _validate_enum(status, VALID_GLOBAL_CONTROL_STATUSES, "global control status")
    event_id = _new_id("gctl")
    conn.execute(
        """
        insert into global_control_events(
            id, action, scope, project_id, reason, status, affected_json, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            action,
            scope,
            project_id,
            reason,
            status,
            json.dumps(list(affected_projects or [])),
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return event_id


def get_latest_global_control_event(
    conn: sqlite3.Connection,
    *,
    action: str | None = None,
) -> GlobalControlEvent | None:
    if action is not None:
        _validate_enum(action, VALID_GLOBAL_CONTROL_ACTIONS, "global control action")
        row = conn.execute(
            """
            select * from global_control_events
            where action = ?
            order by created_at desc
            limit 1
            """,
            (action,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            select * from global_control_events
            order by created_at desc
            limit 1
            """
        ).fetchone()
    if row is None:
        return None
    return GlobalControlEvent(
        id=str(row["id"]),
        action=str(row["action"]),
        scope=str(row["scope"]),
        project_id=str(row["project_id"]) if row["project_id"] else None,
        reason=str(row["reason"]),
        status=str(row["status"]),
        affected_projects=json.loads(row["affected_json"]),
        created_at=str(row["created_at"]),
    )


def record_agent_health_snapshot(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    status: str,
    window_started_at: str,
    window_finished_at: str,
    metrics: Mapping[str, Any] | None = None,
    role: str = "",
    project_id: str | None = None,
    recommendation: str = "",
    commit: bool = False,
) -> str:
    _validate_enum(status, VALID_AGENT_HEALTH_STATUSES, "agent health status")
    snapshot_id = _new_id("ahealth")
    conn.execute(
        """
        insert into agent_health_snapshots(
            id, agent_id, role, project_id, status,
            window_started_at, window_finished_at, metrics_json,
            recommendation, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            agent_id,
            role,
            project_id,
            status,
            window_started_at,
            window_finished_at,
            json.dumps(dict(metrics or {})),
            recommendation,
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return snapshot_id


def record_morning_handoff(
    conn: sqlite3.Connection,
    *,
    scope: str,
    from_time: str,
    to_time: str,
    summary: Mapping[str, Any],
    project_id: str | None = None,
    commit: bool = False,
) -> str:
    _validate_enum(scope, VALID_HANDOFF_SCOPES, "morning handoff scope")
    handoff_id = _new_id("mhand")
    conn.execute(
        """
        insert into morning_handoffs(
            id, scope, project_id, from_time, to_time, summary_json, created_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            handoff_id,
            scope,
            project_id,
            from_time,
            to_time,
            json.dumps(dict(summary)),
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return handoff_id


def get_latest_morning_handoff(
    conn: sqlite3.Connection,
    *,
    scope: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    if scope is not None:
        _validate_enum(scope, VALID_HANDOFF_SCOPES, "morning handoff scope")
    query = "select * from morning_handoffs"
    clauses: list[str] = []
    params: list[Any] = []
    if scope is not None:
        clauses.append("scope = ?")
        params.append(scope)
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    if clauses:
        query += " where " + " and ".join(clauses)
    query += " order by created_at desc limit 1"
    row = conn.execute(query, params).fetchone()
    if row is None:
        return None
    return {
        "handoff_id": str(row["id"]),
        "scope": str(row["scope"]),
        "project_id": str(row["project_id"]) if row["project_id"] else None,
        "from_time": str(row["from_time"]),
        "to_time": str(row["to_time"]),
        "summary": json.loads(row["summary_json"]),
        "created_at": str(row["created_at"]),
    }