"""Supervisor-facing evidence review payloads."""

from __future__ import annotations

import sqlite3
from typing import Any

from .config import CoordinatorConfig
from .db import get_task
from .evidence import list_task_evidence
from .evidence_evaluator import evaluate_completion_evidence
from .policy import should_require_human_review
from .review_packets_v2 import get_review_packet_v2
from .risk import get_latest_risk_assessment


def _task_id_param(params: dict[str, Any]) -> str | None:
    task_id = params.get("task_id")
    if isinstance(task_id, str) and task_id.strip():
        return task_id.strip()
    args = params.get("args")
    if isinstance(args, str) and args.strip():
        return args.strip().split()[0]
    return None


def _changed_files_from_evidence(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> list[str]:
    files: list[str] = []
    for row in list_task_evidence(conn, project_id=project_id, task_id=task_id):
        if row.evidence_type != "diff":
            continue
        changed = row.data.get("changed_files")
        if isinstance(changed, list):
            files.extend(str(path) for path in changed)
    return files


def build_evidence_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    task_id = _task_id_param(params)
    if task_id is None:
        raise ValueError("task_id is required")
    task = get_task(conn, task_id)
    if str(task["project_id"]) != project_id:
        raise ValueError(f"task {task_id!r} is not in project {project_id!r}")
    rows = list_task_evidence(conn, project_id=project_id, task_id=task_id)
    return {
        "project_id": project_id,
        "task_id": task_id,
        "evidence": [
            {
                "id": row.id,
                "type": row.evidence_type,
                "status": row.status,
                "summary": row.summary,
                "data": row.data,
                "created_at": row.created_at,
            }
            for row in rows
        ],
    }


def build_review_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    task_id = _task_id_param(params)
    if task_id is None:
        raise ValueError("task_id is required")
    task = get_task(conn, task_id)
    if str(task["project_id"]) != project_id:
        raise ValueError(f"task {task_id!r} is not in project {project_id!r}")
    packet = get_review_packet_v2(
        conn, project_id=project_id, task_id=task_id
    )
    gate = evaluate_completion_evidence(
        conn, project_id=project_id, task_id=task_id
    )
    risk = get_latest_risk_assessment(
        conn, project_id=project_id, task_id=task_id
    )
    return {
        "project_id": project_id,
        "task_id": task_id,
        "title": task["title"],
        "state": task["state"],
        "completion_allowed": gate.allowed,
        "blockers": list(gate.blockers),
        "missing_acceptance": gate.missing_acceptance,
        "risk_level": risk.risk_level if risk is not None else None,
        "requires_human_review": bool(risk.requires_human_review) if risk else False,
        "packet": (
            {
                "verdict": packet.verdict,
                "json_path": str(packet.json_path),
                "markdown_path": str(packet.markdown_path),
            }
            if packet is not None
            else None
        ),
    }


def build_risk_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    task_id = _task_id_param(params)
    if task_id is None:
        raise ValueError("task_id is required")
    task = get_task(conn, task_id)
    if str(task["project_id"]) != project_id:
        raise ValueError(f"task {task_id!r} is not in project {project_id!r}")
    risk = get_latest_risk_assessment(
        conn, project_id=project_id, task_id=task_id
    )
    if risk is None:
        return {
            "project_id": project_id,
            "task_id": task_id,
            "risk_level": None,
            "reasons": [],
            "requires_human_review": False,
        }
    return {
        "project_id": project_id,
        "task_id": task_id,
        "risk_level": risk.risk_level,
        "reasons": list(risk.reasons),
        "requires_human_review": risk.requires_human_review,
    }


def build_merge_ready_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    params: dict[str, Any],
    config: CoordinatorConfig | None,
) -> dict[str, Any]:
    task_id = _task_id_param(params)
    if task_id is None:
        raise ValueError("task_id is required")
    task = get_task(conn, task_id)
    if str(task["project_id"]) != project_id:
        raise ValueError(f"task {task_id!r} is not in project {project_id!r}")

    gate = evaluate_completion_evidence(
        conn, project_id=project_id, task_id=task_id
    )
    risk = get_latest_risk_assessment(
        conn, project_id=project_id, task_id=task_id
    )
    changed_files = _changed_files_from_evidence(
        conn, project_id=project_id, task_id=task_id
    )

    requires_human = bool(risk.requires_human_review) if risk is not None else False
    policy_reasons: list[str] = []
    repo = config.repos.get(task["repo"]) if config is not None else None
    if repo is not None:
        policy_requires, policy_reasons = should_require_human_review(
            repo,
            changed_files=changed_files,
            max_files_touched=config.policy.max_files_touched if config else 20,
            spec_review_passed=True,
            quality_review_passed=True,
        )
        requires_human = requires_human or policy_requires

    blockers = list(gate.blockers)
    if gate.missing_acceptance:
        blockers.append("missing acceptance evidence")
    if risk is not None:
        blockers.extend(risk.reasons)
    blockers.extend(policy_reasons)

    merge_ready = gate.allowed and not requires_human
    return {
        "project_id": project_id,
        "task_id": task_id,
        "merge_ready": merge_ready,
        "requires_human_review": requires_human,
        "blockers": list(dict.fromkeys(blockers)),
        "completion_allowed": gate.allowed,
        "risk_level": risk.risk_level if risk is not None else None,
    }