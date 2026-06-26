"""Project-scoped task detail enrichment and control mutations."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .config import CoordinatorConfig
from .db import (
    get_task,
    project_get_task_detail,
    project_task_counts,
    release_task_lease,
    task_latest_attempt,
    task_latest_event,
    task_list_artifacts_for_project,
    transition_task,
)
from .goals import active_goal_for_project
from .projects import get_project
from .supervisor_events import EventBroker
from .worker_registry import GLOBAL_WORKER_REGISTRY


class TaskControlError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def build_orchestration(
    *,
    admitted: int,
    rejected: int,
    rejection_reasons: list[str] | None = None,
    blocking_reasons: list[str] | None = None,
    intent: str = "conversation",
) -> dict[str, Any]:
    reasons = list(rejection_reasons or [])
    blocking = list(blocking_reasons or [])
    if admitted:
        next_action = (
            f"daemon will schedule {admitted} admitted task(s)"
            if admitted != 1
            else "daemon will schedule the admitted task"
        )
    elif rejected:
        next_action = "review rejection reasons before retrying"
    elif intent == "conversation":
        next_action = "waiting for task instructions"
    else:
        next_action = "monitor project status with /status"
    return {
        "admitted": admitted,
        "rejected": rejected,
        "next_action": next_action,
        "blocking_reasons": blocking,
        "rejection_reasons": reasons,
    }


def _parse_execution_policy(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _context_manifest_summary(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(entries, list):
        return []
    summary: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or entry.get("relative_path") or "")
        digest = str(entry.get("sha256") or entry.get("hash") or "")
        if path:
            item = {"path": path}
            if digest:
                item["sha256"] = digest
            summary.append(item)
    return summary


def _failure_class(
    state: str,
    latest_event: sqlite3.Row | None,
    latest_attempt: sqlite3.Row | None,
) -> str:
    if state == "awaiting_human":
        return "human"
    note = (latest_event["note"] if latest_event else "") or ""
    note_lower = note.lower()
    if "policy" in note_lower or "execution policy" in note_lower:
        return "policy"
    if state in {"failed", "blocked", "rejected"}:
        if latest_attempt is not None:
            result_class = str(latest_attempt["result_class"] or "")
            if "timeout" in result_class.lower() or "timeout" in note_lower:
                return "timeout"
            if "verify" in result_class.lower() or "verif" in note_lower:
                return "verify"
        if "human" in note_lower or "review" in note_lower:
            return "human"
        return "unknown"
    return "unknown"


def build_task_detail_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    row = project_get_task_detail(conn, project_id=project_id, task_id=task_id)
    if row is None:
        raise TaskControlError(
            "task_not_found",
            f"task {task_id!r} not found in project {project_id!r}",
        )

    latest = task_latest_event(conn, task_id)
    attempt = task_latest_attempt(conn, task_id)
    artifacts = task_list_artifacts_for_project(
        conn, project_id=project_id, task_id=task_id
    )
    verification_commands = [
        line for line in row["verification_commands"].splitlines() if line
    ]
    state = str(row["state"])
    failure_summary = ""
    if latest and latest["note"]:
        failure_summary = str(latest["note"])[:200]

    payload: dict[str, Any] = {
        "task": {
            "id": row["id"],
            "title": row["title"],
            "state": state,
            "repo": row["repo"],
            "priority": row["priority"],
            "capabilities": row["capabilities"],
            "goal": row["goal"],
            "acceptance_criteria": row["acceptance_criteria"],
            "verification_commands": verification_commands,
            "branch": row["branch"],
            "worktree_path": row["worktree_path"],
            "source_path": row["source_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        },
        "execution_policy": _parse_execution_policy(row["execution_policy"]),
        "context_manifest": [],
        "latest_note": latest["note"] if latest else None,
        "latest_transition": (
            {
                "from": latest["old_state"],
                "to": latest["new_state"],
                "at": latest["created_at"],
                "note": latest["note"],
            }
            if latest
            else None
        ),
        "failure_summary": failure_summary,
        "failure_class": _failure_class(state, latest, attempt),
        "human_review_required": state == "awaiting_human",
        "latest_event": (
            {
                "old_state": latest["old_state"],
                "new_state": latest["new_state"],
                "note": latest["note"],
                "created_at": latest["created_at"],
            }
            if latest
            else None
        ),
        "latest_attempt": (
            {
                "agent_id": attempt["agent_id"],
                "exit_code": attempt["exit_code"],
                "result_class": attempt["result_class"],
                "result_reason": attempt["result_reason"],
                "log_path": attempt["log_path"],
                "completed_at": attempt["ended_at"],
            }
            if attempt
            else None
        ),
        "artifacts": [{"kind": art["kind"], "path": art["path"]} for art in artifacts],
    }
    return payload


def flatten_task_detail_json(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape used by headless ``/task <id>`` JSON output."""
    flat = {
        "execution_policy": payload.get("execution_policy", {}),
        "context_manifest": payload.get("context_manifest", []),
        "latest_note": payload.get("latest_note"),
        "failure_class": payload.get("failure_class", "unknown"),
        "human_review_required": payload.get("human_review_required", False),
        "failure_summary": payload.get("failure_summary", ""),
        "latest_transition": payload.get("latest_transition"),
    }
    task = payload.get("task")
    if isinstance(task, dict):
        flat.update(task)
    flat["latest_event"] = payload.get("latest_event")
    flat["latest_attempt"] = payload.get("latest_attempt")
    flat["artifacts"] = payload.get("artifacts", [])
    return flat


