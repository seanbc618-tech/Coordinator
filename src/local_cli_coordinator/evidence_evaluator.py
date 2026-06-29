"""Rule-based completion gate from durable task evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Sequence

from .db import get_task
from .evidence import list_task_evidence, record_evidence

RULES_REVIEWER_ID = "rules-v2"


@dataclass(frozen=True)
class CompletionGateResult:
    allowed: bool
    missing_acceptance: bool
    blockers: list[str]
    covered_criteria: tuple[str, ...]
    uncovered_criteria: tuple[str, ...]


def _acceptance_criteria(task: sqlite3.Row) -> list[str]:
    return [
        line.strip()
        for line in str(task["acceptance_criteria"]).splitlines()
        if line.strip()
    ]


def _covered_acceptance_criteria(evidence_rows) -> set[str]:
    covered: set[str] = set()
    for row in evidence_rows:
        if row.evidence_type != "acceptance":
            continue
        if row.status != "covered":
            continue
        criterion = row.data.get("criterion")
        if isinstance(criterion, str) and criterion.strip():
            covered.add(criterion.strip())
    return covered


def record_acceptance_evidence(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    criterion: str,
    status: str = "covered",
    summary: str | None = None,
    attempt_id: int | None = None,
    commit: bool = True,
) -> int:
    """Record acceptance-criterion coverage as durable evidence."""
    text = criterion.strip()
    return record_evidence(
        conn,
        project_id=project_id,
        task_id=task_id,
        attempt_id=attempt_id,
        evidence_type="acceptance",
        status=status,
        summary=summary or f"acceptance criterion covered: {text}",
        data={"criterion": text},
        commit=commit,
    )


def record_rules_verdict(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    verdict: str,
    rationale: str,
    evidence_ids: Sequence[int] | None = None,
    reviewer_id: str = RULES_REVIEWER_ID,
    attempt_id: int | None = None,
    confidence: float = 1.0,
    commit: bool = True,
) -> int:
    """Persist an independent rules-based reviewer verdict."""
    from datetime import datetime, timezone

    cursor = conn.execute(
        """
        insert into task_review_verdicts(
            project_id, task_id, attempt_id, reviewer_id, verdict,
            confidence, rationale, evidence_ids_json, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            task_id,
            attempt_id,
            reviewer_id,
            verdict,
            confidence,
            rationale.strip(),
            json.dumps(list(evidence_ids or [])),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    verdict_id = int(cursor.lastrowid)
    if commit:
        conn.commit()
    return verdict_id


def evaluate_completion_evidence(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> CompletionGateResult:
    """Decide whether durable evidence supports marking a task done."""
    task = get_task(conn, task_id)
    if str(task["project_id"]) != project_id:
        raise ValueError(f"task {task_id!r} does not belong to project {project_id!r}")

    criteria = _acceptance_criteria(task)
    evidence_rows = list_task_evidence(
        conn, project_id=project_id, task_id=task_id
    )
    blockers: list[str] = []

    failed_commands = [
        row
        for row in evidence_rows
        if row.evidence_type == "command" and row.status == "failed"
    ]
    if failed_commands:
        blockers.append("verification command evidence failed")

    covered = _covered_acceptance_criteria(evidence_rows)
    uncovered = [criterion for criterion in criteria if criterion not in covered]
    missing_acceptance = bool(uncovered)

    capabilities = [part for part in str(task["capabilities"]).split(",") if part]
    if "code" in capabilities:
        diff_rows = [row for row in evidence_rows if row.evidence_type == "diff"]
        if any(row.status == "absent" for row in diff_rows):
            blockers.append("code task has no durable file changes")
        elif diff_rows and not any(
            row.status == "present" and row.data.get("changed_files")
            for row in diff_rows
        ):
            blockers.append("code task missing changed-file evidence")

    latest_rules = conn.execute(
        """
        select verdict from task_review_verdicts
        where project_id = ? and task_id = ? and reviewer_id = ?
        order by created_at desc, id desc
        limit 1
        """,
        (project_id, task_id, RULES_REVIEWER_ID),
    ).fetchone()
    if latest_rules is not None and str(latest_rules["verdict"]) == "reject":
        blockers.append("rules evaluator rejected completion")

    allowed = not blockers and not missing_acceptance
    return CompletionGateResult(
        allowed=allowed,
        missing_acceptance=missing_acceptance,
        blockers=blockers,
        covered_criteria=tuple(sorted(covered)),
        uncovered_criteria=tuple(uncovered),
    )