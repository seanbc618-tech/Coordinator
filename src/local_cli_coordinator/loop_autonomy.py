"""Bounded autonomous loop iteration engine."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .autonomous_backlog import BacklogDraft, promote_next_backlog_item, propose_backlog_items
from .autonomous_loop_db import (
    active_goal_row,
    backlog_status_counts,
    insert_loop_iteration,
    project_has_running_task,
)
from .commander_backlog import commander_response_to_backlog
from .commander_runner import CommanderRunActiveError, run_commander
from .config import CoordinatorConfig
from .evaluator import (
    DEFAULT_EVALUATOR_ID,
    evaluate_task,
    find_unevaluated_terminal_tasks,
    record_task_evaluation,
)
from .goals import (
    clear_commander_failures,
    get_goal,
    linked_tasks_all_terminal,
    record_commander_failure,
    transition_goal,
    update_goal_progress,
)
from .projects import get_project
from .runtime_paths import RuntimePaths

MIN_READY_BACKLOG = 1


@dataclass(frozen=True)
class LoopDecision:
    project_id: str
    goal_id: int | None
    decision: str
    reason: str
    evaluated_count: int = 0
    admitted_task_ids: tuple[str, ...] = ()
    generated_backlog_ids: tuple[str, ...] = ()
    iteration_id: str | None = None


def _autonomy_settings(config: CoordinatorConfig) -> Any:
    return getattr(config, "autonomy", None)


def _wait_when_running(config: CoordinatorConfig) -> bool:
    autonomy = _autonomy_settings(config)
    if autonomy is None:
        return True
    return bool(getattr(autonomy, "wait_when_running", True))


def _pause_after_failures(config: CoordinatorConfig) -> int:
    autonomy = _autonomy_settings(config)
    if autonomy is None:
        return 3
    return int(getattr(autonomy, "pause_after_consecutive_failures", 3))


def _ready_backlog_count(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
) -> int:
    counts = backlog_status_counts(conn, project_id=project_id, goal_id=goal_id)
    return counts.get("ready", 0)


def _consecutive_failure_evaluations(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    limit: int,
) -> int:
    rows = conn.execute(
        """
        select verdict from task_evaluations
        where project_id = ? and goal_id is ?
        order by created_at desc, id desc
        limit ?
        """,
        (project_id, goal_id, limit),
    ).fetchall()
    streak = 0
    for row in rows:
        if row["verdict"] in ("fail", "blocked"):
            streak += 1
        else:
            break
    return streak


def _persist_iteration(
    conn: sqlite3.Connection,
    decision: LoopDecision,
    *,
    generated_count: int = 0,
    caps: dict[str, Any] | None = None,
) -> str:
    return insert_loop_iteration(
        conn,
        project_id=decision.project_id,
        goal_id=decision.goal_id,
        decision=decision.decision,
        reason=decision.reason,
        evaluated_count=decision.evaluated_count,
        admitted_count=len(decision.admitted_task_ids),
        generated_count=generated_count,
        caps=caps or {},
    )


def _finalize_iteration(
    conn: sqlite3.Connection,
    decision: LoopDecision,
    *,
    generated_count: int = 0,
    caps: dict[str, Any] | None = None,
) -> LoopDecision:
    iteration_id = _persist_iteration(
        conn,
        decision,
        generated_count=generated_count,
        caps=caps,
    )
    return replace(decision, iteration_id=iteration_id)


def run_autonomous_iteration(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
    paths: RuntimePaths,
    max_evaluations: int,
    max_admissions: int,
    paused_projects: set[str] | None = None,
    stopped_projects: set[str] | None = None,
) -> LoopDecision:
    """Run one bounded decision cycle and persist a loop_iterations row."""
    paused = paused_projects or set()
    stopped = stopped_projects or set()
    caps = {
        "max_evaluations": max_evaluations,
        "max_admissions": max_admissions,
    }

    if project_id in stopped:
        decision = LoopDecision(
            project_id=project_id,
            goal_id=None,
            decision="wait",
            reason="project is stopped",
        )
        return _finalize_iteration(conn, decision, caps=caps)

    if project_id in paused:
        decision = LoopDecision(
            project_id=project_id,
            goal_id=None,
            decision="wait",
            reason="project is paused",
        )
        return _finalize_iteration(conn, decision, caps=caps)

    goal = active_goal_row(conn, project_id=project_id)
    if goal is None:
        decision = LoopDecision(
            project_id=project_id,
            goal_id=None,
            decision="wait",
            reason="no active goal",
        )
        return _finalize_iteration(conn, decision, caps=caps)

    goal_id = int(goal["id"])
    if str(goal["status"]) == "paused":
        decision = LoopDecision(
            project_id=project_id,
            goal_id=goal_id,
            decision="wait",
            reason="goal is paused",
        )
        return _finalize_iteration(conn, decision, caps=caps)

    if _wait_when_running(config) and project_has_running_task(
        conn, project_id=project_id
    ):
        decision = LoopDecision(
            project_id=project_id,
            goal_id=goal_id,
            decision="wait",
            reason="project has a running task",
        )
        return _finalize_iteration(conn, decision, caps=caps)

    evaluated_count = 0
    human_review_required = False
    if max_evaluations > 0:
        task_ids = find_unevaluated_terminal_tasks(
            conn,
            project_id=project_id,
            limit=max_evaluations,
        )
        for task_id in task_ids:
            evaluation = evaluate_task(conn, task_id=task_id, evaluator_id=DEFAULT_EVALUATOR_ID)
            from .evaluator import TaskEvaluation

            evaluation = TaskEvaluation(
                task_id=evaluation.task_id,
                project_id=evaluation.project_id,
                goal_id=goal_id,
                verdict=evaluation.verdict,
                summary=evaluation.summary,
                evidence=evaluation.evidence,
                next_action=evaluation.next_action,
            )
            record_task_evaluation(conn, evaluation)
            evaluated_count += 1
            if evaluation.next_action == "human_review":
                human_review_required = True

    if human_review_required:
        transition_goal(conn, goal_id, "paused")
        decision = LoopDecision(
            project_id=project_id,
            goal_id=goal_id,
            decision="pause",
            reason="evaluation requires human review",
            evaluated_count=evaluated_count,
        )
        return _finalize_iteration(conn, decision, caps=caps)

    failure_streak = _consecutive_failure_evaluations(
        conn,
        project_id=project_id,
        goal_id=goal_id,
        limit=_pause_after_failures(config),
    )
    if failure_streak >= _pause_after_failures(config):
        transition_goal(conn, goal_id, "paused")
        decision = LoopDecision(
            project_id=project_id,
            goal_id=goal_id,
            decision="pause",
            reason="repeated failures exceeded threshold",
            evaluated_count=evaluated_count,
        )
        return _finalize_iteration(conn, decision, caps=caps)

    admitted_task_ids: list[str] = []
    if max_admissions > 0 and _ready_backlog_count(
        conn, project_id=project_id, goal_id=goal_id
    ) > 0:
        project = get_project(conn, project_id)
        repo_path = paths.data_dir / project_id
        if project is not None:
            repo_path = Path(project["canonical_path"])
        admitted_task_ids = promote_next_backlog_item(
            conn,
            project_id=project_id,
            goal_id=goal_id,
            repo_path=repo_path,
            max_items=max_admissions,
        )

    if admitted_task_ids:
        decision = LoopDecision(
            project_id=project_id,
            goal_id=goal_id,
            decision="admit",
            reason=f"admitted {len(admitted_task_ids)} backlog item(s)",
            evaluated_count=evaluated_count,
            admitted_task_ids=tuple(admitted_task_ids),
        )
        return _finalize_iteration(conn, decision, caps=caps)

    if evaluated_count > 0:
        decision = LoopDecision(
            project_id=project_id,
            goal_id=goal_id,
            decision="evaluate",
            reason=f"evaluated {evaluated_count} terminal task(s)",
            evaluated_count=evaluated_count,
        )
        return _finalize_iteration(conn, decision, caps=caps)

    idle_reason: str | None = None
    ready_count = _ready_backlog_count(conn, project_id=project_id, goal_id=goal_id)
    if ready_count < MIN_READY_BACKLOG:
        project = get_project(conn, project_id)
        root = (
            Path(project["canonical_path"])
            if project is not None
            else paths.data_dir / project_id
        )
        generated_ids, idle_reason = _maybe_generate_backlog(
            conn,
            project_id=project_id,
            goal_id=goal_id,
            config=config,
            root=root,
        )
        if generated_ids:
            decision = LoopDecision(
                project_id=project_id,
                goal_id=goal_id,
                decision="generate",
                reason=f"generated {len(generated_ids)} backlog draft(s)",
                generated_backlog_ids=tuple(generated_ids),
            )
            return _finalize_iteration(
                conn,
                decision,
                generated_count=len(generated_ids),
                caps=caps,
            )

    decision = LoopDecision(
        project_id=project_id,
        goal_id=goal_id,
        decision="wait",
        reason=idle_reason or "no backlog ready and no terminal work to evaluate",
    )
    return _finalize_iteration(conn, decision, caps=caps)


def _repo_autonomy_enabled(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
) -> bool:
    project = get_project(conn, project_id)
    if project is None:
        return False
    canonical = Path(project["canonical_path"]).resolve()
    for repo in config.repos.values():
        if repo.path.resolve() == canonical and repo.autonomy_enabled:
            return True
    return False


def _maybe_generate_backlog(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int,
    config: CoordinatorConfig,
    root: Path,
) -> tuple[list[str], str | None]:
    """Ask Commander for tiny backlog drafts when enabled and budget allows."""
    autonomy = _autonomy_settings(config)
    if autonomy is None or not autonomy.enabled:
        return [], None
    if not _repo_autonomy_enabled(conn, project_id=project_id, config=config):
        return [], None
    max_generated = int(autonomy.max_generated_backlog_per_iteration)
    if max_generated <= 0:
        return [], None
    goal = get_goal(conn, goal_id)
    if goal["commander_retry_after"]:
        from datetime import datetime, timezone

        try:
            retry_after = datetime.fromisoformat(goal["commander_retry_after"])
            if datetime.now(timezone.utc) < retry_after:
                return [], None
        except (ValueError, TypeError):
            pass
    timeout = int(getattr(autonomy, "commander_generation_timeout_seconds", 45))
    try:
        result = run_commander(
            conn,
            config,
            root,
            goal_id,
            "replenishment",
            timeout,
        )
    except CommanderRunActiveError:
        return [], None
    except ValueError:
        return [], None
    if not result.succeeded or result.response is None:
        record_commander_failure(conn, goal_id)
        return [], None
    generation = commander_response_to_backlog(
        conn,
        project_id=project_id,
        goal_id=goal_id,
        response=result.response,
        max_items=max_generated,
    )
    update_goal_progress(conn, goal_id, generation.progress_summary)
    if generation.inserted_ids:
        clear_commander_failures(conn, goal_id)
    if (
        generation.goal_status == "completed"
        and linked_tasks_all_terminal(conn, goal_id)
    ):
        transition_goal(
            conn,
            goal_id,
            "completed",
            stop_reason=generation.stop_reason or "completed by Commander",
        )
    if not generation.inserted_ids and generation.rejected_reasons:
        return [], "no backlog ready and Commander generated no tasks"
    return list(generation.inserted_ids), None