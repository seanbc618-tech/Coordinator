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


def _diff_stats(patch_text: str) -> dict[str, int]:
    insertions = 0
    deletions = 0
    for line in patch_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            insertions += 1
        elif line.startswith("-"):
            deletions += 1
    return {"insertions": insertions, "deletions": deletions}


def record_diff_evidence(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    changed_files: list[str],
    patch_text: str = "",
    attempt_id: int | None = None,
    artifact_path: str | None = None,
    commit: bool = False,
) -> int:
    """Record durable diff evidence for a worker attempt."""
    stats = _diff_stats(patch_text)
    summary = f"{len(changed_files)} files changed"
    if stats["insertions"] or stats["deletions"]:
        summary = (
            f"{len(changed_files)} files changed, "
            f"+{stats['insertions']}/-{stats['deletions']}"
        )
    return record_evidence(
        conn,
        project_id=project_id,
        task_id=task_id,
        attempt_id=attempt_id,
        evidence_type="diff",
        status="present" if changed_files else "absent",
        summary=summary,
        data={
            "changed_files": list(changed_files),
            "insertions": stats["insertions"],
            "deletions": stats["deletions"],
        },
        artifact_path=artifact_path,
        commit=commit,
    )


def record_no_change_evidence(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    attempt_id: int | None = None,
    commit: bool = False,
) -> int:
    """Record that a code task produced no durable file changes."""
    return record_evidence(
        conn,
        project_id=project_id,
        task_id=task_id,
        attempt_id=attempt_id,
        evidence_type="diff",
        status="absent",
        summary="no changed files after worker attempt",
        data={"changed_files": []},
        commit=commit,
    )


def record_verification_evidence(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    verification,
    attempt_id: int | None = None,
    commit: bool = False,
) -> list[int]:
    """Record one command evidence row per verification command result."""
    evidence_ids: list[int] = []
    log_path = str(verification.log_path) if verification.log_path else None
    for result in verification.results:
        status = "passed"
        summary = f"verification command succeeded: {result.command}"
        if result.timed_out:
            status = "failed"
            summary = f"verification command timed out: {result.command}"
        elif result.exit_code != 0:
            status = "failed"
            summary = f"verification command failed: {result.command}"
        evidence_ids.append(
            record_evidence(
                conn,
                project_id=project_id,
                task_id=task_id,
                attempt_id=attempt_id,
                evidence_type="command",
                status=status,
                summary=summary,
                data={
                    "command": result.command,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                },
                artifact_path=log_path,
                commit=False,
            )
        )
    if not verification.results and not verification.passed:
        evidence_ids.append(
            record_evidence(
                conn,
                project_id=project_id,
                task_id=task_id,
                attempt_id=attempt_id,
                evidence_type="command",
                status="failed",
                summary="no verification commands configured",
                data={"command": "", "exit_code": None},
                artifact_path=log_path,
                commit=False,
            )
        )
    if commit:
        conn.commit()
    return evidence_ids