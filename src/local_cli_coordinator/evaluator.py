"""Rule-based independent task evaluation for the autonomous loop."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .autonomous_loop_db import (
    find_unevaluated_terminal_task_ids,
    insert_task_evaluation_row,
)
from .db import artifact_kinds, get_task, list_task_events, task_latest_attempt

DEFAULT_EVALUATOR_ID = "rules-v1"

_HUMAN_REVIEW_SIGNALS = (
    "credential",
    "secret",
    "live trading",
    "funds",
    "market order",
    "merge",
    "push",
    "trading",
)


@dataclass(frozen=True)
class TaskEvaluation:
    task_id: str
    project_id: str
    goal_id: int | None
    verdict: str
    summary: str
    evidence: dict[str, Any]
    next_action: str


def find_unevaluated_terminal_tasks(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    limit: int,
    evaluator_id: str = DEFAULT_EVALUATOR_ID,
) -> list[str]:
    """Return terminal project task ids without a rules-v1 evaluation."""
    return find_unevaluated_terminal_task_ids(
        conn,
        project_id=project_id,
        evaluator_id=evaluator_id,
        limit=limit,
    )


def _task_text_blob(task: sqlite3.Row) -> str:
    parts = [
        str(task["title"]),
        str(task["goal"]),
        str(task["acceptance_criteria"]),
        str(task["verification_commands"]),
        str(task["execution_policy"]),
    ]
    return " ".join(parts).lower()


def _needs_human_review(task: sqlite3.Row) -> bool:
    blob = _task_text_blob(task)
    return any(signal in blob for signal in _HUMAN_REVIEW_SIGNALS)


def _verification_commands(task: sqlite3.Row) -> list[str]:
    return [line for line in str(task["verification_commands"]).splitlines() if line.strip()]


def evaluate_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    evaluator_id: str = DEFAULT_EVALUATOR_ID,
) -> TaskEvaluation:
    """Build a deterministic evaluation from task state, events, and artifacts."""
    task = get_task(conn, task_id)
    project_id = str(task["project_id"])
    goal_id = None
    events = list_task_events(conn, task_id)
    attempt = task_latest_attempt(conn, task_id)
    artifacts = sorted(artifact_kinds(conn, task_id))
    verify_commands = _verification_commands(task)
    evidence: dict[str, Any] = {
        "state": task["state"],
        "verification_commands": verify_commands,
        "artifact_kinds": artifacts,
        "event_count": len(events),
    }
    if attempt is not None:
        evidence["latest_attempt"] = {
            "exit_code": attempt["exit_code"],
            "agent_id": attempt["agent_id"],
        }

    if _needs_human_review(task):
        return TaskEvaluation(
            task_id=task_id,
            project_id=project_id,
            goal_id=goal_id,
            verdict="human_review",
            summary="task requires human review before follow-up",
            evidence=evidence,
            next_action="human_review",
        )

    state = str(task["state"])
    if state == "failed":
        return TaskEvaluation(
            task_id=task_id,
            project_id=project_id,
            goal_id=goal_id,
            verdict="fail",
            summary="task reached failed terminal state",
            evidence=evidence,
            next_action="admit_followup",
        )

    if state == "blocked":
        return TaskEvaluation(
            task_id=task_id,
            project_id=project_id,
            goal_id=goal_id,
            verdict="blocked",
            summary="task is blocked and cannot progress autonomously",
            evidence=evidence,
            next_action="pause_goal",
        )

    if state == "rejected":
        return TaskEvaluation(
            task_id=task_id,
            project_id=project_id,
            goal_id=goal_id,
            verdict="fail",
            summary="task was rejected",
            evidence=evidence,
            next_action="ask_commander",
        )

    if state == "done":
        attempt_exit = attempt["exit_code"] if attempt is not None else None
        if verify_commands and attempt_exit not in (0, None):
            return TaskEvaluation(
                task_id=task_id,
                project_id=project_id,
                goal_id=goal_id,
                verdict="fail",
                summary="task completed but verification did not pass",
                evidence=evidence,
                next_action="admit_followup",
            )
        if not verify_commands and not artifacts:
            return TaskEvaluation(
                task_id=task_id,
                project_id=project_id,
                goal_id=goal_id,
                verdict="needs_followup",
                summary="task completed without verification evidence",
                evidence=evidence,
                next_action="admit_followup",
            )
        return TaskEvaluation(
            task_id=task_id,
            project_id=project_id,
            goal_id=goal_id,
            verdict="pass",
            summary="task completed with acceptable evidence",
            evidence=evidence,
            next_action="none",
        )

    return TaskEvaluation(
        task_id=task_id,
        project_id=project_id,
        goal_id=goal_id,
        verdict="blocked",
        summary=f"task state {state!r} is not a supported terminal outcome",
        evidence=evidence,
        next_action="pause_goal",
    )


def record_task_evaluation(
    conn: sqlite3.Connection,
    evaluation: TaskEvaluation,
    *,
    evaluator_id: str = DEFAULT_EVALUATOR_ID,
) -> str:
    """Persist exactly one evaluation per task/evaluator pair."""
    return insert_task_evaluation_row(
        conn,
        project_id=evaluation.project_id,
        goal_id=evaluation.goal_id,
        task_id=evaluation.task_id,
        evaluator_id=evaluator_id,
        verdict=evaluation.verdict,
        summary=evaluation.summary,
        evidence=evaluation.evidence,
        next_action=evaluation.next_action,
    )