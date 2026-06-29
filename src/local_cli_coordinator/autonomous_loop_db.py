"""SQLite helpers for Phase 6 autonomous loop tables."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

OPEN_BACKLOG_DEDUPE_INDEX = "idx_project_backlog_dedupe_open"
OPEN_BACKLOG_STATUSES = ("candidate", "ready", "admitted")


def is_open_backlog_dedupe_error(exc: sqlite3.IntegrityError) -> bool:
    """Return True when an integrity error came from the open backlog dedupe index."""
    message = str(exc).lower()
    if OPEN_BACKLOG_DEDUPE_INDEX in message:
        return True
    return (
        "project_backlog_items.project_id" in message
        and "project_backlog_items.goal_id" in message
        and "project_backlog_items.dedupe_key" in message
    )
TERMINAL_TASK_STATES = ("done", "failed", "blocked", "rejected")
RUNNING_TASK_STATES = (
    "running",
    "verifying",
    "committing",
    "pushing",
    "merging",
    "reviewing_spec",
    "reviewing_quality",
    "retrying",
)


def insert_backlog_item(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    source: str,
    title: str,
    rationale: str,
    acceptance_criteria: list[str],
    verification_commands: list[str],
    execution_policy: str,
    priority: int,
    status: str,
    dedupe_key: str,
    milestone_id: int | None = None,
    commit: bool = True,
) -> str:
    item_id = f"backlog-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        insert into project_backlog_items(
            id, project_id, goal_id, source, title, rationale,
            acceptance_criteria_json, verification_commands_json,
            execution_policy, priority, status, dedupe_key, milestone_id
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            project_id,
            goal_id,
            source,
            title,
            rationale,
            json.dumps(acceptance_criteria),
            json.dumps(verification_commands),
            execution_policy,
            priority,
            status,
            dedupe_key,
            milestone_id,
        ),
    )
    if commit:
        conn.commit()
    return item_id


def open_backlog_exists(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    dedupe_key: str,
) -> bool:
    row = conn.execute(
        """
        select 1 from project_backlog_items
        where project_id = ?
          and goal_id is ?
          and dedupe_key = ?
          and status in ('candidate', 'ready', 'admitted')
        limit 1
        """,
        (project_id, goal_id, dedupe_key),
    ).fetchone()
    return row is not None


def list_ready_backlog_items(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    limit: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        select * from project_backlog_items
        where project_id = ?
          and goal_id is ?
          and status = 'ready'
        order by priority desc, created_at asc, id asc
        limit ?
        """,
        (project_id, goal_id, limit),
    ).fetchall()


def mark_backlog_admitted(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    linked_task_id: str,
    commit: bool = True,
) -> None:
    conn.execute(
        """
        update project_backlog_items
        set status = 'admitted',
            linked_task_id = ?,
            admitted_at = current_timestamp,
            updated_at = current_timestamp
        where id = ?
        """,
        (linked_task_id, item_id),
    )
    if commit:
        conn.commit()


