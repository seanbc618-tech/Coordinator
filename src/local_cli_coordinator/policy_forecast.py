"""Read-only policy, risk, and approval forecasts for autonomy simulation."""

from __future__ import annotations

import sqlite3
from typing import Any

from .approval_callbacks import POLICY_GATED_METHODS, requires_external_approval
from .config import CoordinatorConfig
from .db import circuit_breaker_reason
from .operator_inbox import DESTRUCTIVE_METHODS
from .policy import detect_risk_signals
from .risk import _generated_file_signals, _large_diff_signal, _risk_level, _secret_signals


def classify_task_risk_forecast(
    *,
    changed_files: list[str] | None = None,
    diff_text: str = "",
    capabilities: list[str] | None = None,
    max_files_touched: int = 20,
) -> dict[str, Any]:
    """Classify task risk without persisting an assessment row."""
    files = list(changed_files or [])
    reasons: list[str] = []
    reasons.extend(
        detect_risk_signals(
            files,
            max_files_touched=max_files_touched,
            spec_review_passed=True,
            quality_review_passed=True,
        )
    )
    reasons.extend(_secret_signals(diff_text))
    reasons.extend(_generated_file_signals(files))
    reasons.extend(_large_diff_signal(diff_text))
    caps = [part for part in (capabilities or []) if part]
    if "code" in caps and not files:
        reasons.append("code task produced no changed files")
    deduped = list(dict.fromkeys(reasons))
    level = _risk_level(deduped)
    return {
        "risk_level": level,
        "reasons": deduped,
        "requires_human_review": bool(deduped) or level != "low",
        "forecast": True,
    }


def forecast_policy_blocks(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    """Predict operations that policy would block during an autonomous run."""
    blocks: list[dict[str, Any]] = []
    cb_reason = circuit_breaker_reason(conn, config.policy)
    if cb_reason:
        blocks.append({
            "project_id": project_id,
            "operation": "schedule_tick",
            "reason": cb_reason,
            "forecast": True,
        })

    running = conn.execute(
        """
        select id, title from tasks
        where project_id = ? and state = 'running'
        order by created_at, id
        """,
        (project_id,),
    ).fetchall()
    for row in running:
        blocks.append({
            "project_id": project_id,
            "task_id": row["id"],
            "operation": "parallel_admission",
            "reason": "running task blocks additional admissions when wait_when_running is enabled",
            "forecast": True,
        })

    return blocks


def forecast_approval_requirements(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Predict approvals likely required from durable inbox and policy gates."""
    forecasts: list[dict[str, Any]] = []

    query = """
        select id, project_id, title, action_method, action_params_json, severity
        from operator_items
        where status = 'open'
    """
    params: list[Any] = []
    if project_id is not None:
        query += " and project_id = ?"
        params.append(project_id)
    query += " order by created_at desc, id desc"
    rows = conn.execute(query, params).fetchall()

    for row in rows:
        action_method = row["action_method"]
        if action_method and requires_external_approval(str(action_method)):
            forecasts.append({
                "project_id": row["project_id"],
                "operator_item_id": row["id"],
                "action_method": action_method,
                "title": row["title"],
                "severity": row["severity"],
                "reason": "open operator inbox item requires external approval",
                "forecast": True,
            })

    pending = conn.execute(
        """
        select id, project_id, action_method, status
        from approval_requests
        where status = 'pending'
        """
        + (" and project_id = ?" if project_id else "")
        + " order by created_at desc",
        (project_id,) if project_id else (),
    ).fetchall()
    for row in pending:
        forecasts.append({
            "project_id": row["project_id"],
            "approval_request_id": row["id"],
            "action_method": row["action_method"],
            "reason": "pending external approval request",
            "forecast": True,
        })

    for method in sorted(DESTRUCTIVE_METHODS | POLICY_GATED_METHODS):
        forecasts.append({
            "project_id": project_id,
            "action_method": method,
            "reason": "policy-gated method requires approval before execution",
            "forecast": True,
            "latent": True,
        })

    return forecasts


def forecast_budget_pressure(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
) -> dict[str, Any]:
    """Forecast daily task budget pressure from durable daemon run totals."""
    row = conn.execute(
        """
        select coalesce(sum(tasks_processed), 0) as total
        from daemon_runs
        where date(started_at) = date('now')
        """
    ).fetchone()
    used = int(row["total"])
    limit = int(config.policy.max_tasks_per_day)
    remaining = max(0, limit - used)
    pressure = "none"
    if used >= limit:
        pressure = "exhausted"
    elif used >= max(1, int(limit * 0.8)):
        pressure = "high"
    elif used >= max(1, int(limit * 0.5)):
        pressure = "moderate"
    return {
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "pressure": pressure,
        "forecast": True,
    }