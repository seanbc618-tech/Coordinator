"""Bounded recovery proposals for failed GitHub delivery / CI."""

from __future__ import annotations

import json
import sqlite3

from .github_delivery import get_delivery_record
from .recovery import compute_recovery_dedupe_key


def _delivery_recovery_dedupe_key(delivery_id: int) -> str:
    return compute_recovery_dedupe_key(f"delivery-{delivery_id}", "ci_repair")


def _open_delivery_recovery_exists(
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


def propose_recovery_for_ci_failure(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    delivery_id: int,
    commit: bool = True,
) -> int | None:
    record = get_delivery_record(conn, delivery_id=delivery_id)
    if record is None or record.project_id != project_id:
        return None
    if record.last_check_state != "fail" and record.status != "ci_failed":
        return None
    if record.task_id is None:
        return None

    dedupe_key = _delivery_recovery_dedupe_key(delivery_id)
    if _open_delivery_recovery_exists(
        conn,
        project_id=project_id,
        task_id=record.task_id,
        dedupe_key=dedupe_key,
    ):
        return None

    verify_commands = ["true"]
    title = f"CI recovery: {record.branch_name}"
    rationale = (
        f"Bounded recovery for failed GitHub checks on delivery {delivery_id} "
        f"(PR {record.pr_number})."
    )
    try:
        cursor = conn.execute(
            """
            insert into task_recovery_proposals(
                project_id, task_id, attempt_id, proposal_type, status,
                title, rationale, verification_commands_json, dedupe_key,
                created_at
            ) values (?, ?, NULL, 'ci_repair', 'pending', ?, ?, ?, ?, datetime('now'))
            """,
            (
                project_id,
                record.task_id,
                title,
                rationale,
                json.dumps(verify_commands),
                dedupe_key,
            ),
        )
    except sqlite3.IntegrityError:
        return None

    proposal_id = int(cursor.lastrowid)
    if commit:
        conn.commit()
    return proposal_id