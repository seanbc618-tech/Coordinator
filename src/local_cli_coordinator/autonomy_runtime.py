"""Supervisor-facing autonomy runtime helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .autonomous_loop_db import list_backlog_items
from .config import CoordinatorConfig, RepoConfig
from .loop_autonomy import LoopDecision, run_autonomous_iteration
from .projects import get_project
from .runtime_paths import RuntimePaths
from .supervisor_events import EventBroker


def _match_repo_for_project(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    project_id: str,
) -> RepoConfig | None:
    project = get_project(conn, project_id)
    if project is None:
        return None
    canonical = Path(project["canonical_path"]).resolve()
    for repo in config.repos.values():
        if repo.path.resolve() == canonical:
            return repo
    return None


def project_autonomy_enabled(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
) -> bool:
    if not config.autonomy.enabled:
        return False
    repo = _match_repo_for_project(conn, config, project_id)
    return repo is not None and repo.autonomy_enabled


def run_project_autonomy(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
    paths: RuntimePaths,
    broker: EventBroker,
    paused_projects: set[str] | None = None,
    stopped_projects: set[str] | None = None,
) -> list[LoopDecision]:
    """Run bounded autonomous iterations for one project and publish events."""
    decisions: list[LoopDecision] = []
    iterations = max(1, config.autonomy.max_iterations_per_tick)
    for _ in range(iterations):
        decision = run_autonomous_iteration(
            conn,
            project_id=project_id,
            config=config,
            paths=paths,
            max_evaluations=config.autonomy.max_evaluations_per_iteration,
            max_admissions=config.autonomy.max_admissions_per_iteration,
            paused_projects=paused_projects,
            stopped_projects=stopped_projects,
        )
        decisions.append(decision)
        broker.publish(
            conn,
            project_id,
            "loop.iteration",
            {
                "decision": decision.decision,
                "reason": decision.reason,
                "goal_id": decision.goal_id,
                "evaluated_count": decision.evaluated_count,
                "admitted_task_ids": list(decision.admitted_task_ids),
                "generated_backlog_ids": list(decision.generated_backlog_ids),
            },
        )
        for task_id in decision.admitted_task_ids:
            broker.publish(
                conn,
                project_id,
                "backlog.item",
                {
                    "action": "admitted",
                    "task_id": task_id,
                    "goal_id": decision.goal_id,
                },
            )
        for backlog_id in decision.generated_backlog_ids:
            broker.publish(
                conn,
                project_id,
                "backlog.item",
                {
                    "action": "generated",
                    "backlog_id": backlog_id,
                    "goal_id": decision.goal_id,
                },
            )
        if decision.evaluated_count > 0:
            rows = conn.execute(
                """
                select * from task_evaluations
                where project_id = ?
                order by created_at desc, id desc
                limit ?
                """,
                (project_id, decision.evaluated_count),
            ).fetchall()
            for row in rows:
                broker.publish(
                    conn,
                    project_id,
                    "task.evaluated",
                    {
                        "task_id": row["task_id"],
                        "evaluator_id": row["evaluator_id"],
                        "verdict": row["verdict"],
                        "next_action": row["next_action"],
                        "summary": row["summary"],
                    },
                )
        if decision.decision in {"wait", "pause", "blocked", "complete"}:
            break
    return decisions


def build_loop_status_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
) -> dict[str, Any]:
    from .autonomous_loop_db import (
        active_goal_row,
        backlog_status_counts,
        count_unevaluated_terminal_tasks,
        latest_loop_iteration,
    )

    goal = active_goal_row(conn, project_id=project_id)
    goal_id = int(goal["id"]) if goal is not None else None
    last_iteration = latest_loop_iteration(
        conn, project_id=project_id, goal_id=goal_id
    )
    backlog_counts = backlog_status_counts(
        conn, project_id=project_id, goal_id=goal_id
    )
    unevaluated = count_unevaluated_terminal_tasks(conn, project_id=project_id)
    enabled = project_autonomy_enabled(conn, project_id=project_id, config=config)
    next_action = "wait"
    if last_iteration is not None:
        decision = str(last_iteration["decision"])
        if decision == "admit":
            next_action = "monitor admitted task"
        elif decision == "evaluate":
            next_action = "review evaluations"
        elif decision == "pause":
            next_action = "operator review required"
        elif decision == "generate":
            next_action = "review generated backlog"
        else:
            next_action = decision
    return {
        "project_id": project_id,
        "autonomy_enabled": enabled,
        "goal": (
            {
                "id": goal_id,
                "title": goal["title"],
                "status": goal["status"],
            }
            if goal is not None
            else None
        ),
        "last_iteration": (
            {
                "decision": last_iteration["decision"],
                "reason": last_iteration["reason"],
                "started_at": last_iteration["started_at"],
                "generated_count": last_iteration["generated_count"],
            }
            if last_iteration is not None
            else None
        ),
        "generation": {
            "enabled": config.autonomy.enabled,
            "max_per_iteration": config.autonomy.max_generated_backlog_per_iteration,
            "timeout_seconds": config.autonomy.commander_generation_timeout_seconds,
        },
        "backlog_counts": backlog_counts,
        "unevaluated_terminal_count": unevaluated,
        "caps": {
            "max_evaluations_per_iteration": config.autonomy.max_evaluations_per_iteration,
            "max_admissions_per_iteration": config.autonomy.max_admissions_per_iteration,
            "max_generated_backlog_per_iteration": (
                config.autonomy.max_generated_backlog_per_iteration
            ),
        },
        "next_expected_action": next_action,
    }


def build_backlog_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    rows = list_backlog_items(
        conn, project_id=project_id, goal_id=goal_id, limit=limit
    )
    items = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                "title": row["title"],
                "status": row["status"],
                "source": row["source"],
                "priority": row["priority"],
                "linked_task_id": row["linked_task_id"],
                "dedupe_key": row["dedupe_key"],
                "created_at": row["created_at"],
            }
        )
    return {"project_id": project_id, "items": items}


def build_evaluations_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    from .autonomous_loop_db import list_task_evaluations

    rows = list_task_evaluations(
        conn, project_id=project_id, goal_id=goal_id, limit=limit
    )
    evaluations = []
    for row in rows:
        evaluations.append(
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "evaluator_id": row["evaluator_id"],
                "verdict": row["verdict"],
                "summary": row["summary"],
                "next_action": row["next_action"],
                "evidence": json.loads(row["evidence_json"]),
                "created_at": row["created_at"],
            }
        )
    return {"project_id": project_id, "evaluations": evaluations}