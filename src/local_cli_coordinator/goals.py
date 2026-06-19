"""Commander goal persistence layer.

Manages goals, commander runs, messages, and task-goal links in SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import GOAL_STATES, NONTERMINAL_GOAL_STATES


# ---------------------------------------------------------------------------
# Goal CRUD
# ---------------------------------------------------------------------------

def create_goal(
    conn: sqlite3.Connection,
    title: str,
    objective: str,
    completion_criteria: list[str] | None = None,
    constraints: list[str] | None = None,
    repo_ids: list[str] | None = None,
) -> int:
    """Create a new draft goal. Raises IntegrityError if a non-terminal goal exists."""
    cursor = conn.execute(
        """
        insert into goals(title, objective, completion_criteria, constraints, repo_ids, status)
        values (?, ?, ?, ?, ?, 'draft')
        """,
        (
            title,
            objective,
            json.dumps(completion_criteria or []),
            json.dumps(constraints or []),
            json.dumps(repo_ids or []),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_goal(conn: sqlite3.Connection, goal_id: int) -> sqlite3.Row:
    """Return a goal row by id. Raises KeyError if not found."""
    row = conn.execute("select * from goals where id = ?", (goal_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown goal: {goal_id}")
    return row


def active_goal(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Return the single non-terminal goal, or None."""
    return conn.execute(
        "select * from goals where status in ('draft', 'active', 'paused', 'blocked') "
        "order by id limit 1"
    ).fetchone()


def transition_goal(
    conn: sqlite3.Connection,
    goal_id: int,
    new_status: str,
    *,
    stop_reason: str = "",
    progress_summary: str = "",
) -> None:
    """Transition a goal to a new status. Raises ValueError for invalid states."""
    if new_status not in GOAL_STATES:
        raise ValueError(f"invalid goal status: {new_status!r}")
    current = get_goal(conn, goal_id)
    updates = ["status = ?", "updated_at = current_timestamp"]
    params: list = [new_status]
    if stop_reason:
        updates.append("stop_reason = ?")
        params.append(stop_reason)
    if progress_summary:
        updates.append("progress_summary = ?")
        params.append(progress_summary)
    if new_status == "completed":
        updates.append("completed_at = current_timestamp")
    elif new_status == "paused":
        updates.append("paused_at = current_timestamp")
    elif new_status == "active" and current["status"] == "draft":
        updates.append("confirmed_at = current_timestamp")
    params.append(goal_id)
    conn.execute(
        f"update goals set {', '.join(updates)} where id = ?",
        params,
    )
    conn.commit()


