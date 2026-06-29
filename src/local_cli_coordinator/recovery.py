"""Bounded failure recovery proposals for Phase 7."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .autonomous_backlog import BacklogDraft, propose_backlog_items
from .db import get_task


@dataclass(frozen=True)
class RecoveryProposal:
    id: int
    project_id: str
    task_id: str
    attempt_id: int | None
    proposal_type: str
    status: str
    title: str
    rationale: str
    verification_commands: tuple[str, ...]
    dedupe_key: str
    created_at: str
    admitted_backlog_id: str | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_recovery_dedupe_key(task_id: str, proposal_type: str) -> str:
    payload = f"{task_id.strip().lower()}|{proposal_type.strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_to_proposal(row: sqlite3.Row) -> RecoveryProposal:
    commands = json.loads(row["verification_commands_json"])
    return RecoveryProposal(
        id=int(row["id"]),
        project_id=str(row["project_id"]),
        task_id=str(row["task_id"]),
        attempt_id=int(row["attempt_id"]) if row["attempt_id"] is not None else None,
        proposal_type=str(row["proposal_type"]),
        status=str(row["status"]),
        title=str(row["title"]),
        rationale=str(row["rationale"]),
        verification_commands=tuple(str(item) for item in commands),
        dedupe_key=str(row["dedupe_key"]),
        created_at=str(row["created_at"]),
        admitted_backlog_id=(
            str(row["admitted_backlog_id"])
            if row["admitted_backlog_id"]
            else None
        ),
    )


def _task_has_fail_evaluation(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> bool:
    row = conn.execute(
        """
        select 1 from task_evaluations
        where project_id = ? and task_id = ? and verdict in ('fail', 'blocked')
        limit 1
        """,
        (project_id, task_id),
    ).fetchone()
    return row is not None


def _open_recovery_exists(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    dedupe_key: str,
) -> bool:
    row = conn.execute(
        """
        select 1 from task_recovery_proposals
        where project_id = ?
          and task_id = ?
          and dedupe_key = ?
          and status in ('pending', 'admitted')
        limit 1
        """,
        (project_id, task_id, dedupe_key),
    ).fetchone()
    return row is not None


def propose_recovery_for_failed_task(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    attempt_id: int | None = None,
    commit: bool = True,
) -> int | None:
    """Create at most one open recovery proposal for a failed terminal task."""
    task = get_task(conn, task_id)
    if task is None or task["project_id"] != project_id:
        return None
    if str(task["state"]) not in ("failed", "blocked", "cancelled"):
        return None

    proposal_type = "diagnostic" if str(task["state"]) == "blocked" else "repair"
    dedupe_key = compute_recovery_dedupe_key(task_id, proposal_type)
    if _open_recovery_exists(
        conn,
        project_id=project_id,
        task_id=task_id,
        dedupe_key=dedupe_key,
    ):
        return None

    verify_commands = [
        line.strip()
        for line in str(task["verification_commands"]).splitlines()
        if line.strip()
    ]
    if not verify_commands:
        verify_commands = ["true"]

    title = f"Recovery: {task['title']}"
    rationale = (
        f"Bounded recovery proposal for terminal task {task_id} "
        f"in state {task['state']!r}."
    )
    now = _iso_now()
    try:
        cursor = conn.execute(
            """
            insert into task_recovery_proposals(
                project_id, task_id, attempt_id, proposal_type, status,
                title, rationale, verification_commands_json, dedupe_key,
                created_at
            ) values (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                task_id,
                attempt_id,
                proposal_type,
                title,
                rationale,
                json.dumps(verify_commands),
                dedupe_key,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        return None
    proposal_id = int(cursor.lastrowid)
    if commit:
        conn.commit()
    return proposal_id


def list_recovery_proposals(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    status: str | None = None,
) -> list[RecoveryProposal]:
    if status is None:
        rows = conn.execute(
            """
            select * from task_recovery_proposals
            where project_id = ?
            order by created_at desc, id desc
            """,
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select * from task_recovery_proposals
            where project_id = ? and status = ?
            order by created_at desc, id desc
            """,
            (project_id, status),
        ).fetchall()
    return [_row_to_proposal(row) for row in rows]


def admit_recovery_to_backlog(
    conn: sqlite3.Connection,
    *,
    proposal_id: int,
    commit: bool = True,
) -> str | None:
    """Admit a pending recovery proposal into ready backlog after evaluation."""
    row = conn.execute(
        "select * from task_recovery_proposals where id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None or row["status"] != "pending":
        return None

    project_id = str(row["project_id"])
    task_id = str(row["task_id"])
    if not _task_has_fail_evaluation(conn, project_id=project_id, task_id=task_id):
        return None

    verify_commands = json.loads(row["verification_commands_json"])
    inserted = propose_backlog_items(
        conn,
        project_id=project_id,
        goal_id=None,
        drafts=[
            BacklogDraft(
                source="recovery",
                title=str(row["title"]),
                rationale=str(row["rationale"]),
                acceptance_criteria=[
                    f"Recover from failed task {task_id}",
                ],
                verification_commands=[str(cmd) for cmd in verify_commands],
                priority=80,
            )
        ],
    )
    if not inserted:
        return None

    backlog_id = inserted[0]
    conn.execute(
        """
        update task_recovery_proposals
        set status = 'admitted', admitted_backlog_id = ?
        where id = ?
        """,
        (backlog_id, proposal_id),
    )
    conn.execute(
        "update tasks set recovery_proposal_id = ? where id = ?",
        (proposal_id, task_id),
    )
    if commit:
        conn.commit()
    return backlog_id