def _publish_task_updated(
    broker: EventBroker | None,
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    new_state: str,
    note: str,
) -> None:
    if broker is None:
        return
    broker.publish(
        conn,
        project_id,
        "task.updated",
        {"task_id": task_id, "state": new_state, "note": note},
    )


def approve_task(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    broker: EventBroker | None = None,
    config: CoordinatorConfig | None = None,
) -> dict[str, Any]:
    row = project_get_task_detail(conn, project_id=project_id, task_id=task_id)
    if row is None:
        raise TaskControlError(
            "task_not_found",
            f"task {task_id!r} not found in project {project_id!r}",
        )
    if row["state"] != "awaiting_human":
        raise TaskControlError(
            "invalid_state",
            f"task {task_id!r} is {row['state']!r}; approve requires awaiting_human",
        )
    transition_task(conn, task_id, "ready", "approved by operator")
    _publish_task_updated(
        broker,
        conn,
        project_id=project_id,
        task_id=task_id,
        new_state="ready",
        note="approved by operator",
    )
    return {"task_id": task_id, "state": "ready"}


def retry_task(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    config: CoordinatorConfig | None = None,
    broker: EventBroker | None = None,
) -> dict[str, Any]:
    row = project_get_task_detail(conn, project_id=project_id, task_id=task_id)
    if row is None:
        raise TaskControlError(
            "task_not_found",
            f"task {task_id!r} not found in project {project_id!r}",
        )
    if row["state"] not in {"failed", "blocked", "rejected"}:
        raise TaskControlError(
            "invalid_state",
            f"task {task_id!r} is {row['state']!r}; retry requires failed/blocked/rejected",
        )
    max_attempts = config.policy.max_attempts if config is not None else 3
    attempt_count = conn.execute(
        "select count(*) as cnt from attempts where task_id = ?",
        (task_id,),
    ).fetchone()["cnt"]
    if attempt_count >= max_attempts:
        raise TaskControlError(
            "retry_exhausted",
            f"task {task_id!r} reached max attempts ({max_attempts})",
        )
    transition_task(conn, task_id, "ready", "retried by operator")
    _publish_task_updated(
        broker,
        conn,
        project_id=project_id,
        task_id=task_id,
        new_state="ready",
        note="retried by operator",
    )
    return {"task_id": task_id, "state": "ready", "attempt": attempt_count + 1}


def cancel_task(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    broker: EventBroker | None = None,
    config: CoordinatorConfig | None = None,
) -> dict[str, Any]:
    row = project_get_task_detail(conn, project_id=project_id, task_id=task_id)
    if row is None:
        raise TaskControlError(
            "task_not_found",
            f"task {task_id!r} not found in project {project_id!r}",
        )
    worker_terminated = GLOBAL_WORKER_REGISTRY.terminate(task_id)
    release_task_lease(conn, task_id)
    if row["state"] not in {"done", "failed", "blocked", "rejected"}:
        transition_task(conn, task_id, "failed", "cancelled by operator")
        new_state = "failed"
    else:
        new_state = row["state"]
    _publish_task_updated(
        broker,
        conn,
        project_id=project_id,
        task_id=task_id,
        new_state=new_state,
        note="cancelled by operator",
    )
    return {
        "task_id": task_id,
        "state": new_state,
        "lease_released": True,
        "worker_terminated": worker_terminated,
    }


def build_dashboard_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        select id, updated_at
        from projects
        order by updated_at desc
        limit 32
        """
    ).fetchall()
    projects: list[dict[str, Any]] = []
    for row in rows:
        project_id = row["id"]
        goal = active_goal_for_project(conn, project_id)
        counts = project_task_counts(conn, project_id=project_id)
        active_workers = conn.execute(
            """
            select count(*) as cnt
            from task_leases
            where project_id = ?
              and released_at is null
              and expires_at > datetime('now')
            """,
            (project_id,),
        ).fetchone()["cnt"]
        projects.append(
            {
                "project_id": project_id,
                "goal_status": goal["status"] if goal is not None else "none",
                "task_counts": counts,
                "active_workers": active_workers,
                "last_tick_at": row["updated_at"],
            }
        )
    return {"projects": projects}


def format_task_control_error(exc: TaskControlError) -> str:
    return f"{exc.code}: {exc.message}"