"""Durable PR health, healing attempts, and CI failure records."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

VALID_HEALTH_STATUS = frozenset({
    "observed",
    "healthy",
    "stale",
    "ci_failed",
    "review_blocked",
    "ready",
    "merged",
    "closed",
})
VALID_HEALING_ACTIONS = frozenset({
    "watch",
    "rebase_dry_run",
    "rebase_apply",
    "ci_repair",
    "evidence_update",
    "review_ingest",
})
VALID_HEALING_STATUS = frozenset({
    "started",
    "succeeded",
    "failed",
    "blocked",
    "skipped",
})
VALID_FAILURE_CLASS = frozenset({
    "test_failure",
    "lint_failure",
    "typecheck_failure",
    "build_failure",
    "flaky",
    "infra",
    "unknown",
})


@dataclass(frozen=True)
class PrHealthRecord:
    id: str
    project_id: str
    delivery_id: int
    pr_number: int
    status: str
    head_branch: str
    base_branch: str
    head_sha: str
    base_sha: str
    merge_state: str
    ci_state: str
    review_state: str
    stale: bool
    last_checked_at: str
    updated_at: str


@dataclass(frozen=True)
class PrHealingAttempt:
    id: str
    project_id: str
    delivery_id: int
    pr_health_id: str
    action: str
    status: str
    worktree_path: str
    evidence_path: str
    error: str
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class CiFailureRecord:
    id: str
    project_id: str
    delivery_id: int
    check_name: str
    status: str
    conclusion: str
    failure_class: str
    summary: str
    evidence: dict[str, Any]
    recovery_task_id: str | None
    created_at: str
    updated_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "prhealth") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _validate_enum(value: str, allowed: frozenset[str], field: str) -> str:
    text = value.strip()
    if text not in allowed:
        raise ValueError(f"invalid {field}: {value!r}")
    return text


def _row_health(row: sqlite3.Row) -> PrHealthRecord:
    return PrHealthRecord(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        delivery_id=int(row["delivery_id"]),
        pr_number=int(row["pr_number"]),
        status=str(row["status"]),
        head_branch=str(row["head_branch"]),
        base_branch=str(row["base_branch"]),
        head_sha=str(row["head_sha"]),
        base_sha=str(row["base_sha"]),
        merge_state=str(row["merge_state"]),
        ci_state=str(row["ci_state"]),
        review_state=str(row["review_state"]),
        stale=bool(row["stale"]),
        last_checked_at=str(row["last_checked_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_healing(row: sqlite3.Row) -> PrHealingAttempt:
    return PrHealingAttempt(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        delivery_id=int(row["delivery_id"]),
        pr_health_id=str(row["pr_health_id"]),
        action=str(row["action"]),
        status=str(row["status"]),
        worktree_path=str(row["worktree_path"]),
        evidence_path=str(row["evidence_path"]),
        error=str(row["error"]),
        created_at=str(row["created_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
    )


def _row_ci_failure(row: sqlite3.Row) -> CiFailureRecord:
    return CiFailureRecord(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        delivery_id=int(row["delivery_id"]),
        check_name=str(row["check_name"]),
        status=str(row["status"]),
        conclusion=str(row["conclusion"]),
        failure_class=str(row["failure_class"]),
        summary=str(row["summary"]),
        evidence=json.loads(row["evidence_json"]),
        recovery_task_id=(
            str(row["recovery_task_id"]) if row["recovery_task_id"] else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def upsert_pr_health_record(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    delivery_id: int,
    pr_number: int,
    head_branch: str,
    base_branch: str,
    status: str = "observed",
    head_sha: str = "",
    base_sha: str = "",
    merge_state: str = "unknown",
    ci_state: str = "unknown",
    review_state: str = "unknown",
    stale: bool = False,
    commit: bool = False,
) -> PrHealthRecord:
    _validate_enum(status, VALID_HEALTH_STATUS, "status")
    now = _iso_now()
    existing = conn.execute(
        """
        select id from pr_health_records
        where project_id = ? and delivery_id = ?
        """,
        (project_id, delivery_id),
    ).fetchone()
    if existing is not None:
        health_id = str(existing["id"])
        conn.execute(
            """
            update pr_health_records
            set pr_number = ?, status = ?, head_branch = ?, base_branch = ?,
                head_sha = ?, base_sha = ?, merge_state = ?, ci_state = ?,
                review_state = ?, stale = ?, last_checked_at = ?, updated_at = ?
            where id = ?
            """,
            (
                pr_number,
                status,
                head_branch,
                base_branch,
                head_sha,
                base_sha,
                merge_state,
                ci_state,
                review_state,
                1 if stale else 0,
                now,
                now,
                health_id,
            ),
        )
    else:
        health_id = _new_id()
        conn.execute(
            """
            insert into pr_health_records(
                id, project_id, delivery_id, pr_number, status, head_branch,
                base_branch, head_sha, base_sha, merge_state, ci_state,
                review_state, stale, last_checked_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                health_id,
                project_id,
                delivery_id,
                pr_number,
                status,
                head_branch,
                base_branch,
                head_sha,
                base_sha,
                merge_state,
                ci_state,
                review_state,
                1 if stale else 0,
                now,
                now,
            ),
        )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from pr_health_records where id = ?",
        (health_id,),
    ).fetchone()
    assert row is not None
    return _row_health(row)


