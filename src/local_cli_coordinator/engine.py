from pathlib import Path
import re
import sqlite3

from .agent import run_agent
from .config import CoordinatorConfig, RepoConfig, select_agent_by_role
from .db import (
    add_artifact,
    artifact_kinds,
    circuit_breaker_reason,
    get_task,
    next_ready_task,
    set_task_branch_and_worktree,
    transition_task,
)
from .gitops import (
    collect_changed_files,
    commit_all,
    create_worktree,
    diff_patch,
    merge_branch_to_default,
    push_branch,
)
from .policy import check_changed_files
from .review import run_quality_review, run_spec_review
from .verify import run_verification
from .memory import LoopMemoryEntry, append_loop_memory, loop_memory_path


def _slug(text: str) -> str:
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned[:40] or "task"


def _select_agent(config: CoordinatorConfig, capabilities: list[str], role: str = "worker"):
    if not capabilities:
        return None
    return select_agent_by_role(config, role, capabilities)


def _read_optional_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def _resolve_memory_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _write_prompt(task, run_dir: Path, root: Path, repo: RepoConfig) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    sections = [
        f"# Task: {task['title']}\n",
        f"Repo: {task['repo']}\n",
        f"## Goal\n\n{task['goal']}\n",
        f"## Acceptance Criteria\n\n{task['acceptance_criteria']}\n",
    ]
    loop_memory = _read_optional_text(loop_memory_path(root))
    if loop_memory is not None:
        sections.append(f"## Loop Memory\n\n{loop_memory}\n")
    if repo.memory_path is not None:
        repo_memory = _read_optional_text(_resolve_memory_path(root, repo.memory_path))
        if repo_memory is not None:
            sections.append(f"## Repo Memory\n\n{repo_memory}\n")
    prompt = run_dir / "prompt.md"
    prompt.write_text("\n".join(sections))
    return prompt


def _finish_task(
    conn: sqlite3.Connection,
    root: Path,
    task_id: str,
    state: str,
    note: str,
    *,
    verifier_result: str,
    next_action: str,
) -> None:
    transition_task(conn, task_id, state, note)
    task = get_task(conn, task_id)
    append_loop_memory(
        root,
        LoopMemoryEntry(
            task_id=task["id"],
            repo=task["repo"],
            title=task["title"],
            outcome=state,
            branch=task["branch"],
            verifier_result=verifier_result,
            next_action=next_action,
        ),
    )


def _review_failure_state(repo: RepoConfig) -> str:
    if repo.merge_policy == "auto_merge_default_branch":
        return "rejected"
    return "awaiting_human"


def _missing_completion_evidence(
    conn: sqlite3.Connection,
    task_id: str,
    repo: RepoConfig,
) -> list[str]:
    required = {"verifier_log"}
    if repo.review_policy != "tests_only":
        required.update({"spec_review_log", "quality_review_log"})
    return sorted(required - artifact_kinds(conn, task_id))


