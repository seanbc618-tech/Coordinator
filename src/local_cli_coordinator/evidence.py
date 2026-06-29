"""Durable task evidence records for Phase 8 completion gates."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True)
class TaskEvidence:
    id: int
    project_id: str
    task_id: str
    attempt_id: int | None
    evidence_type: str
    status: str
    summary: str
    data: dict[str, Any]
    artifact_path: str | None
    created_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_evidence(row: sqlite3.Row) -> TaskEvidence:
    return TaskEvidence(
        id=int(row["id"]),
        project_id=str(row["project_id"]),
        task_id=str(row["task_id"]),
        attempt_id=int(row["attempt_id"]) if row["attempt_id"] is not None else None,
        evidence_type=str(row["evidence_type"]),
        status=str(row["status"]),
        summary=str(row["summary"]),
        data=json.loads(row["data_json"]),
        artifact_path=str(row["artifact_path"]) if row["artifact_path"] else None,
        created_at=str(row["created_at"]),
    )


def record_evidence(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    evidence_type: str,
    status: str,
    summary: str,
    data: Mapping[str, Any] | None = None,
    attempt_id: int | None = None,
    artifact_path: str | None = None,
    commit: bool = True,
) -> int:
    """Insert one evidence row and return its id."""
    cursor = conn.execute(
        """
        insert into task_evidence(
            project_id, task_id, attempt_id, evidence_type, status,
            summary, data_json, artifact_path, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            task_id,
            attempt_id,
            evidence_type,
            status,
            summary.strip(),
            json.dumps(dict(data or {})),
            artifact_path,
            _iso_now(),
        ),
    )
    evidence_id = int(cursor.lastrowid)
    if commit:
        conn.commit()
    return evidence_id


def list_task_evidence(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> list[TaskEvidence]:
    """Return all evidence rows for one project-scoped task."""
    rows = conn.execute(
        """
        select * from task_evidence
        where project_id = ? and task_id = ?
        order by created_at asc, id asc
        """,
        (project_id, task_id),
    ).fetchall()
    return [_row_to_evidence(row) for row in rows]