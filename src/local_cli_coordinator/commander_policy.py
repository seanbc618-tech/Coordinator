from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from .commander_protocol import CommanderResponse, CommanderTaskProposal
from .config import CoordinatorConfig, select_agent_by_role
from .db import create_task
from .goals import get_goal, insert_task_goal_link
from .models import TaskDraft
from .policy import check_task_draft

MAX_COMMANDER_FAILURES = 3

_HIGH_RISK_SIGNALS = (
    "credential",
    "secret",
    "live trading",
    "funds",
    "market order",
    "destructive migration",
    "drop table",
    "disable security",
)


@dataclass(frozen=True)
class CommanderAdmissionResult:
    accepted_task_ids: list[str]
    rejection_reasons: list[str]
    batch_id: str


def _goal_repo_ids(conn: sqlite3.Connection, goal_id: int) -> list[str]:
    goal = get_goal(conn, goal_id)
    return json.loads(goal["repo_ids"])


def _proposal_text(proposal: CommanderTaskProposal) -> str:
    parts = [
        proposal.title,
        proposal.goal,
        proposal.rationale,
        *proposal.acceptance_criteria,
    ]
    return " ".join(parts).lower()


def _effective_verification(
    proposal: CommanderTaskProposal,
    config: CoordinatorConfig,
) -> list[str]:
    if proposal.verification_commands:
        return list(proposal.verification_commands)
    repo = config.repos.get(proposal.repo)
    if repo is None:
        return []
    return list(repo.verify_commands)


