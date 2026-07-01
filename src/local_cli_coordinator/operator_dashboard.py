"""Daily operator dashboard payloads with project scoping."""

from __future__ import annotations

import sqlite3
from typing import Any

from .approval_channels import list_approval_requests
from .config import CoordinatorConfig
from .failure_explainer import explain_task_failure
from .global_controls import is_global_paused
from .operator_hardening import get_latest_morning_handoff
from .operator_inbox import list_operator_items
from .runtime_paths import RuntimePaths
from .task_control import build_dashboard_payload


def _aggregate_task_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "select state, count(*) as cnt from tasks group by state"
    ).fetchall()
    return {str(row["state"]): int(row["cnt"]) for row in rows}


def _count_running_workers(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        select count(*) as cnt
        from task_leases
        where released_at is null
          and expires_at > datetime('now')
        """
    ).fetchone()
    return int(row["cnt"]) if row is not None else 0


def _count_pr_ci_attention(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            """
            select count(*) as cnt
            from pr_health_records
            where status in ('stale', 'conflict', 'ci_failed', 'needs_attention')
            """
        ).fetchone()
        return int(row["cnt"]) if row is not None else 0
    except sqlite3.OperationalError:
        return 0


def _count_unhealthy_agents(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        select agent_id, max(created_at) as latest
        from agent_health_snapshots
        group by agent_id
        """
    ).fetchall()
    if not rows:
        return 0
    count = 0
    for row in rows:
        snap = conn.execute(
            """
            select status from agent_health_snapshots
            where agent_id = ? order by created_at desc limit 1
            """,
            (row["agent_id"],),
        ).fetchone()
        if snap is not None and str(snap["status"]) in {"degraded", "unavailable"}:
            count += 1
    return count


def _build_next_actions(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    failed_count: int,
    pending_approval_count: int,
    pr_ci_attention_count: int,
) -> list[str]:
    actions: list[str] = []
    if is_global_paused(paths):
        actions.append("Global pause active — run coordinator resume --all when ready")
    if failed_count:
        actions.append(f"Review {failed_count} failed task(s) with /why <task-id>")
    if pending_approval_count:
        actions.append("Pending approvals need attention — see /approvals")
    if pr_ci_attention_count:
        actions.append("PR/CI items need attention — see /stale or /ci failures")
    if not actions:
        actions.append("No urgent operator actions")
    return actions


def build_daily_dashboard(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    config: CoordinatorConfig | None,
) -> dict[str, Any]:
    base = build_dashboard_payload(conn)
    tasks_by_state = _aggregate_task_counts(conn)
    project_rows = conn.execute("select id, status from projects").fetchall()
    project_count = len(project_rows)
    active_project_count = sum(
        1 for row in project_rows if str(row["status"] or "active") == "active"
    )
    failed_count = tasks_by_state.get("failed", 0)
    blocked_count = tasks_by_state.get("blocked", 0)
    awaiting_approval_count = tasks_by_state.get("awaiting_human", 0)
    pending_approval_count = conn.execute(
        "select count(*) as cnt from approval_requests where status = 'pending'"
    ).fetchone()["cnt"]
    pr_ci_attention_count = _count_pr_ci_attention(conn)
    unhealthy_agent_count = _count_unhealthy_agents(conn)
    latest_handoff = get_latest_morning_handoff(conn, scope="global")
    morning_handoff_at = latest_handoff["created_at"] if latest_handoff else None

    redacted_projects = []
    for entry in base.get("projects") or []:
        redacted = dict(entry)
        redacted.pop("failure_summaries", None)
        redacted_projects.append(redacted)

    return {
        **base,
        "projects": redacted_projects,
        "global_pause": is_global_paused(paths),
        "project_count": project_count,
        "active_project_count": active_project_count,
        "tasks_by_state": tasks_by_state,
        "running_workers": _count_running_workers(conn),
        "failed_count": failed_count,
        "blocked_count": blocked_count,
        "awaiting_approval_count": awaiting_approval_count,
        "pending_approval_count": int(pending_approval_count),
        "pr_ci_attention_count": pr_ci_attention_count,
        "unhealthy_agent_count": unhealthy_agent_count,
        "morning_handoff_at": morning_handoff_at,
        "next_actions": _build_next_actions(
            conn,
            paths=paths,
            failed_count=failed_count,
            pending_approval_count=int(pending_approval_count),
            pr_ci_attention_count=pr_ci_attention_count,
        ),
    }


def build_project_dashboard(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    paths: RuntimePaths,
    config: CoordinatorConfig | None,
) -> dict[str, Any]:
    base = build_daily_dashboard(conn, paths=paths, config=config)
    base["scope"] = "project"
    base["project_id"] = project_id
    failed_rows = conn.execute(
        """
        select id from tasks
        where project_id = ? and state in ('failed', 'blocked')
        order by updated_at desc
        limit 10
        """,
        (project_id,),
    ).fetchall()
    failure_summaries = []
    for row in failed_rows:
        task_id = str(row["id"])
        try:
            summary = explain_task_failure(conn, task_id=task_id)
            failure_summaries.append(
                {
                    "task_id": task_id,
                    "classified_reason": summary.get("classified_reason"),
                    "next_action": summary.get("next_action"),
                }
            )
        except ValueError:
            continue
    base["failure_summaries"] = failure_summaries
    base["pending_approvals"] = len(
        list_approval_requests(conn, project_id=project_id, status="pending")
    )
    base["operator_items"] = len(list_operator_items(conn, project_id=project_id))
    return base