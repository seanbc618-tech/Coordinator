from dataclasses import dataclass
from pathlib import PurePosixPath

from .config import PolicyConfig, RepoConfig, SUPPORTED_REVIEW_POLICIES
from .models import TaskDraft

_DEPENDENCY_FILES = frozenset({
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "poetry.lock",
    "uv.lock",
})

_PROTECTED_PREFIXES = (
    "config/",
    "migrations/",
    ".github/",
)


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


def _normalize_review_policy(review_policy: str) -> str:
    if review_policy not in SUPPORTED_REVIEW_POLICIES:
        raise ValueError(
            f"unsupported review_policy {review_policy!r}; "
            f"expected one of {sorted(SUPPORTED_REVIEW_POLICIES)}"
        )
    return review_policy


def detect_risk_signals(
    changed_files: list[str],
    *,
    max_files_touched: int,
    spec_review_passed: bool,
    quality_review_passed: bool,
) -> list[str]:
    signals: list[str] = []
    if len(changed_files) > max_files_touched:
        signals.append(
            f"changed file count {len(changed_files)} exceeds limit {max_files_touched}"
        )
    for path in changed_files:
        name = PurePosixPath(path.replace("\\", "/")).name
        normalized = path.replace("\\", "/")
        if normalized.startswith("migrations/") and name.endswith(".sql"):
            signals.append("migration file touched")
        if name in _DEPENDENCY_FILES:
            signals.append("dependency file touched")
        if any(normalized.startswith(prefix) for prefix in _PROTECTED_PREFIXES):
            signals.append(f"protected path touched: {normalized}")
    if not spec_review_passed:
        signals.append("spec review did not pass")
    if not quality_review_passed:
        signals.append("quality review did not pass")
    return signals


def should_require_human_review(
    repo: RepoConfig,
    *,
    changed_files: list[str],
    max_files_touched: int,
    spec_review_passed: bool,
    quality_review_passed: bool,
) -> tuple[bool, list[str]]:
    review_policy = _normalize_review_policy(repo.review_policy)
    if review_policy in {"always_human", "full_review"}:
        return True, ["review_policy requires human review"]
    signals = detect_risk_signals(
        changed_files,
        max_files_touched=max_files_touched,
        spec_review_passed=spec_review_passed,
        quality_review_passed=quality_review_passed,
    )
    if review_policy == "risky_human" and signals:
        return True, signals
    return False, []


def allows_auto_merge(
    repo: RepoConfig,
    *,
    changed_files: list[str],
    max_files_touched: int,
    spec_review_passed: bool,
    quality_review_passed: bool,
) -> bool:
    review_policy = _normalize_review_policy(repo.review_policy)
    if review_policy in {"branch_only", "always_human", "full_review"}:
        return False
    required, _ = should_require_human_review(
        repo,
        changed_files=changed_files,
        max_files_touched=max_files_touched,
        spec_review_passed=spec_review_passed,
        quality_review_passed=quality_review_passed,
    )
    return not required