def run_one_ready_task(conn: sqlite3.Connection, config: CoordinatorConfig, root: Path) -> bool:
    if circuit_breaker_reason(conn, config.policy) is not None:
        return False
    task = next_ready_task(conn)
    if task is None:
        return False
    repo = config.repos.get(task["repo"])
    if repo is None:
        _finish_task(
            conn,
            root,
            task["id"],
            "blocked",
            f"repo is not configured: {task['repo']}",
            verifier_result="not run",
            next_action="configure repo allowlist",
        )
        return True
    capabilities = [part for part in task["capabilities"].split(",") if part]
    if not config.agents:
        _finish_task(
            conn,
            root,
            task["id"],
            "blocked",
            "no configured agents",
            verifier_result="not run",
            next_action="configure an agent",
        )
        return True
    agent = _select_agent(config, capabilities)
    if agent is None:
        capability_text = ", ".join(capabilities) if capabilities else "(none)"
        _finish_task(
            conn,
            root,
            task["id"],
            "blocked",
            f"no matching agent for capabilities: {capability_text}",
            verifier_result="not run",
            next_action="split task or configure capable agent",
        )
        return True
    branch = f"{repo.branch_prefix}{task['id']}-{_slug(task['title'])}"
    run_dir = root / "runs" / task["id"]

    transition_task(conn, task["id"], "running", f"assigned to {agent.id}")
    try:
        worktree = create_worktree(
            repo_path=repo.path,
            worktrees_root=root / "worktrees" / repo.id,
            task_id=task["id"],
            branch_name=branch,
        )
    except (RuntimeError, OSError) as exc:
        _finish_task(
            conn,
            root,
            task["id"],
            "failed",
            f"worktree creation failed: {exc}",
            verifier_result="not run",
            next_action="inspect git worktree setup and retry",
        )
        return True
    set_task_branch_and_worktree(conn, task["id"], branch, worktree)
    prompt = _write_prompt(task, run_dir, root, repo)
    agent_result = run_agent(
        agent,
        prompt,
        worktree,
        run_dir,
        timeout_seconds=config.policy.max_task_runtime_seconds,
    )
    add_artifact(conn, task["id"], "agent_log", agent_result.log_path)
    if agent_result.exit_code != 0 or agent_result.timed_out:
        _finish_task(
            conn,
            root,
            task["id"],
            "failed",
            (
                f"agent command timed out after "
                f"{config.policy.max_task_runtime_seconds} seconds"
                if agent_result.timed_out
                else "agent command failed"
            ),
            verifier_result="not run",
            next_action="inspect agent log and retry",
        )
        return True

    changed_files = collect_changed_files(worktree)
    if not changed_files:
        _finish_task(
            conn,
            root,
            task["id"],
            "failed",
            "no changed files",
            verifier_result="not run",
            next_action="inspect agent output and retry",
        )
        return True
    policy_result = check_changed_files(changed_files, config.policy)
    if not policy_result.accepted:
        _finish_task(
            conn,
            root,
            task["id"],
            "needs_split",
            "; ".join(policy_result.reasons),
            verifier_result="not run",
            next_action="split task smaller",
        )
        return True

    patch_path = run_dir / "diff.patch"
    patch_path.write_text(diff_patch(worktree))
    add_artifact(conn, task["id"], "diff", patch_path)

    transition_task(conn, task["id"], "verifying", "running verification")
    commands = [line for line in task["verification_commands"].splitlines() if line] or repo.verify_commands
    verification = run_verification(
        commands,
        worktree,
        run_dir,
        timeout_seconds=config.policy.max_task_runtime_seconds,
    )
    add_artifact(conn, task["id"], "verifier_log", verification.log_path)
    if not verification.passed:
        _finish_task(
            conn,
            root,
            task["id"],
            "failed",
            (
                f"verification timed out after "
                f"{config.policy.max_task_runtime_seconds} seconds"
                if verification.timed_out
                else "verification failed"
            ),
            verifier_result="timed out" if verification.timed_out else "failed",
            next_action="inspect verifier log and retry",
        )
        return True

    spec_review_passed = False
    spec_reviewer = _select_agent(config, capabilities, role="spec_reviewer")
    if spec_reviewer is not None:
        transition_task(conn, task["id"], "reviewing_spec", "running spec review")
        spec_review = run_spec_review(
            spec_reviewer,
            task,
            changed_files,
            patch_path,
            worktree,
            run_dir,
            timeout_seconds=config.policy.max_task_runtime_seconds,
        )
        add_artifact(conn, task["id"], "spec_review_log", spec_review.log_path)
        if not spec_review.passed:
            state = "failed" if spec_review.timed_out else _review_failure_state(repo)
            _finish_task(
                conn,
                root,
                task["id"],
                state,
                (
                    f"spec review timed out after "
                    f"{config.policy.max_task_runtime_seconds} seconds"
                    if spec_review.timed_out
                    else "spec review failed"
                ),
                verifier_result="passed",
                next_action=(
                    "inspect spec reviewer log and retry"
                    if spec_review.timed_out
                    else "address spec review feedback"
                ),
            )
            return True
        spec_review_passed = True

    quality_reviewer = _select_agent(config, capabilities, role="quality_reviewer")
    if quality_reviewer is not None:
        if not spec_review_passed:
            state = _review_failure_state(repo)
            _finish_task(
                conn,
                root,
                task["id"],
                state,
                "quality review requires passing spec review",
                verifier_result="passed",
                next_action="configure spec reviewer before quality reviewer",
            )
            return True
        transition_task(conn, task["id"], "reviewing_quality", "running quality review")
        quality_review = run_quality_review(
            quality_reviewer,
            task,
            changed_files,
            patch_path,
            verification.log_path,
            repo,
            worktree,
            run_dir,
            timeout_seconds=config.policy.max_task_runtime_seconds,
        )
        add_artifact(conn, task["id"], "quality_review_log", quality_review.log_path)
        if not quality_review.passed:
            state = "failed" if quality_review.timed_out else _review_failure_state(repo)
            _finish_task(
                conn,
                root,
                task["id"],
                state,
                (
                    f"quality review timed out after "
                    f"{config.policy.max_task_runtime_seconds} seconds"
                    if quality_review.timed_out
                    else "quality review failed"
                ),
                verifier_result="passed",
                next_action=(
                    "inspect quality reviewer log and retry"
                    if quality_review.timed_out
                    else "address quality review feedback"
                ),
            )
            return True

    missing_evidence = _missing_completion_evidence(conn, task["id"], repo)
    if missing_evidence:
        if "verifier_log" in missing_evidence:
            state = "failed"
            next_action = "rerun verification and capture verifier evidence"
            verifier_result = "failed"
        else:
            state = _review_failure_state(repo)
            next_action = "provide required review evidence"
            verifier_result = "passed"
        _finish_task(
            conn,
            root,
            task["id"],
            state,
            f"missing completion evidence: {', '.join(missing_evidence)}",
            verifier_result=verifier_result,
            next_action=next_action,
        )
        return True

    transition_task(conn, task["id"], "committing", "creating commit")
    commit_all(
        worktree,
        f"{task['title']}\n\nTask: {task['id']}\nAgent: {agent.id}",
    )
    if repo.allow_push and repo.merge_policy != "no_push":
        transition_task(conn, task["id"], "pushing", "pushing branch")
        try:
            push_branch(worktree, repo.remote, branch)
        except (RuntimeError, OSError) as exc:
            _finish_task(
                conn,
                root,
                task["id"],
                "failed",
                f"push failed: {exc}",
                verifier_result="passed",
                next_action="inspect push failure and retry",
            )
            return True
        if repo.merge_policy == "auto_merge_default_branch":
            transition_task(conn, task["id"], "merging", "merging to default branch")
            try:
                merge_branch_to_default(repo.path, branch, repo.default_branch, repo.remote)
            except (RuntimeError, OSError) as exc:
                _finish_task(
                    conn,
                    root,
                    task["id"],
                    "failed",
                    f"merge failed: {exc}",
                    verifier_result="passed",
                    next_action="inspect merge failure and retry",
                )
                return True
    _finish_task(
        conn,
        root,
        task["id"],
        "done",
        "completed",
        verifier_result="passed",
        next_action="continue next task",
    )
    return True


def queue_follow_up_task(root: Path, task_draft) -> Path:
    from .tasks import write_generated_task

    return write_generated_task(root, task_draft)