def backlog_status_counts(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None = None,
) -> dict[str, int]:
    if goal_id is None:
        rows = conn.execute(
            """
            select status, count(*) as cnt
            from project_backlog_items
            where project_id = ?
            group by status
            """,
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select status, count(*) as cnt
            from project_backlog_items
            where project_id = ? and goal_id is ?
            group by status
            """,
            (project_id, goal_id),
        ).fetchall()
    return {row["status"]: row["cnt"] for row in rows}


def list_backlog_items(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    if goal_id is None:
        return conn.execute(
            """
            select * from project_backlog_items
            where project_id = ?
            order by created_at desc, id desc
            limit ?
            """,
            (project_id, limit),
        ).fetchall()
    return conn.execute(
        """
        select * from project_backlog_items
        where project_id = ? and goal_id is ?
        order by created_at desc, id desc
        limit ?
        """,
        (project_id, goal_id, limit),
    ).fetchall()


def get_task_evaluation_id(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    evaluator_id: str,
) -> str | None:
    row = conn.execute(
        """
        select id from task_evaluations
        where task_id = ? and evaluator_id = ?
        """,
        (task_id, evaluator_id),
    ).fetchone()
    return row["id"] if row is not None else None


def insert_task_evaluation_row(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    task_id: str,
    evaluator_id: str,
    verdict: str,
    summary: str,
    evidence: dict[str, Any],
    next_action: str,
    commit: bool = True,
) -> str:
    existing = get_task_evaluation_id(
        conn, task_id=task_id, evaluator_id=evaluator_id
    )
    if existing is not None:
        return existing
    evaluation_id = f"eval-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        insert into task_evaluations(
            id, project_id, goal_id, task_id, evaluator_id,
            verdict, summary, evidence_json, next_action
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evaluation_id,
            project_id,
            goal_id,
            task_id,
            evaluator_id,
            verdict,
            summary,
            json.dumps(evidence),
            next_action,
        ),
    )
    if commit:
        conn.commit()
    return evaluation_id


def list_task_evaluations(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    if goal_id is None:
        return conn.execute(
            """
            select * from task_evaluations
            where project_id = ?
            order by created_at desc, id desc
            limit ?
            """,
            (project_id, limit),
        ).fetchall()
    return conn.execute(
        """
        select * from task_evaluations
        where project_id = ? and goal_id is ?
        order by created_at desc, id desc
        limit ?
        """,
        (project_id, goal_id, limit),
    ).fetchall()


def find_unevaluated_terminal_task_ids(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    evaluator_id: str,
    limit: int,
) -> list[str]:
    placeholders = ",".join("?" for _ in TERMINAL_TASK_STATES)
    rows = conn.execute(
        f"""
        select t.id
        from tasks t
        where t.project_id = ?
          and t.state in ({placeholders})
          and not exists (
              select 1 from task_evaluations te
              where te.task_id = t.id and te.evaluator_id = ?
          )
        order by t.created_at asc, t.id asc
        limit ?
        """,
        (project_id, *TERMINAL_TASK_STATES, evaluator_id, limit),
    ).fetchall()
    return [row["id"] for row in rows]


def count_unevaluated_terminal_tasks(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    evaluator_id: str = "rules-v1",
) -> int:
    placeholders = ",".join("?" for _ in TERMINAL_TASK_STATES)
    row = conn.execute(
        f"""
        select count(*) as cnt
        from tasks t
        where t.project_id = ?
          and t.state in ({placeholders})
          and not exists (
              select 1 from task_evaluations te
              where te.task_id = t.id and te.evaluator_id = ?
          )
        """,
        (project_id, *TERMINAL_TASK_STATES, evaluator_id),
    ).fetchone()
    return int(row["cnt"])


def project_has_running_task(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> bool:
    placeholders = ",".join("?" for _ in RUNNING_TASK_STATES)
    row = conn.execute(
        f"""
        select 1 from tasks
        where project_id = ? and state in ({placeholders})
        limit 1
        """,
        (project_id, *RUNNING_TASK_STATES),
    ).fetchone()
    return row is not None


def insert_loop_iteration(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    decision: str,
    reason: str,
    evaluated_count: int = 0,
    admitted_count: int = 0,
    generated_count: int = 0,
    caps: dict[str, Any] | None = None,
    milestone_id: int | None = None,
    commit: bool = True,
) -> str:
    iteration_id = f"loop-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        insert into loop_iterations(
            id, project_id, goal_id, decision, reason,
            evaluated_count, admitted_count, generated_count,
            caps_json, milestone_id, ended_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
        """,
        (
            iteration_id,
            project_id,
            goal_id,
            decision,
            reason,
            evaluated_count,
            admitted_count,
            generated_count,
            json.dumps(caps or {}),
            milestone_id,
        ),
    )
    if commit:
        conn.commit()
    return iteration_id


def latest_loop_iteration(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None = None,
) -> sqlite3.Row | None:
    if goal_id is None:
        return conn.execute(
            """
            select * from loop_iterations
            where project_id = ?
            order by started_at desc, id desc
            limit 1
            """,
            (project_id,),
        ).fetchone()
    return conn.execute(
        """
        select * from loop_iterations
        where project_id = ? and goal_id is ?
        order by started_at desc, id desc
        limit 1
        """,
        (project_id, goal_id),
    ).fetchone()


def active_goal_row(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        select * from goals
        where project_id = ? and status = 'active'
        order by id desc
        limit 1
        """,
        (project_id,),
    ).fetchone()