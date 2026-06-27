"""Convert Commander task proposals into autonomous project backlog rows."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .autonomous_backlog import BacklogDraft, propose_backlog_items
from .commander_protocol import CommanderResponse, CommanderTaskProposal


@dataclass(frozen=True)
class CommanderBacklogGeneration:
    inserted_ids: tuple[str, ...]
    rejected_reasons: tuple[str, ...]
    progress_summary: str
    goal_status: str
    stop_reason: str | None


def proposal_to_backlog_draft(proposal: CommanderTaskProposal) -> BacklogDraft:
    return BacklogDraft(
        source="commander",
        title=proposal.title,
        rationale=proposal.goal or proposal.rationale,
        acceptance_criteria=list(proposal.acceptance_criteria),
        verification_commands=list(proposal.verification_commands),
        execution_policy="normal",
        priority=50,
    )


def commander_response_to_backlog(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int,
    response: CommanderResponse,
    max_items: int,
) -> CommanderBacklogGeneration:
    if response.intent != "task_request" or not response.tasks:
        return CommanderBacklogGeneration(
            inserted_ids=tuple(),
            rejected_reasons=("no task proposals",),
            progress_summary=response.progress_summary,
            goal_status=response.goal_status,
            stop_reason=response.stop_reason,
        )
    drafts = [
        proposal_to_backlog_draft(proposal)
        for proposal in response.tasks[:max_items]
    ]
    inserted = propose_backlog_items(
        conn,
        project_id=project_id,
        goal_id=goal_id,
        drafts=drafts,
    )
    return CommanderBacklogGeneration(
        inserted_ids=tuple(inserted),
        rejected_reasons=tuple(),
        progress_summary=response.progress_summary,
        goal_status=response.goal_status,
        stop_reason=response.stop_reason,
    )