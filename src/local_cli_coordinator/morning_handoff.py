"""Morning handoff summaries from durable Coordinator state."""

from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta, timezone
from typing import Any

from .approval_channels import list_approval_requests
from .config import CoordinatorConfig
from .doctor_repair import run_readiness_findings
from .failure_explainer import explain_task_failure
from .global_controls import is_global_paused
from .operator_hardening import get_latest_morning_handoff, record_morning_handoff
from .operator_inbox import list_operator_items
from .runtime_paths import RuntimePaths


def _default_window() -> tuple[str, str]:
    now = datetime.now().astimezone()
    start = datetime.combine(now.date(), time(6, 0), tzinfo=now.tzinfo)
    if now < start:
        start = start - timedelta(days=1)
    return start.isoformat(), now.isoformat()


def _sqlite_since(value: str) -> str:
    if "T" in value:
        body = value.split("T", 1)[1]
        body = body.split("+")[0].split("Z")[0]
        return f"{value.split('T')[0]} {body}"
    return value


def _list_tasks_in_states(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    states: tuple[str, ...],
    since: str,
) -> list[sqlite3.Row]:
    query = """
        select id, title, state, project_id, updated_at
        from tasks
        where state in ({states})
          and updated_at >= ?
    """.format(states=",".join("?" for _ in states))
    params: list[Any] = list(states) + [_sqlite_since(since)]
    if project_id is not None:
        query += " and project_id = ?"
        params.append(project_id)
    query += " order by updated_at desc limit 50"
    return conn.execute(query, params).fetchall()


def build_morning_handoff(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    config: CoordinatorConfig | None,
    scope: str | None = None,
    project_id: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    resolved_scope = scope or ("project" if project_id else "global")
    if from_time is None or to_time is None:
        latest = get_latest_morning_handoff(
            conn,
            scope=resolved_scope,
            project_id=project_id,
        )
        if latest is not None:
            from_time = latest["to_time"]
            to_time = datetime.now(timezone.utc).isoformat()
        else:
            from_time, to_time = _default_window()

    completed_rows = _list_tasks_in_states(
        conn,
        project_id=project_id,
        states=("done",),
        since=from_time,
    )
    failed_rows = _list_tasks_in_states(
        conn,
        project_id=project_id,
        states=("failed", "blocked"),
        since=from_time,
    )
    completed_tasks = [
        {
            "task_id": str(row["id"]),
            "title": str(row["title"]),
            "project_id": str(row["project_id"]),
        }
        for row in completed_rows
    ]
    failed_tasks = []
    for row in failed_rows:
        task_id = str(row["id"])
        summary = explain_task_failure(conn, task_id=task_id)
        failed_tasks.append(
            {
                "task_id": task_id,
                "title": str(row["title"]),
                "project_id": str(row["project_id"]),
                "classified_reason": summary.get("classified_reason"),
                "why_ref": f"/why {task_id}",
            }
        )

    pending_approvals = []
    if project_id is not None:
        for request in list_approval_requests(conn, project_id=project_id, status="pending"):
            pending_approvals.append(
                {
                    "request_id": request.id,
                    "action_method": request.action_method,
                }
            )
        inbox_items = list_operator_items(conn, project_id=project_id)
        pending_approvals.extend(
            {
                "operator_item_id": item.id,
                "title": item.title,
                "severity": item.severity,
            }
            for item in inbox_items
            if item.severity in {"warning", "error", "critical"}
        )
    else:
        for row in conn.execute(
            "select distinct project_id from approval_requests where status = 'pending'"
        ).fetchall():
            pid = str(row["project_id"])
            pending_approvals.append(
                {
                    "project_id": pid,
                    "pending_count": len(
                        list_approval_requests(conn, project_id=pid, status="pending")
                    ),
                }
            )

    pr_ci_count = 0
    try:
        pr_ci_changes = conn.execute(
            """
            select count(*) as cnt
            from github_delivery_records
            where updated_at >= ?
            """
            + (" and project_id = ?" if project_id else ""),
            (from_time, project_id) if project_id else (from_time,),
        ).fetchone()
        pr_ci_count = int(pr_ci_changes["cnt"]) if pr_ci_changes is not None else 0
    except sqlite3.OperationalError:
        pr_ci_count = 0

    paused_or_blocked_projects = []
    query = """
        select id, status, pause_reason
        from projects
        where status in ('paused', 'blocked')
    """
    params: list[Any] = []
    if project_id is not None:
        query += " and id = ?"
        params.append(project_id)
    for row in conn.execute(query, params).fetchall():
        paused_or_blocked_projects.append(
            {
                "project_id": str(row["id"]),
                "status": str(row["status"]),
                "pause_reason": str(row["pause_reason"] or ""),
            }
        )
    if is_global_paused(paths):
        paused_or_blocked_projects.append({"scope": "global", "status": "paused"})

    findings = run_readiness_findings(paths, conn, config)
    repair_recommendations = [
        {
            "repair_key": item.get("finding_key"),
            "path": item.get("path"),
        }
        for item in findings
    ]

    from .agent_health import compute_agent_health
    from .config_runtime import load_config_for_paths

    resolved_config = config or load_config_for_paths(paths)
    agent_health_changes = compute_agent_health(
        conn,
        config=resolved_config,
        project_id=project_id,
    )

    next_actions: list[str] = []
    if failed_tasks:
        next_actions.append(f"Review {len(failed_tasks)} failed task(s)")
    if pending_approvals:
        next_actions.append("Clear pending approvals")
    if repair_recommendations:
        next_actions.append("Run coordinator doctor --repair --dry-run")
    if is_global_paused(paths):
        next_actions.append("Global pause is active; run coordinator resume --all when ready")

    payload = {
        "scope": resolved_scope,
        "project_id": project_id,
        "from_time": from_time,
        "to_time": to_time,
        "completed_tasks": completed_tasks,
        "failed_tasks": failed_tasks,
        "pending_approvals": pending_approvals,
        "pr_ci_changes": {"count": pr_ci_count},
        "paused_or_blocked_projects": paused_or_blocked_projects,
        "repair_recommendations": repair_recommendations,
        "agent_health_changes": agent_health_changes,
        "next_actions": next_actions,
    }
    if persist:
        handoff_id = record_morning_handoff(
            conn,
            scope=resolved_scope,
            project_id=project_id,
            from_time=from_time,
            to_time=to_time,
            summary=payload,
            commit=True,
        )
        payload["handoff_id"] = handoff_id
    return payload