def get_pr_health_record(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    delivery_id: int,
) -> PrHealthRecord | None:
    row = conn.execute(
        """
        select * from pr_health_records
        where project_id = ? and delivery_id = ?
        """,
        (project_id, delivery_id),
    ).fetchone()
    return _row_health(row) if row is not None else None


def list_pr_health_records(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    status: str | None = None,
    stale_only: bool = False,
) -> list[PrHealthRecord]:
    query = "select * from pr_health_records where project_id = ?"
    params: list[Any] = [project_id]
    if status is not None:
        query += " and status = ?"
        params.append(_validate_enum(status, VALID_HEALTH_STATUS, "status"))
    if stale_only:
        query += " and stale = 1"
    query += " order by updated_at desc"
    rows = conn.execute(query, params).fetchall()
    return [_row_health(row) for row in rows]


def create_healing_attempt(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    delivery_id: int,
    pr_health_id: str,
    action: str,
    status: str = "started",
    worktree_path: str = "",
    evidence_path: str = "",
    error: str = "",
    commit: bool = False,
) -> PrHealingAttempt:
    _validate_enum(action, VALID_HEALING_ACTIONS, "action")
    _validate_enum(status, VALID_HEALING_STATUS, "status")
    now = _iso_now()
    attempt_id = _new_id("prheal")
    conn.execute(
        """
        insert into pr_healing_attempts(
            id, project_id, delivery_id, pr_health_id, action, status,
            worktree_path, evidence_path, error, created_at, completed_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            attempt_id,
            project_id,
            delivery_id,
            pr_health_id,
            action,
            status,
            worktree_path,
            evidence_path,
            error,
            now,
        ),
    )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from pr_healing_attempts where id = ?",
        (attempt_id,),
    ).fetchone()
    assert row is not None
    return _row_healing(row)


def complete_healing_attempt(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    status: str,
    error: str = "",
    evidence_path: str = "",
    worktree_path: str = "",
    commit: bool = False,
) -> PrHealingAttempt:
    _validate_enum(status, VALID_HEALING_STATUS, "status")
    now = _iso_now()
    conn.execute(
        """
        update pr_healing_attempts
        set status = ?, error = ?, evidence_path = ?, worktree_path = ?,
            completed_at = ?
        where id = ?
        """,
        (status, error, evidence_path, worktree_path, now, attempt_id),
    )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from pr_healing_attempts where id = ?",
        (attempt_id,),
    ).fetchone()
    assert row is not None
    return _row_healing(row)


def upsert_ci_failure_record(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    delivery_id: int,
    check_name: str,
    status: str,
    conclusion: str,
    failure_class: str,
    summary: str = "",
    evidence: Mapping[str, Any] | None = None,
    recovery_task_id: str | None = None,
    commit: bool = False,
) -> CiFailureRecord:
    _validate_enum(failure_class, VALID_FAILURE_CLASS, "failure_class")
    now = _iso_now()
    evidence_json = json.dumps(dict(evidence or {}))
    existing = conn.execute(
        """
        select id from ci_failure_records
        where project_id = ? and delivery_id = ? and check_name = ?
          and conclusion = ? and failure_class = ?
        """,
        (project_id, delivery_id, check_name, conclusion, failure_class),
    ).fetchone()
    if existing is not None:
        record_id = str(existing["id"])
        conn.execute(
            """
            update ci_failure_records
            set status = ?, summary = ?, evidence_json = ?,
                recovery_task_id = ?, updated_at = ?
            where id = ?
            """,
            (
                status,
                summary,
                evidence_json,
                recovery_task_id,
                now,
                record_id,
            ),
        )
    else:
        record_id = _new_id("cifail")
        conn.execute(
            """
            insert into ci_failure_records(
                id, project_id, delivery_id, check_name, status, conclusion,
                failure_class, summary, evidence_json, recovery_task_id,
                created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                project_id,
                delivery_id,
                check_name,
                status,
                conclusion,
                failure_class,
                summary,
                evidence_json,
                recovery_task_id,
                now,
                now,
            ),
        )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from ci_failure_records where id = ?",
        (record_id,),
    ).fetchone()
    assert row is not None
    return _row_ci_failure(row)


def list_ci_failure_records(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    delivery_id: int | None = None,
) -> list[CiFailureRecord]:
    if delivery_id is None:
        rows = conn.execute(
            """
            select * from ci_failure_records
            where project_id = ?
            order by updated_at desc
            """,
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select * from ci_failure_records
            where project_id = ? and delivery_id = ?
            order by updated_at desc
            """,
            (project_id, delivery_id),
        ).fetchall()
    return [_row_ci_failure(row) for row in rows]