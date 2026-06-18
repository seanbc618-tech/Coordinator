"""Rule-based planner that converts discovery findings into small task drafts.

The planner enforces the PDF's guidance: findings must be cut into small,
actionable handoffs before agents start writing code.  Broad or vague findings
are rejected with a ``needs_split`` reason so an operator or LLM can break
them down further.

An optional LLM planner agent can propose task drafts; the rule-based guard
always validates its output before accepting it.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import AgentConfig, CoordinatorConfig, select_agent_by_role
from .models import Finding, TaskDraft
from .process import run_command

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


# ---------------------------------------------------------------------------
# LLM planner hook
# ---------------------------------------------------------------------------

_PLANNER_PROMPT_TEMPLATE = """\
You are a task planning assistant. Given a discovery finding, produce one or
more small, actionable task drafts as JSONL (one JSON object per line).

Each JSON object must have these fields:
- title: short imperative description
- repo: repository id
- priority: "low", "normal", or "high"
- capabilities: list of strings (e.g. ["code"])
- goal: what the task should accomplish
- acceptance_criteria: list of specific, testable criteria

Rules:
- Each task must be small enough for a single agent session.
- Acceptance criteria must be concrete and testable.
- Do NOT produce broad tasks like "refactor everything".
- If the finding is too broad, output {{"needs_split": "reason"}} instead.

Finding:
- id: {finding_id}
- repo: {repo}
- source: {source}
- title: {title}
- body: {body}
- severity: {severity}
- evidence: {evidence}
"""


def _parse_agent_output(output: str) -> list[TaskDraft] | list[str]:
    """Parse planner agent output into TaskDrafts or split reasons.

    Returns either a list of TaskDraft objects or a list of error/split
    reason strings.
    """
    tasks: list[TaskDraft] = []
    reasons: list[str] = []

    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if "needs_split" in data:
            reasons.append(str(data["needs_split"]))
            continue

        try:
            task = TaskDraft(
                title=str(data["title"]),
                repo=str(data["repo"]),
                priority=str(data.get("priority", "normal")),
                capabilities=list(data.get("capabilities", ["code"])),
                goal=str(data.get("goal", "")),
                acceptance_criteria=list(data.get("acceptance_criteria", [])),
            )
            tasks.append(task)
        except (KeyError, TypeError, ValueError):
            continue

    if reasons:
        return reasons
    return tasks


def _validate_draft(draft: TaskDraft, finding_id: str) -> list[str]:
    """Apply rule-based guard to a planner-proposed task draft."""
    reasons: list[str] = []

    if _has_broad_signals(draft.title):
        reasons.append(
            f"LLM-proposed task for {finding_id!r} has broad title: {draft.title!r}"
        )

    if len(draft.acceptance_criteria) > MAX_ACCEPTANCE_CRITERIA:
        reasons.append(
            f"LLM-proposed task for {finding_id!r} has "
            f"{len(draft.acceptance_criteria)} acceptance criteria, "
            f"which exceeds the limit of {MAX_ACCEPTANCE_CRITERIA}."
        )

    if not draft.acceptance_criteria:
        reasons.append(
            f"LLM-proposed task for {finding_id!r} has no acceptance criteria."
        )

    return reasons


def plan_finding_with_agent(
    finding: Finding,
    config: CoordinatorConfig,
    timeout_seconds: float | None = 60,
) -> PlanResult:
    """Plan a finding using a configured planner agent, guarded by rules.

    If no planner agent is configured, falls back to rule-based planning.
    The agent's output is always validated by the rule-based guard before
    being accepted.
    """
    planner_agent = select_agent_by_role(config, "planner")
    if planner_agent is None:
        return plan_finding(finding)

    prompt_text = _PLANNER_PROMPT_TEMPLATE.format(
        finding_id=finding.id,
        repo=finding.repo,
        source=finding.source,
        title=finding.title,
        body=finding.body,
        severity=finding.severity,
        evidence=finding.evidence,
    )

    with tempfile.TemporaryDirectory() as tmp:
        prompt_path = Path(tmp) / "planner_prompt.md"
        prompt_path.write_text(prompt_text, encoding="utf-8")

        workdir = Path(tmp) / "workdir"
        workdir.mkdir()

        try:
            argv = [planner_agent.command, str(prompt_path)]
            result = run_command(
                argv,
                cwd=workdir,
                timeout_seconds=timeout_seconds,
            )
        except (OSError, ValueError):
            return plan_finding(finding)

        if result.returncode != 0:
            return plan_finding(finding)

        parsed = _parse_agent_output(result.stdout)

    if isinstance(parsed, list) and parsed and isinstance(parsed[0], str):
        return PlanResult(tasks=[], needs_split=parsed)  # type: ignore[arg-type]

    drafts: list[TaskDraft] = parsed  # type: ignore[assignment]
    if not drafts:
        return plan_finding(finding)

    # Apply rule-based guard to every proposed draft
    all_tasks: list[TaskDraft] = []
    all_reasons: list[str] = []
    for draft in drafts:
        guard_reasons = _validate_draft(draft, finding.id)
        if guard_reasons:
            all_reasons.extend(guard_reasons)
        else:
            # Attach source path
            all_tasks.append(
                TaskDraft(
                    title=draft.title,
                    repo=draft.repo,
                    priority=draft.priority,
                    capabilities=draft.capabilities,
                    goal=draft.goal,
                    acceptance_criteria=draft.acceptance_criteria,
                    source_path=f"state/findings/{finding.id}.jsonl",
                )
            )

    if all_reasons:
        return PlanResult(tasks=[], needs_split=all_reasons)

    return PlanResult(tasks=all_tasks, needs_split=[])
