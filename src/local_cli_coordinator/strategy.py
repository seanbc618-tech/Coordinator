"""Project-scoped strategic milestones for Phase 7."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence


@dataclass(frozen=True)
class Milestone:
    id: int
    project_id: str
    goal_id: int | None
    title: str
    status: str
    priority: int
    success_criteria: tuple[str, ...]
    created_at: str
    updated_at: str
    completed_at: str | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_milestone(row: sqlite3.Row) -> Milestone:
    criteria = json.loads(row["success_criteria_json"])
    return Milestone(
        id=int(row["id"]),
        project_id=str(row["project_id"]),
        goal_id=int(row["goal_id"]) if row["goal_id"] is not None else None,
        title=str(row["title"]),
        status=str(row["status"]),
        priority=int(row["priority"]),
        success_criteria=tuple(str(item) for item in criteria),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
    )


def create_milestone(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    title: str,
    goal_id: int | None = None,
    priority: int = 0,
    success_criteria: Sequence[str] | None = None,
    commit: bool = True,
) -> int:
    """Insert a durable project milestone and return its id."""
    now = _iso_now()
    criteria = list(success_criteria or [])
    cursor = conn.execute(
        """
        insert into project_milestones(
            project_id, goal_id, title, status, priority,
            success_criteria_json, created_at, updated_at
        ) values (?, ?, ?, 'active', ?, ?, ?, ?)
        """,
        (
            project_id,
            goal_id,
            title.strip(),
            priority,
            json.dumps(criteria),
            now,
            now,
        ),
    )
    milestone_id = int(cursor.lastrowid)
    if commit:
        conn.commit()
    return milestone_id


def list_milestones(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    status: str | None = None,
) -> list[Milestone]:
    """Return milestones for one project, optionally filtered by status."""
    if status is None:
        rows = conn.execute(
            """
            select * from project_milestones
            where project_id = ?
            order by priority desc, created_at asc, id asc
            """,
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select * from project_milestones
            where project_id = ? and status = ?
            order by priority desc, created_at asc, id asc
            """,
            (project_id, status),
        ).fetchall()
    return [_row_to_milestone(row) for row in rows]


def get_active_milestone(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> Milestone | None:
    """Return the highest-priority active milestone for a project."""
    row = conn.execute(
        """
        select * from project_milestones
        where project_id = ? and status = 'active'
        order by priority desc, created_at asc, id asc
        limit 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_milestone(row)


def complete_milestone(
    conn: sqlite3.Connection,
    *,
    milestone_id: int,
    commit: bool = True,
) -> None:
    """Mark a milestone completed."""
    now = _iso_now()
    conn.execute(
        """
        update project_milestones
        set status = 'completed',
            completed_at = ?,
            updated_at = ?
        where id = ?
        """,
        (now, now, milestone_id),
    )
    if commit:
        conn.commit()


def build_strategy_summary(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> dict[str, object]:
    """Build a project-scoped strategy summary for RPC and slash surfaces."""
    active = get_active_milestone(conn, project_id=project_id)
    milestones = list_milestones(conn, project_id=project_id, status="active")
    return {
        "project_id": project_id,
        "current_milestone": (
            {
                "id": active.id,
                "title": active.title,
                "priority": active.priority,
                "success_criteria": list(active.success_criteria),
            }
            if active is not None
            else None
        ),
        "active_milestone_count": len(milestones),
        "milestones": [
            {
                "id": milestone.id,
                "title": milestone.title,
                "priority": milestone.priority,
                "status": milestone.status,
            }
            for milestone in milestones
        ],
    }