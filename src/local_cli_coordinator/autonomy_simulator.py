"""Dry-run autonomy simulation without mutating operational tables."""

from __future__ import annotations

import sqlite3
from typing import Any

from .agent_router import preview_route
from .approval_callbacks import forecast_method_approval
from .autonomous_runs import project_has_runnable_run_session
from .autonomy_runtime import project_autonomy_enabled
from .config import CoordinatorConfig
from .db import (
    active_lease_count,
    circuit_breaker_reason,
    peek_project_claim,
    project_has_claimable_task,
)
from .policy_forecast import (
    classify_task_risk_forecast,
    forecast_approval_requirements,
    forecast_budget_pressure,
    forecast_policy_blocks,
)
from .projects import list_projects
from .runtime_paths import RuntimePaths
from .simulation_reports import (
    create_simulation_run,
    finish_simulation_run,
    record_simulation_event,
)
from .supervisor_capacity import SharedCapacity
from .supervisor_scheduler import (
    FairProjectScheduler,
    ProjectSkipForecast,
    ScheduleDecision,
    forecast_project_skip_reason,
    simulate_scheduler_round,
)


def _operational_snapshot(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "tasks": conn.execute("select count(*) as c from tasks").fetchone()["c"],
        "leases": conn.execute(
            "select count(*) as c from task_leases where released_at is null"
        ).fetchone()["c"],
        "worktrees": conn.execute(
            "select count(*) as c from tasks where worktree_path is not null and worktree_path != ''"
        ).fetchone()["c"],
    }


def _project_ids_for_scope(
    conn: sqlite3.Connection,
    *,
    scope: str,
    project_id: str | None,
) -> list[str]:
    if scope == "project":
        if not project_id:
            raise ValueError("project_id is required for project scope")
        return [project_id]
    rows = list_projects(conn)
    project_ids = [str(row["id"]) for row in rows]
    task_rows = conn.execute(
        "select distinct project_id from tasks order by project_id"
    ).fetchall()
    for row in task_rows:
        pid = str(row["project_id"])
        if pid not in project_ids:
            project_ids.append(pid)
    return project_ids


