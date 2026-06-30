"""Delivery policy gates for GitHub delivery flows."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .config import CoordinatorConfig
from .db import get_task
from .evidence_evaluator import evaluate_completion_evidence
from .evidence_review import build_merge_ready_payload


@dataclass(frozen=True)
class DeliveryPolicyDecision:
    allowed: bool
    blockers: list[str]
    requires_human_review: bool
    merge_ready: bool


def evaluate_delivery_policy(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    project_id: str,
    task_id: str,
    branch_name: str,
    action: str = "deliver",
) -> DeliveryPolicyDecision:
    task = get_task(conn, task_id)
    if str(task["project_id"]) != project_id:
        raise ValueError(f"task {task_id!r} is not in project {project_id!r}")

    repo_id = str(task["repo"])
    repo = config.repos.get(repo_id)
    blockers: list[str] = []

    if repo is None:
        blockers.append(f"repo {repo_id!r} is not in allowlist")
        return DeliveryPolicyDecision(
            allowed=False,
            blockers=blockers,
            requires_human_review=True,
            merge_ready=False,
        )

    if action in {"deliver", "push"} and not repo.allow_push:
        blockers.append("allow_push=false blocks delivery")

    if repo.merge_policy == "no_push" and action in {"deliver", "push"}:
        blockers.append("merge_policy=no_push blocks delivery")

    gate = evaluate_completion_evidence(
        conn, project_id=project_id, task_id=task_id
    )
    if not gate.allowed:
        blockers.extend(gate.blockers)
        if gate.missing_acceptance:
            blockers.append("missing acceptance evidence")

    merge_payload = build_merge_ready_payload(
        conn,
        project_id=project_id,
        params={"task_id": task_id},
        config=config,
    )
    requires_human = bool(merge_payload.get("requires_human_review"))
    merge_ready = bool(merge_payload.get("merge_ready"))
    if not merge_ready:
        for reason in merge_payload.get("blockers") or []:
            if reason not in blockers:
                blockers.append(str(reason))

    if requires_human and action == "deliver":
        blockers.append("human review required before delivery")

    if not branch_name.strip():
        blockers.append("branch_name is required")

    allowed = not blockers
    return DeliveryPolicyDecision(
        allowed=allowed,
        blockers=list(dict.fromkeys(blockers)),
        requires_human_review=requires_human,
        merge_ready=merge_ready,
    )