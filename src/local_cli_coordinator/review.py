from dataclasses import dataclass
from pathlib import Path

from .agent import run_agent
from .config import AgentConfig, RepoConfig


@dataclass(frozen=True)
class ReviewResult:
    passed: bool
    log_path: Path
    prompt_path: Path
    timed_out: bool = False


def _format_changed_files(changed_files: list[str]) -> str:
    if not changed_files:
        return "(none)"
    return "\n".join(f"- {path}" for path in changed_files)


def write_spec_review_prompt(task, changed_files: list[str], diff_path: Path, run_dir: Path) -> Path:
    prompt_path = run_dir / "spec_review_prompt.md"
    prompt_path.write_text(
        f"# Spec Review: {task['title']}\n\n"
        f"Repo: {task['repo']}\n\n"
        f"## Goal\n\n{task['goal']}\n\n"
        f"## Acceptance Criteria\n\n{task['acceptance_criteria']}\n\n"
        f"## Changed Files\n\n{_format_changed_files(changed_files)}\n\n"
        f"## Diff\n\n{diff_path.resolve()}\n"
    )
    return prompt_path


def run_spec_review(
    agent: AgentConfig,
    task,
    changed_files: list[str],
    diff_path: Path,
    worktree: Path,
    run_dir: Path,
    timeout_seconds: float | None = None,
) -> ReviewResult:
    prompt_path = write_spec_review_prompt(task, changed_files, diff_path, run_dir)
    agent_result = run_agent(
        agent,
        prompt_path,
        worktree,
        run_dir / "spec_review",
        timeout_seconds=timeout_seconds,
    )
    return ReviewResult(
        passed=agent_result.exit_code == 0 and not agent_result.timed_out,
        log_path=agent_result.log_path,
        prompt_path=prompt_path,
        timed_out=agent_result.timed_out,
    )


def write_quality_review_prompt(
    task,
    changed_files: list[str],
    diff_path: Path,
    verifier_log_path: Path,
    repo: RepoConfig,
    run_dir: Path,
) -> Path:
    prompt_path = run_dir / "quality_review_prompt.md"
    prompt_path.write_text(
        f"# Quality Review: {task['title']}\n\n"
        f"Repo: {task['repo']}\n\n"
        f"## Changed Files\n\n{_format_changed_files(changed_files)}\n\n"
        f"## Diff\n\n{diff_path.resolve()}\n\n"
        f"## Verifier Log\n\n{verifier_log_path.resolve()}\n\n"
        "## Repo Policy\n\n"
        f"allow_push: {repo.allow_push}\n"
        f"merge_policy: {repo.merge_policy}\n"
        f"default_branch: {repo.default_branch}\n"
    )
    return prompt_path


def run_quality_review(
    agent: AgentConfig,
    task,
    changed_files: list[str],
    diff_path: Path,
    verifier_log_path: Path,
    repo: RepoConfig,
    worktree: Path,
    run_dir: Path,
    timeout_seconds: float | None = None,
) -> ReviewResult:
    prompt_path = write_quality_review_prompt(
        task,
        changed_files,
        diff_path,
        verifier_log_path,
        repo,
        run_dir,
    )
    agent_result = run_agent(
        agent,
        prompt_path,
        worktree,
        run_dir / "quality_review",
        timeout_seconds=timeout_seconds,
    )
    return ReviewResult(
        passed=agent_result.exit_code == 0 and not agent_result.timed_out,
        log_path=agent_result.log_path,
        prompt_path=prompt_path,
        timed_out=agent_result.timed_out,
    )
