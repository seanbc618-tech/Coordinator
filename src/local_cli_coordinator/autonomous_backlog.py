"""Autonomous project backlog governance."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .autonomous_loop_db import (
    insert_backlog_item,
    is_open_backlog_dedupe_error,
    list_ready_backlog_items,
    mark_backlog_admitted,
    open_backlog_exists,
)
from .db import create_task

MAX_ACCEPTANCE_CRITERIA = 8
MAX_VERIFICATION_COMMANDS = 5
MAX_TITLE_LENGTH = 200


@dataclass(frozen=True)
class BacklogDraft:
    source: str
    title: str
    rationale: str
    acceptance_criteria: list[str]
    verification_commands: list[str]
    execution_policy: str = "normal"
    priority: int = 50
    milestone_id: int | None = None


def compute_backlog_dedupe_key(
    title: str,
    acceptance_criteria: Sequence[str],
) -> str:
    """Return a stable lowercase hash key for duplicate open-work detection."""
    normalized_title = title.strip().lower()
    normalized_criteria = "|".join(
        sorted(criterion.strip().lower() for criterion in acceptance_criteria)
    )
    payload = f"{normalized_title}|{normalized_criteria}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _small_task_rejection_reasons(draft: BacklogDraft) -> list[str]:
    reasons: list[str] = []
    if not draft.title.strip():
        reasons.append("missing title")
    if len(draft.title.strip()) > MAX_TITLE_LENGTH:
        reasons.append("title too long")
    if len(draft.acceptance_criteria) > MAX_ACCEPTANCE_CRITERIA:
        reasons.append("too many acceptance criteria")
    if len(draft.verification_commands) > MAX_VERIFICATION_COMMANDS:
        reasons.append("too many verification commands")
    return reasons


def propose_backlog_items(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    drafts: Sequence[BacklogDraft],
) -> list[str]:
    """Insert non-duplicate candidate/ready items and return inserted ids."""
    inserted: list[str] = []
    for draft in drafts:
        dedupe_key = compute_backlog_dedupe_key(
            draft.title,
            draft.acceptance_criteria,
        )
        if open_backlog_exists(
            conn,
            project_id=project_id,
            goal_id=goal_id,
            dedupe_key=dedupe_key,
        ):
            continue
        milestone_id = draft.milestone_id
        if milestone_id is not None:
            milestone_row = conn.execute(
                "select project_id from project_milestones where id = ?",
                (milestone_id,),
            ).fetchone()
            if milestone_row is None or milestone_row["project_id"] != project_id:
                continue
        rejections = _small_task_rejection_reasons(draft)
        status = "ready" if not rejections else "candidate"
        try:
            item_id = insert_backlog_item(
                conn,
                project_id=project_id,
                goal_id=goal_id,
                source=draft.source,
                title=draft.title.strip(),
                rationale=draft.rationale,
                acceptance_criteria=list(draft.acceptance_criteria),
                verification_commands=list(draft.verification_commands),
                execution_policy=draft.execution_policy,
                priority=draft.priority,
                status=status,
                dedupe_key=dedupe_key,
                milestone_id=milestone_id,
                commit=False,
            )
        except sqlite3.IntegrityError as exc:
            if not is_open_backlog_dedupe_error(exc):
                raise
            continue
        if rejections:
            conn.execute(
                """
                update project_backlog_items
                set rejection_reason = ?, updated_at = current_timestamp
                where id = ?
                """,
                ("; ".join(rejections), item_id),
            )
        inserted.append(item_id)
    if inserted:
        conn.commit()
    return inserted


def promote_next_backlog_item(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    repo_path: Path,
    max_items: int = 1,
) -> list[str]:
    """Create ready tasks from backlog and mark items admitted."""
    if max_items <= 0:
        return []
    from .roadmap_readiness import backlog_item_roadmap_ready

    items = list_ready_backlog_items(
        conn,
        project_id=project_id,
        goal_id=goal_id,
        limit=max(max_items * 4, max_items),
    )
    if not items:
        return []

    task_ids: list[str] = []
    for item in items:
        ready, roadmap_node_id = backlog_item_roadmap_ready(
            conn,
            project_id=project_id,
            backlog_id=str(item["id"]),
        )
        if not ready:
            continue
        criteria = json.loads(item["acceptance_criteria_json"])
        verify_commands = json.loads(item["verification_commands_json"])
        execution_policy = item["execution_policy"]
        if execution_policy != "normal":
            policy_json = execution_policy
        else:
            policy_json = "{}"
        source_path = f"tasks/generated/backlog-{item['id']}.md"
        task_id = create_task(
            conn,
            title=item["title"],
            repo=str(repo_path),
            source_path=source_path,
            priority="normal",
            capabilities=["code"],
            goal=item["rationale"],
            acceptance_criteria=[str(c) for c in criteria],
            verification_commands=[str(c) for c in verify_commands],
            project_id=project_id,
            execution_policy=policy_json,
            commit=False,
        )
        mark_backlog_admitted(
            conn,
            item_id=item["id"],
            linked_task_id=task_id,
            commit=False,
        )
        task_ids.append(task_id)
        if len(task_ids) >= max_items:
            break
    if task_ids:
        conn.commit()
    return task_ids