def run_autonomy_simulation(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    paths: RuntimePaths | None = None,
    scope: str = "global",
    project_id: str | None = None,
    horizon_hours: float = 8.0,
    paused_projects: set[str] | None = None,
    stopped_projects: set[str] | None = None,
    max_global_running: int = 4,
    max_per_project: int = 2,
    commit: bool = True,
) -> dict[str, Any]:
    """Simulate scheduler/policy outcomes and persist a forecast report only."""
    del paths  # reserved for future config/state reads
    before = _operational_snapshot(conn)
    run_id = create_simulation_run(
        conn,
        scope=scope,
        project_id=project_id,
        horizon_hours=horizon_hours,
        inputs={
            "paused_projects": sorted(paused_projects or []),
            "stopped_projects": sorted(stopped_projects or []),
        },
        commit=False,
    )

    project_ids = _project_ids_for_scope(conn, scope=scope, project_id=project_id)
    from .agent_capabilities import load_capability_profiles

    load_capability_profiles(conn, config, sync=True)
    capacity = SharedCapacity.from_running_tasks(
        conn,
        max_global_running=max_global_running,
        max_per_project=max_per_project,
        max_daily_tasks=config.policy.max_tasks_per_day,
    )
    scheduler = FairProjectScheduler(project_ids)
    cb_reason = circuit_breaker_reason(conn, config.policy)
    budget = forecast_budget_pressure(conn, config)

    scheduled: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    admissions: list[dict[str, Any]] = []
    agent_usage: list[dict[str, Any]] = []
    policy_blocks: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    warnings: list[str] = []

    def _is_runnable(pid: str) -> bool:
        skip = forecast_project_skip_reason(
            project_id=pid,
            paused_projects=paused_projects,
            stopped_projects=stopped_projects,
            capacity_available=capacity.can_accept_project(pid)
            and capacity.snapshot()["active_global"] < max_global_running,
            has_claimable_task=project_has_claimable_task(conn, pid, config),
            has_runnable_autonomy=project_autonomy_enabled(
                conn, project_id=pid, config=config
            )
            and project_has_runnable_run_session(conn, project_id=pid),
            circuit_breaker_reason=cb_reason,
        )
        return skip is None

    rounds = max(1, len(project_ids))
    outcomes = simulate_scheduler_round(
        scheduler,
        _is_runnable,
        rounds=rounds,
    )

    for outcome in outcomes:
        if isinstance(outcome, ScheduleDecision):
            scheduled.append({
                "project_id": outcome.project_id,
                "reason": outcome.reason,
                "forecast": True,
            })
            record_simulation_event(
                conn,
                simulation_run_id=run_id,
                event_type="would_schedule",
                project_id=outcome.project_id,
                data={"reason": outcome.reason, "forecast": True},
                commit=False,
            )
        elif isinstance(outcome, ProjectSkipForecast):
            skipped.append({
                "project_id": outcome.project_id,
                "reason": outcome.reason,
                "forecast": True,
            })
            record_simulation_event(
                conn,
                simulation_run_id=run_id,
                event_type="would_skip",
                project_id=outcome.project_id,
                data={"reason": outcome.reason, "forecast": True},
                commit=False,
            )

    for pid in project_ids:
        if pid in {item["project_id"] for item in scheduled}:
            continue
        reason = forecast_project_skip_reason(
            project_id=pid,
            paused_projects=paused_projects,
            stopped_projects=stopped_projects,
            capacity_available=capacity.can_accept_project(pid),
            has_claimable_task=project_has_claimable_task(conn, pid, config),
            has_runnable_autonomy=project_autonomy_enabled(
                conn, project_id=pid, config=config
            )
            and project_has_runnable_run_session(conn, project_id=pid),
            circuit_breaker_reason=cb_reason,
        )
        if reason is None:
            continue
        if any(item["project_id"] == pid for item in skipped):
            continue
        skipped.append({"project_id": pid, "reason": reason, "forecast": True})
        record_simulation_event(
            conn,
            simulation_run_id=run_id,
            event_type="would_skip",
            project_id=pid,
            data={"reason": reason, "forecast": True},
            commit=False,
        )

    for pid in project_ids:
        if scope == "project" and pid != project_id:
            continue
        ready_rows = conn.execute(
            """
            select id, capabilities, repo from tasks
            where project_id = ? and state = 'ready'
            order by created_at, id
            limit ?
            """,
            (pid, config.autonomy.max_admissions_per_iteration),
        ).fetchall()
        for row in ready_rows:
            task_id = str(row["id"])
            admissions.append({
                "project_id": pid,
                "task_id": task_id,
                "forecast": True,
                "reason": "ready task may be admitted",
            })
            task_row, agent_id = peek_project_claim(conn, pid, config)
            if task_row is not None and agent_id is not None:
                route = preview_route(
                    conn,
                    config,
                    project_id=pid,
                    task_id=str(task_row["id"]),
                )
                selected = route.selected_agent_id if route else agent_id
                usage = {
                    "project_id": pid,
                    "task_id": str(task_row["id"]),
                    "agent_id": selected or agent_id,
                    "reason": route.reason if route else "eligible agent available",
                    "forecast": True,
                }
                agent_usage.append(usage)
                record_simulation_event(
                    conn,
                    simulation_run_id=run_id,
                    event_type="would_use_agent",
                    project_id=pid,
                    task_id=str(task_row["id"]),
                    data=usage,
                    commit=False,
                )
            cancel_forecast = forecast_method_approval(
                "project.task.cancel",
                project_id=pid,
                task_id=task_id,
            )
            if cancel_forecast:
                approvals.append(cancel_forecast)
                record_simulation_event(
                    conn,
                    simulation_run_id=run_id,
                    event_type="would_require_approval",
                    project_id=pid,
                    task_id=task_id,
                    data=cancel_forecast,
                    commit=False,
                )
            risk = classify_task_risk_forecast(
                capabilities=[part for part in str(row["capabilities"]).split(",") if part],
                max_files_touched=config.policy.max_files_touched,
            )
            if risk["requires_human_review"]:
                policy_blocks.append({
                    "project_id": pid,
                    "task_id": task_id,
                    "operation": "human_review",
                    "reason": "; ".join(risk["reasons"]) or "elevated risk forecast",
                    "forecast": True,
                })
                record_simulation_event(
                    conn,
                    simulation_run_id=run_id,
                    event_type="would_block",
                    project_id=pid,
                    task_id=task_id,
                    data=policy_blocks[-1],
                    commit=False,
                )

        policy_blocks.extend(
            forecast_policy_blocks(conn, config, project_id=pid)
        )
        approvals.extend(
            forecast_approval_requirements(conn, project_id=pid)
        )

    cap_forecast = capacity.forecast_pressure(additional_tasks=len(admissions))
    if budget["pressure"] in {"high", "exhausted"}:
        record_simulation_event(
            conn,
            simulation_run_id=run_id,
            event_type="would_hit_budget",
            data={**budget, "capacity": cap_forecast},
            commit=False,
        )
        warnings.append(
            f"daily budget pressure: {budget['used']}/{budget['limit']} tasks used"
        )
    if cap_forecast["pressure"] != "none":
        warnings.append(
            "capacity pressure: " + "; ".join(cap_forecast["reasons"])
        )
    if cb_reason:
        warnings.append(f"circuit breaker active: {cb_reason}")
    if active_lease_count(conn) >= max_global_running:
        warnings.append("global lease count at concurrency ceiling")

    report = {
        "forecast": True,
        "scope": scope,
        "project_id": project_id,
        "horizon_hours": horizon_hours,
        "scheduled_projects": scheduled,
        "skipped_projects": skipped,
        "expected_admissions": admissions,
        "expected_agent_usage": agent_usage,
        "budget_pressure": budget,
        "capacity_pressure": cap_forecast,
        "approvals_likely_required": approvals,
        "policy_blocked_operations": policy_blocks,
        "safety_warnings": warnings[:10],
        "label": "FORECAST — no tasks, leases, or agents were started",
    }

    status = "completed"
    finish_simulation_run(
        conn,
        simulation_run_id=run_id,
        status=status,
        report=report,
        commit=False,
    )

    after = _operational_snapshot(conn)
    if after != before:
        finish_simulation_run(
            conn,
            simulation_run_id=run_id,
            status="failed",
            report={
                **report,
                "mutation_detected": {"before": before, "after": after},
            },
            commit=False,
        )
        if commit:
            conn.commit()
        raise RuntimeError(
            "simulation mutated operational tables: "
            f"before={before} after={after}"
        )

    if commit:
        conn.commit()

    return {
        "simulation_run_id": run_id,
        "status": status,
        "report": report,
        "forecast": True,
    }