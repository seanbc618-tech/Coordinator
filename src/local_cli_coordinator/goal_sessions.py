"""Project-scoped goal resume, fork, and candidate listing."""

from __future__ import annotations

import json
import sqlite3

from .goals import (
    clear_commander_failures,
    create_goal,
    get_goal,
    linked_task_counts,
    list_commander_messages,
    list_linked_tasks,
    transition_goal,
)
from .models import NONTERMINAL_GOAL_STATES

TERMINAL_GOAL_STATES = frozenset({"completed", "failed", "abandoned"})
RESUMABLE_GOAL_STATES = NONTERMINAL_GOAL_STATES

GOAL_SESSION_ERROR_CODES = frozenset(
    {
        "goal_not_resumable",
        "goal_wrong_project",
        "goal_conflict",
        "goal_not_found",
        "fork_conflict",
    }
)

MAX_FORK_MESSAGES = 5
MAX_FORK_MESSAGE_CHARS = 500
MAX_FORK_TASKS = 20


class GoalSessionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def format_goal_session_error(exc: GoalSessionError) -> str:
    return f"{exc.code}: {exc}"


def parse_goal_session_error_message(error: str | None) -> tuple[str, str]:
    message = error or "supervisor request failed"
    if ":" in message:
        code, _, remainder = message.partition(":")
        code = code.strip()
        if code in GOAL_SESSION_ERROR_CODES:
            return code, remainder.strip() or message
    return "supervisor_error", message


def _linked_task_count(conn: sqlite3.Connection, goal_id: int) -> int:
    return sum(linked_task_counts(conn, goal_id).values())


def _candidate_record(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    keys = row.keys()
    parent_goal_id = row["parent_goal_id"] if "parent_goal_id" in keys else None
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "updated_at": row["updated_at"],
        "progress_summary": row["progress_summary"] or "",
        "linked_task_count": _linked_task_count(conn, row["id"]),
        "parent_goal_id": parent_goal_id,
    }


def list_project_goal_candidates(
    conn: sqlite3.Connection,
    project_id: str,
) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        select *
        from goals
        where project_id = ?
          and status in ('draft', 'active', 'paused', 'blocked')
        order by updated_at desc, id desc
        """,
        (project_id,),
    ).fetchall()
    return [_candidate_record(conn, row) for row in rows]


def _other_non_terminal_goals(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    exclude_goal_id: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        select *
        from goals
        where project_id = ?
          and status in ('draft', 'active', 'paused', 'blocked')
          and id != ?
        order by id desc
        """,
        (project_id, exclude_goal_id),
    ).fetchall()


def _assert_project_goal(goal: sqlite3.Row, project_id: str) -> None:
    if goal["project_id"] != project_id:
        raise GoalSessionError(
            "goal_wrong_project",
            f"goal {goal['id']} does not belong to project {project_id}",
        )


def resume_project_goal(
    conn: sqlite3.Connection,
    project_id: str,
    goal_id: int,
) -> sqlite3.Row:
    try:
        goal = get_goal(conn, goal_id)
    except KeyError as exc:
        raise GoalSessionError("goal_not_found", f"unknown goal: {goal_id}") from exc

    _assert_project_goal(goal, project_id)

    if goal["status"] in TERMINAL_GOAL_STATES:
        raise GoalSessionError(
            "goal_not_resumable",
            f"goal {goal_id} is {goal['status']}; use --fork to continue from a terminal goal",
        )

    if goal["status"] not in RESUMABLE_GOAL_STATES:
        raise GoalSessionError(
            "goal_not_resumable",
            f"goal {goal_id} cannot be resumed from status {goal['status']}",
        )

    others = _other_non_terminal_goals(conn, project_id, exclude_goal_id=goal_id)
    if others:
        raise GoalSessionError(
            "goal_conflict",
            (
                f"project already has non-terminal goal {others[0]['id']}; "
                "resolve or complete it before resuming another goal"
            ),
        )

    if goal["status"] in {"paused", "blocked"}:
        clear_commander_failures(conn, goal_id)
        transition_goal(conn, goal_id, "active")
    elif goal["status"] == "draft":
        pass
    elif goal["status"] == "active":
        pass

    return get_goal(conn, goal_id)


def _bounded_fork_task_summary(conn: sqlite3.Connection, goal_id: int) -> str:
    tasks = list_linked_tasks(conn, goal_id)[:MAX_FORK_TASKS]
    if not tasks:
        return "(none)"
    lines: list[str] = []
    for task in tasks:
        lines.append(f"- [{task['state']}] {task['title']} ({task['id']})")
    return "\n".join(lines)


def _bounded_fork_message_summary(conn: sqlite3.Connection, goal_id: int) -> str:
    messages = list_commander_messages(conn, goal_id, limit=MAX_FORK_MESSAGES)
    if not messages:
        return "(none)"
    lines: list[str] = []
    for message in reversed(messages):
        content = (message["content"] or "")[:MAX_FORK_MESSAGE_CHARS]
        lines.append(f"- {message['role']}: {content}")
    return "\n".join(lines)


def _build_fork_objective(
    conn: sqlite3.Connection,
    source: sqlite3.Row,
    instruction: str,
) -> str:
    progress = (source["progress_summary"] or "").strip() or "(none)"
    sections = [
        source["objective"].strip(),
        "",
        f"Fork instruction: {instruction.strip()}",
        f"Source progress: {progress}",
        "",
        "Recent Commander messages:",
        _bounded_fork_message_summary(conn, source["id"]),
        "",
        "Linked task outcomes:",
        _bounded_fork_task_summary(conn, source["id"]),
    ]
    return "\n".join(sections).strip()


def fork_project_goal(
    conn: sqlite3.Connection,
    project_id: str,
    source_goal_id: int,
    instruction: str,
) -> int:
    if not instruction.strip():
        raise GoalSessionError("fork_conflict", "fork instruction is required")

    try:
        source = get_goal(conn, source_goal_id)
    except KeyError as exc:
        raise GoalSessionError("goal_not_found", f"unknown goal: {source_goal_id}") from exc

    _assert_project_goal(source, project_id)

    if source["status"] not in TERMINAL_GOAL_STATES:
        raise GoalSessionError(
            "fork_conflict",
            f"cannot fork from non-terminal goal {source_goal_id}",
        )

    if _other_non_terminal_goals(conn, project_id, exclude_goal_id=-1):
        raise GoalSessionError(
            "goal_conflict",
            "project already has a non-terminal goal; complete it before forking",
        )

    objective = _build_fork_objective(conn, source, instruction)
    completion_criteria = json.loads(source["completion_criteria"])
    constraints = json.loads(source["constraints"])
    repo_ids = json.loads(source["repo_ids"])

    cursor = conn.execute(
        """
        insert into goals(
            title,
            objective,
            completion_criteria,
            constraints,
            repo_ids,
            status,
            project_id,
            progress_summary,
            parent_goal_id
        )
        values (?, ?, ?, ?, ?, 'draft', ?, ?, ?)
        """,
        (
            source["title"],
            objective,
            json.dumps(completion_criteria),
            json.dumps(constraints),
            json.dumps(repo_ids),
            project_id,
            source["progress_summary"] or "",
            source_goal_id,
        ),
    )
    conn.commit()
    return cursor.lastrowid