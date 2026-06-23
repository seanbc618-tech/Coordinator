"""Commander service layer.

High-level operations for goal lifecycle: create, preview, confirm,
pause, resume, abandon.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .commander_policy import admit_commander_response, batch_is_high_risk_only
from .commander_runner import (
    CommanderResponse,
    CommanderRunActiveError,
    CommanderTaskProposal,
    classify_commander_failure,
    run_commander,
)
from .config import CoordinatorConfig
from .db import create_task, next_ready_task
from .reporting import NULL_REPORTER, Reporter
from .goals import (
    add_commander_message,
    active_goal,
    active_goal_for_project,
    clear_commander_failures,
    create_goal,
    get_goal,
    link_task_to_goal,
    linked_task_counts,
    linked_tasks_all_terminal,
    record_commander_failure,
    transition_goal,
    update_goal_progress,
)

COMMANDER_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class GoalPlanPreview:
    goal_id: int
    progress_summary: str
    proposals: list[CommanderTaskProposal]
    error: str | None = None


def create_and_preview_goal(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    objective: str,
    *,
    project_id: str = "legacy-default",
    title: str | None = None,
    completion_criteria: list[str] | None = None,
    constraints: list[str] | None = None,
    repo_ids: list[str] | None = None,
    reporter: Reporter = NULL_REPORTER,
) -> GoalPlanPreview:
    """Create a draft goal and run the Commander to preview the first batch.

    The goal remains in 'draft' status until confirmed.
    """
    if repo_ids is None:
        repo_ids = list(config.repos.keys())

    goal_title = title or objective[:100]
    goal_id = create_goal(
        conn,
        goal_title,
        objective,
        project_id=project_id,
        completion_criteria=completion_criteria,
        constraints=constraints,
        repo_ids=repo_ids,
    )

    # Run Commander to get initial plan
    try:
        result = run_commander(
            conn,
            config,
            root,
            goal_id,
            "initial_plan",
            COMMANDER_TIMEOUT_SECONDS,
            reporter=reporter,
        )
    except (CommanderRunActiveError, ValueError) as exc:
        return GoalPlanPreview(
            goal_id=goal_id,
            progress_summary="Commander preview failed",
            proposals=[],
            error=str(exc),
        )

    if not result.succeeded or result.response is None:
        # Still return the goal even if Commander failed
        return GoalPlanPreview(
            goal_id=goal_id,
            progress_summary=result.error or "Commander failed",
            proposals=[],
            error=result.error or "Commander failed",
        )

    return GoalPlanPreview(
        goal_id=goal_id,
        progress_summary=result.response.progress_summary,
        proposals=result.response.tasks,
        error=None,
    )


def confirm_goal(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    *,
    project_id: str = "legacy-default",
    goal_id: int | None = None,
) -> str:
    """Confirm a draft goal and activate it.

    Returns a status message.
    """
    if goal_id is None:
        goal = active_goal_for_project(conn, project_id)
        if goal is None:
            return "no draft goal to confirm"
        goal_id = goal["id"]

    goal = get_goal(conn, goal_id)
    if goal["status"] != "draft":
        return f"goal is {goal['status']}, not draft"

    # Get the latest run to re-validate proposals
    from .goals import get_latest_commander_run
    run = get_latest_commander_run(conn, goal_id)
    if run is None:
        return "no preview available; create a goal first"
    if run["status"] != "succeeded":
        detail = run["error"] or f"status {run['status']}"
        return f"preview failed; goal remains draft: {detail}"

    transition_goal(conn, goal_id, "active")
    return f"goal {goal_id} activated"


def send_chat_message(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    goal_id: int,
    content: str,
    reporter: Reporter = NULL_REPORTER,
) -> str:
    """Send a user message to Commander and return its visible response."""
    add_commander_message(conn, goal_id, "user", content)
    try:
        result = run_commander(
            conn,
            config,
            root,
            goal_id,
            "chat",
            COMMANDER_TIMEOUT_SECONDS,
            reporter=reporter,
        )
    except CommanderRunActiveError:
        return "Commander is already running; try again after the current run finishes."
    except ValueError as exc:
        return f"Commander unavailable: {exc}"

    if not result.succeeded or result.response is None:
        message = f"Commander failed: {result.error or 'unknown error'}"
        add_commander_message(conn, goal_id, "assistant", message)
        return message

    response = result.response
    admission = admit_commander_response(conn, config, root, goal_id, response)
    update_goal_progress(conn, goal_id, response.progress_summary)

    parts = [f"Commander: {response.progress_summary}"]
    if admission.accepted_task_ids:
        parts.append(f"admitted {len(admission.accepted_task_ids)} task(s)")
    if admission.rejection_reasons:
        parts.append("rejected: " + "; ".join(admission.rejection_reasons))
    message = " | ".join(parts)
    add_commander_message(conn, goal_id, "assistant", message)
    return message


def pause_goal(
    conn: sqlite3.Connection,
    *,
    project_id: str = "legacy-default",
    goal_id: int | None = None,
) -> str:
    """Pause an active goal."""
    if goal_id is None:
        goal = active_goal_for_project(conn, project_id)
        if goal is None:
            return "no active goal"
        goal_id = goal["id"]

    goal = get_goal(conn, goal_id)
    if goal["status"] not in ("active", "blocked"):
        return f"goal is {goal['status']}, cannot pause"

    transition_goal(conn, goal_id, "paused")
    return f"goal {goal_id} paused"


def resume_goal(
    conn: sqlite3.Connection,
    *,
    project_id: str = "legacy-default",
    goal_id: int | None = None,
) -> str:
    """Resume a paused goal."""
    if goal_id is None:
        goal = active_goal_for_project(conn, project_id)
        if goal is None:
            return "no paused goal"
        goal_id = goal["id"]

    goal = get_goal(conn, goal_id)
    if goal["status"] != "paused":
        return f"goal is {goal['status']}, not paused"

    prior_reason = goal["stop_reason"] or goal["progress_summary"]
    clear_commander_failures(conn, goal_id)
    transition_goal(conn, goal_id, "active", stop_reason="")
    if prior_reason:
        return f"goal {goal_id} resumed (prior reason: {prior_reason})"
    return f"goal {goal_id} resumed"


def abandon_goal(
    conn: sqlite3.Connection,
    goal_id: int | None = None,
) -> str:
    """Abandon a non-terminal goal."""
    if goal_id is None:
        goal = active_goal(conn)
        if goal is None:
            return "no goal to abandon"
        goal_id = goal["id"]

    goal = get_goal(conn, goal_id)
    if goal["status"] in ("completed", "abandoned"):
        return f"goal is already {goal['status']}"

    transition_goal(conn, goal_id, "abandoned")
    return f"goal {goal_id} abandoned"


def goal_status(conn: sqlite3.Connection, *, project_id: str = "legacy-default") -> str:
    """Return human-readable status for project's non-terminal goal."""
    goal = active_goal_for_project(conn, project_id)
    if goal is None:
        return "no active goal"

    from .goals import linked_task_counts
    counts = linked_task_counts(conn, goal["id"])
    total = sum(counts.values())
    done = counts.get("done", 0)

    lines = [
        f"Goal: {goal['status']}",
        f"  ID: {goal['id']}",
        f"  Title: {goal['title']}",
        f"  Objective: {goal['objective'][:200]}",
    ]

    if goal["progress_summary"]:
        lines.append(f"  Progress: {goal['progress_summary'][:200]}")

    if total > 0:
        lines.append(f"  Tasks: {done}/{total} done")

    if goal["stop_reason"]:
        lines.append(f"  Stop reason: {goal['stop_reason']}")

    if goal["commander_failures"] > 0:
        lines.append(f"  Commander failures: {goal['commander_failures']}")
    if goal["commander_retry_after"]:
        lines.append(f"  Commander retry after: {goal['commander_retry_after']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Daemon replenishment
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReplenishmentResult:
    status: str
    admitted_task_ids: list[str]
    rejected_reasons: list[str]
    commander_run_id: int | None


def maybe_replenish_goal(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    reporter: Reporter = NULL_REPORTER,
) -> ReplenishmentResult:
    """Replenish the task queue if the active goal has no ready tasks.

    Called by the daemon cycle. Returns a ReplenishmentResult indicating
    what happened.
    """
    goal = active_goal(conn)
    if goal is None:
        return ReplenishmentResult("not_eligible", [], [], None)

    if goal["status"] != "active":
        return ReplenishmentResult("not_eligible", [], [], None)

    # Check if there are already ready tasks
    ready_count = linked_task_counts(conn, goal["id"]).get("ready", 0)
    if ready_count > 0:
        return ReplenishmentResult("queue_not_low", [], [], None)

    # Check retry timing
    if goal["commander_retry_after"]:
        from datetime import datetime, timezone
        try:
            retry_after = datetime.fromisoformat(goal["commander_retry_after"])
            if datetime.now(timezone.utc) < retry_after:
                return ReplenishmentResult("retry_pending", [], [], None)
        except (ValueError, TypeError):
            pass

    # Run Commander to get next batch
    try:
        result = run_commander(
            conn,
            config,
            root,
            goal["id"],
            "replenishment",
            COMMANDER_TIMEOUT_SECONDS,
            reporter=reporter,
        )
    except CommanderRunActiveError:
        return ReplenishmentResult("commander_run_active", [], [], None)
    except ValueError as exc:
        return ReplenishmentResult(
            "not_eligible",
            [],
            [str(exc)],
            None,
        )

    if not result.succeeded or result.response is None:
        record_commander_failure(conn, goal["id"])
        refreshed = get_goal(conn, goal["id"])
        failure_kind = classify_commander_failure(result)
        if refreshed["status"] == "paused":
            return ReplenishmentResult(
                "paused_after_failures",
                [],
                [f"commander {failure_kind} failure"],
                result.run_id,
            )
        if failure_kind == "timeout":
            return ReplenishmentResult(
                "retry_scheduled",
                [],
                [result.error or "timeout"],
                result.run_id,
            )
        return ReplenishmentResult(
            "commander_failed",
            [],
            [result.error or "unknown error"],
            result.run_id,
        )

    if batch_is_high_risk_only(conn, config, goal["id"], result.response):
        stop_reason = "blocked: high-risk Commander batch requires human review"
        transition_goal(
            conn,
            goal["id"],
            "blocked",
            stop_reason=stop_reason,
        )
        return ReplenishmentResult(
            "blocked_high_risk",
            [],
            [stop_reason],
            result.run_id,
        )

    admission = admit_commander_response(
        conn,
        config,
        root,
        goal["id"],
        result.response,
    )

    if admission.accepted_task_ids:
        clear_commander_failures(conn, goal["id"])
        update_goal_progress(conn, goal["id"], result.response.progress_summary)

    if (
        result.response.goal_status == "completed"
        and linked_tasks_all_terminal(conn, goal["id"])
    ):
        transition_goal(
            conn,
            goal["id"],
            "completed",
            stop_reason=result.response.stop_reason or "completed by Commander",
        )

    return ReplenishmentResult(
        "admitted" if admission.accepted_task_ids else "all_rejected",
        admission.accepted_task_ids,
        admission.rejection_reasons,
        result.run_id,
    )


def _admit_task_proposal(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    goal_id: int,
    proposal: CommanderTaskProposal,
) -> str:
    """Admit a single task proposal. Returns the task_id.

    Raises ValueError if the proposal is rejected.
    """
    # Validate repo
    if proposal.repo not in config.repos:
        raise ValueError(f"repo not allowlisted: {proposal.repo}")

    # Validate capabilities match a worker
    from .config import select_agent_by_role
    worker = select_agent_by_role(config, "worker", proposal.capabilities)
    if worker is None:
        raise ValueError(f"no worker with capabilities: {proposal.capabilities}")

    # Validate file limits
    if proposal.expected_files > config.policy.max_files_touched:
        raise ValueError(f"expected files {proposal.expected_files} exceeds limit {config.policy.max_files_touched}")

    # Validate time limits
    if proposal.expected_minutes > config.policy.max_expected_minutes:
        raise ValueError(f"expected minutes {proposal.expected_minutes} exceeds limit {config.policy.max_expected_minutes}")

    # Check for duplicate title among active tasks
    existing = conn.execute(
        "select id from tasks where title = ? and state not in ('done', 'failed', 'rejected')",
        (proposal.title,),
    ).fetchone()
    if existing:
        raise ValueError(f"duplicate task title: {proposal.title}")

    # Create the task
    task_id = create_task(
        conn,
        title=proposal.title,
        repo=proposal.repo,
        source_path="",
        priority="normal",
        capabilities=proposal.capabilities,
        goal=proposal.goal,
        acceptance_criteria=proposal.acceptance_criteria,
        verification_commands=proposal.verification_commands,
    )

    # Link to goal
    link_task_to_goal(
        conn, goal_id, task_id,
        rationale=proposal.rationale,
    )

    return task_id
