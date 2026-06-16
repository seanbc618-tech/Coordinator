from dataclasses import dataclass

from .config import PolicyConfig
from .models import TaskDraft


@dataclass(frozen=True)
class PolicyResult:
    accepted: bool
    reasons: list[str]


def _has_nonblank_item(values: list[str]) -> bool:
    return any(value.strip() for value in values)


def check_task_draft(task: TaskDraft, policy: PolicyConfig) -> PolicyResult:
    reasons: list[str] = []
    if policy.require_single_repo and not task.repo.strip():
        reasons.append("missing repo")
    if policy.require_acceptance_criteria and not _has_nonblank_item(
        task.acceptance_criteria
    ):
        reasons.append("missing acceptance criteria")
    if policy.require_verification_commands and not _has_nonblank_item(
        task.verification_commands
    ):
        reasons.append("missing verification commands")
    if not task.goal.strip():
        reasons.append("missing goal")
    return PolicyResult(accepted=not reasons, reasons=reasons)


def check_changed_files(changed_files: list[str], policy: PolicyConfig) -> PolicyResult:
    reasons: list[str] = []
    if len(changed_files) > policy.max_files_touched:
        reasons.append(
            f"changed file count {len(changed_files)} exceeds limit {policy.max_files_touched}"
        )
    return PolicyResult(accepted=not reasons, reasons=reasons)