def update_goal_progress(
    conn: sqlite3.Connection,
    goal_id: int,
    progress_summary: str,
) -> None:
    """Update the progress summary of a goal."""
    conn.execute(
        "update goals set progress_summary = ?, updated_at = current_timestamp where id = ?",
        (progress_summary, goal_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Commander messages
# ---------------------------------------------------------------------------

def add_commander_message(
    conn: sqlite3.Connection,
    goal_id: int,
    role: str,
    content: str,
) -> int:
    """Add a message to the commander conversation."""
    cursor = conn.execute(
        "insert into commander_messages(goal_id, role, content) values (?, ?, ?)",
        (goal_id, role, content),
    )
    conn.commit()
    return cursor.lastrowid


def list_commander_messages(
    conn: sqlite3.Connection,
    goal_id: int,
    limit: int = 20,
) -> list[sqlite3.Row]:
    """Return the most recent messages for a goal."""
    return conn.execute(
        "select * from commander_messages where goal_id = ? "
        "order by id desc limit ?",
        (goal_id, limit),
    ).fetchall()


# ---------------------------------------------------------------------------
# Commander runs
# ---------------------------------------------------------------------------

def start_commander_run(
    conn: sqlite3.Connection,
    goal_id: int,
    trigger: str,
    schema_version: int,
    prompt_path: Path,
) -> int:
    """Record the start of a commander run. Returns the run id."""
    run_id = acquire_commander_run_slot(
        conn,
        goal_id,
        trigger,
        schema_version,
        prompt_path,
    )
    if run_id is None:
        raise RuntimeError("commander run already active")
    return run_id


def acquire_commander_run_slot(
    conn: sqlite3.Connection,
    goal_id: int,
    trigger: str,
    schema_version: int,
    prompt_path: Path,
) -> int | None:
    """Atomically claim a Commander run slot, or return None if one is active."""
    interrupt_stale_commander_runs(conn, goal_id)
    conn.execute("begin immediate")
    try:
        if has_live_commander_run(conn, goal_id):
            conn.rollback()
            return None
        cursor = conn.execute(
            """
            insert into commander_runs(goal_id, trigger, schema_version, prompt_path, status)
            values (?, ?, ?, ?, 'running')
            """,
            (goal_id, trigger, schema_version, str(prompt_path)),
        )
        run_id = cursor.lastrowid
        conn.commit()
        return run_id
    except sqlite3.Error:
        conn.rollback()
        raise


def finish_commander_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    exit_code: int | None = None,
    timed_out: bool = False,
    raw_output_path: str = "",
    parsed_output_path: str = "",
    progress_summary: str = "",
    stop_reason: str = "",
    error: str = "",
    duration_seconds: float | None = None,
) -> None:
    """Record the completion of a commander run."""
    conn.execute(
        """
        update commander_runs
        set status = ?,
            exit_code = ?,
            timed_out = ?,
            raw_output_path = ?,
            parsed_output_path = ?,
            progress_summary = ?,
            stop_reason = ?,
            error = ?,
            duration_seconds = ?,
            completed_at = current_timestamp
        where id = ?
        """,
        (
            status,
            exit_code,
            1 if timed_out else 0,
            raw_output_path,
            parsed_output_path,
            progress_summary,
            stop_reason,
            error,
            duration_seconds,
            run_id,
        ),
    )
    conn.commit()


def get_latest_commander_run(
    conn: sqlite3.Connection,
    goal_id: int,
) -> sqlite3.Row | None:
    """Return the most recent commander run for a goal."""
    return conn.execute(
        "select * from commander_runs where goal_id = ? order by id desc limit 1",
        (goal_id,),
    ).fetchone()


def list_commander_runs(
    conn: sqlite3.Connection,
    goal_id: int,
) -> list[sqlite3.Row]:
    """Return all commander runs for a goal in chronological order."""
    return conn.execute(
        "select * from commander_runs where goal_id = ? order by id",
        (goal_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Task-goal links
# ---------------------------------------------------------------------------

def insert_task_goal_link(
    conn: sqlite3.Connection,
    goal_id: int,
    task_id: str,
    batch_id: str = "",
    proposal_fingerprint: str = "",
    rationale: str = "",
) -> int:
    """Link a task to a goal without committing (for transactional admission)."""
    cursor = conn.execute(
        """
        insert into task_goal_links(goal_id, task_id, batch_id, proposal_fingerprint, rationale)
        values (?, ?, ?, ?, ?)
        """,
        (goal_id, task_id, batch_id, proposal_fingerprint, rationale),
    )
    return cursor.lastrowid


def link_task_to_goal(
    conn: sqlite3.Connection,
    goal_id: int,
    task_id: str,
    batch_id: str = "",
    proposal_fingerprint: str = "",
    rationale: str = "",
) -> int:
    """Link a task to a goal."""
    link_id = insert_task_goal_link(
        conn,
        goal_id,
        task_id,
        batch_id,
        proposal_fingerprint,
        rationale,
    )
    conn.commit()
    return link_id


def goal_for_task(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    """Return the goal linked to a task, or None."""
    return conn.execute(
        """
        select g.* from goals g
        join task_goal_links tgl on tgl.goal_id = g.id
        where tgl.task_id = ?
        order by tgl.id desc limit 1
        """,
        (task_id,),
    ).fetchone()


def list_linked_tasks(
    conn: sqlite3.Connection,
    goal_id: int,
) -> list[sqlite3.Row]:
    """Return all tasks linked to a goal."""
    return conn.execute(
        """
        select t.*, tgl.batch_id, tgl.proposal_fingerprint, tgl.rationale
        from tasks t
        join task_goal_links tgl on tgl.task_id = t.id
        where tgl.goal_id = ?
        order by tgl.id
        """,
        (goal_id,),
    ).fetchall()


TERMINAL_LINKED_TASK_STATES = frozenset({"done", "failed", "rejected"})


def has_live_commander_run(conn: sqlite3.Connection, goal_id: int) -> bool:
    """Return True when a Commander run is still marked running."""
    row = conn.execute(
        """
        select 1 from commander_runs
        where goal_id = ? and status = 'running'
        limit 1
        """,
        (goal_id,),
    ).fetchone()
    return row is not None


def interrupt_stale_commander_runs(conn: sqlite3.Connection, goal_id: int) -> int:
    """Mark leftover running Commander runs as interrupted."""
    cursor = conn.execute(
        """
        update commander_runs
        set status = 'interrupted',
            error = 'superseded by restart',
            completed_at = current_timestamp
        where goal_id = ? and status = 'running'
        """,
        (goal_id,),
    )
    conn.commit()
    return cursor.rowcount


def linked_tasks_all_terminal(conn: sqlite3.Connection, goal_id: int) -> bool:
    """Return True when every linked task is in a terminal state."""
    counts = linked_task_counts(conn, goal_id)
    if not counts:
        return True
    return all(state in TERMINAL_LINKED_TASK_STATES for state in counts)


def linked_task_counts(conn: sqlite3.Connection, goal_id: int) -> dict[str, int]:
    """Return task state counts for tasks linked to a goal."""
    rows = conn.execute(
        """
        select t.state, count(*) as count
        from tasks t
        join task_goal_links tgl on tgl.task_id = t.id
        where tgl.goal_id = ?
        group by t.state
        """,
        (goal_id,),
    ).fetchall()
    return {row["state"]: row["count"] for row in rows}


# ---------------------------------------------------------------------------
# Commander failure tracking
# ---------------------------------------------------------------------------

def record_commander_failure(
    conn: sqlite3.Connection,
    goal_id: int,
    *,
    max_failures: int = 3,
) -> None:
    """Increment the failure count, schedule retry, and pause after the limit."""
    goal = get_goal(conn, goal_id)
    failures = goal["commander_failures"] + 1
    retry_seconds = 60 if failures == 1 else 300
    if failures >= max_failures:
        conn.execute(
            """
            update goals
            set commander_failures = ?,
                commander_retry_after = '',
                status = 'paused',
                stop_reason = 'commander failure limit reached',
                paused_at = current_timestamp,
                updated_at = current_timestamp
            where id = ?
            """,
            (failures, goal_id),
        )
    else:
        conn.execute(
            """
            update goals
            set commander_failures = ?,
                commander_retry_after = datetime('now', '+' || ? || ' seconds'),
                updated_at = current_timestamp
            where id = ?
            """,
            (failures, retry_seconds, goal_id),
        )
    conn.commit()


def clear_commander_failures(
    conn: sqlite3.Connection,
    goal_id: int,
) -> None:
    """Clear failure count after a successful run."""
    conn.execute(
        """
        update goals
        set commander_failures = 0,
            commander_retry_after = '',
            updated_at = current_timestamp
        where id = ?
        """,
        (goal_id,),
    )
    conn.commit()