def proposal_fingerprint(goal_id: int, proposal: CommanderTaskProposal) -> str:
    payload = "|".join([
        str(goal_id),
        proposal.repo.strip().lower(),
        proposal.title.strip().lower(),
        proposal.goal.strip().lower(),
        "|".join(sorted(criterion.strip().lower() for criterion in proposal.acceptance_criteria)),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fingerprint_exists(conn: sqlite3.Connection, goal_id: int, fingerprint: str) -> bool:
    row = conn.execute(
        """
        select 1 from task_goal_links
        where goal_id = ? and proposal_fingerprint = ?
        limit 1
        """,
        (goal_id, fingerprint),
    ).fetchone()
    return row is not None


def _title_exists_for_goal(
    conn: sqlite3.Connection,
    goal_id: int,
    title: str,
) -> bool:
    row = conn.execute(
        """
        select 1
        from tasks t
        join task_goal_links tgl on tgl.task_id = t.id
        where tgl.goal_id = ? and lower(t.title) = lower(?)
        limit 1
        """,
        (goal_id, title.strip()),
    ).fetchone()
    return row is not None


def is_high_risk_rejection(reason: str) -> bool:
    return "high-risk" in reason.lower()


def batch_is_high_risk_only(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    goal_id: int,
    response: CommanderResponse,
) -> bool:
    if not response.tasks:
        return False
    for proposal in response.tasks:
        reasons = proposal_rejection_reasons(conn, config, goal_id, proposal)
        if not reasons or not all(is_high_risk_rejection(reason) for reason in reasons):
            return False
    return True


def proposal_rejection_reasons(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    goal_id: int,
    proposal: CommanderTaskProposal,
    *,
    batch_titles: set[str] | None = None,
    batch_fingerprints: set[str] | None = None,
) -> list[str]:
    reasons: list[str] = []

    if proposal.repo not in config.repos:
        reasons.append(f"repo is not allowlisted: {proposal.repo}")

    allowed_repos = _goal_repo_ids(conn, goal_id)
    if allowed_repos and proposal.repo not in allowed_repos:
        reasons.append(f"repo {proposal.repo!r} is outside goal repo allowlist")

    if select_agent_by_role(config, "worker", proposal.capabilities) is None:
        reasons.append(
            f"no worker agent supports capabilities {proposal.capabilities!r}"
        )

    if proposal.expected_files > config.policy.max_files_touched:
        reasons.append(
            "file limit exceeded: "
            f"expected_files {proposal.expected_files} > {config.policy.max_files_touched}"
        )

    if proposal.expected_minutes > config.policy.max_expected_minutes:
        reasons.append(
            "duration limit exceeded: "
            f"expected_minutes {proposal.expected_minutes} > "
            f"{config.policy.max_expected_minutes}"
        )

    combined = _proposal_text(proposal)
    matched = [signal for signal in _HIGH_RISK_SIGNALS if signal in combined]
    if matched:
        reasons.append(f"high-risk proposal signals: {', '.join(matched)}")

    verification_commands = _effective_verification(proposal, config)
    draft = TaskDraft(
        title=proposal.title,
        repo=proposal.repo,
        priority="normal",
        capabilities=list(proposal.capabilities),
        goal=proposal.goal,
        acceptance_criteria=list(proposal.acceptance_criteria),
        verification_commands=verification_commands,
        source_path="",
    )
    reasons.extend(check_task_draft(draft, config.policy).reasons)

    fingerprint = proposal_fingerprint(goal_id, proposal)
    if _fingerprint_exists(conn, goal_id, fingerprint):
        reasons.append("duplicate fingerprint for goal")
    if batch_fingerprints is not None and fingerprint in batch_fingerprints:
        reasons.append("duplicate fingerprint in batch")

    normalized_title = proposal.title.strip().lower()
    if _title_exists_for_goal(conn, goal_id, proposal.title):
        reasons.append("duplicate title for goal")
    if batch_titles is not None and normalized_title in batch_titles:
        reasons.append("duplicate title in batch")

    return reasons


def _filename_slug(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    return slug[:60] or "commander-task"


def admit_commander_response(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    goal_id: int,
    response: CommanderResponse,
    project_id: str = "legacy-default",
) -> CommanderAdmissionResult:
    batch_id = f"batch-{uuid.uuid4().hex[:12]}"
    accepted_task_ids: list[str] = []
    rejection_reasons: list[str] = []
    batch_titles: set[str] = set()
    batch_fingerprints: set[str] = set()
    pending: list[tuple[CommanderTaskProposal, list[str], str]] = []

    for proposal in response.tasks:
        verification_commands = _effective_verification(proposal, config)
        reasons = proposal_rejection_reasons(
            conn,
            config,
            goal_id,
            proposal,
            batch_titles=batch_titles,
            batch_fingerprints=batch_fingerprints,
        )
        fingerprint = proposal_fingerprint(goal_id, proposal)
        if reasons:
            rejection_reasons.extend(
                f"{proposal.title}: {reason}" for reason in reasons
            )
        else:
            pending.append((proposal, verification_commands, fingerprint))
            batch_titles.add(proposal.title.strip().lower())
            batch_fingerprints.add(fingerprint)

    if not pending:
        return CommanderAdmissionResult(
            accepted_task_ids=accepted_task_ids,
            rejection_reasons=rejection_reasons,
            batch_id=batch_id,
        )

    generated = root / "tasks" / "generated"
    generated.mkdir(parents=True, exist_ok=True)

    conn.execute("begin immediate")
    try:
        for proposal, verification_commands, fingerprint in pending:
            source_path = (
                f"tasks/generated/commander-{batch_id}-{_filename_slug(proposal.title)}.md"
            )
            task_id = create_task(
                conn,
                title=proposal.title,
                repo=proposal.repo,
                source_path=source_path,
                priority="normal",
                capabilities=list(proposal.capabilities),
                goal=proposal.goal,
                acceptance_criteria=list(proposal.acceptance_criteria),
                verification_commands=verification_commands,
                project_id=project_id,
                commit=False,
            )
            insert_task_goal_link(
                conn,
                goal_id,
                task_id,
                batch_id,
                fingerprint,
                proposal.rationale,
            )
            accepted_task_ids.append(task_id)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise

    return CommanderAdmissionResult(
        accepted_task_ids=accepted_task_ids,
        rejection_reasons=rejection_reasons,
        batch_id=batch_id,
    )