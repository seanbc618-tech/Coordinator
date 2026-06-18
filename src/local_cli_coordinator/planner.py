"""Rule-based planner that converts discovery findings into small task drafts.

The planner enforces the PDF's guidance: findings must be cut into small,
actionable handoffs before agents start writing code.  Broad or vague findings
are rejected with a ``needs_split`` reason so an operator or LLM can break
them down further.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Finding, TaskDraft

# Maximum number of acceptance criteria a single task may carry before the
# planner considers it too broad.
MAX_ACCEPTANCE_CRITERIA = 5

# Words that signal a finding is too broad for a single task.
_BROAD_SIGNALS = frozenset({
    "refactor",
    "rewrite",
    "redesign",
    "overhaul",
    "migrate",
    "everything",
    "all",
    "entire",
    "complete",
    "comprehensive",
})

# Words that signal a finding is vague or needs clarification.
_VAGUE_SIGNALS = frozenset({
    "maybe",
    "somehow",
    "figure out",
    "investigate",
    "explore",
    "research",
    "tbd",
    "todo",
})


@dataclass(frozen=True)
class PlanResult:
    """Outcome of attempting to plan a finding."""

    tasks: list[TaskDraft]
    needs_split: list[str]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _has_broad_signals(text: str) -> bool:
    normalized = _normalize(text)
    return any(signal in normalized for signal in _BROAD_SIGNALS)


def _has_vague_signals(text: str) -> bool:
    normalized = _normalize(text)
    return any(signal in normalized for signal in _VAGUE_SIGNALS)


def _extract_acceptance_criteria(finding: Finding) -> list[str]:
    """Derive acceptance criteria from the finding body.

    Each non-empty line in the body that starts with a bullet or dash becomes
    a criterion.  If no bullets are found the body itself is used as a single
    criterion (assuming it is specific enough).
    """
    criteria: list[str] = []
    for line in finding.body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "• ")):
            criteria.append(stripped[2:].strip())
    if not criteria:
        body = finding.body.strip()
        if body:
            criteria.append(body)
    return criteria


def _capability_from_source(source: str) -> list[str]:
    """Map a finding source to a rough capability requirement."""
    if "ci" in source:
        return ["code", "test"]
    if "issue" in source:
        return ["code"]
    if "commit" in source:
        return ["code"]
    return ["code"]


def plan_finding(finding: Finding) -> PlanResult:
    """Convert a single finding into task drafts or split reasons.

    Returns a :class:`PlanResult` with either one or more ``TaskDraft``
    objects, or one or more ``needs_split`` reasons explaining why the
    finding cannot be planned as-is.
    """
    reasons: list[str] = []

    if _has_broad_signals(finding.title) or _has_broad_signals(finding.body):
        reasons.append(
            f"Finding {finding.id!r} contains broad language "
            "(refactor/rewrite/migrate/etc). Break it into smaller tasks."
        )

    if _has_vague_signals(finding.title) or _has_vague_signals(finding.body):
        reasons.append(
            f"Finding {finding.id!r} contains vague language "
            "(maybe/somehow/investigate/etc). Clarify before planning."
        )

    criteria = _extract_acceptance_criteria(finding)
    if len(criteria) > MAX_ACCEPTANCE_CRITERIA:
        reasons.append(
            f"Finding {finding.id!r} has {len(criteria)} acceptance criteria, "
            f"which exceeds the limit of {MAX_ACCEPTANCE_CRITERIA}. Split it."
        )

    if not criteria:
        reasons.append(
            f"Finding {finding.id!r} has no extractable acceptance criteria."
        )

    if reasons:
        return PlanResult(tasks=[], needs_split=reasons)

    task = TaskDraft(
        title=finding.title,
        repo=finding.repo,
        priority="normal",
        capabilities=_capability_from_source(finding.source),
        goal=finding.body or finding.title,
        acceptance_criteria=criteria,
        source_path=f"state/findings/{finding.id}.jsonl",
    )
    return PlanResult(tasks=[task], needs_split=[])


def plan_findings(findings: list[Finding]) -> PlanResult:
    """Plan a batch of findings, aggregating tasks and split reasons."""
    all_tasks: list[TaskDraft] = []
    all_reasons: list[str] = []
    for finding in findings:
        result = plan_finding(finding)
        all_tasks.extend(result.tasks)
        all_reasons.extend(result.needs_split)
    return PlanResult(tasks=all_tasks, needs_split=all_reasons)
