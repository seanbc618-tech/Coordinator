import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

from .agent import run_agent
from .config import CoordinatorConfig, RepoConfig, select_agent_by_role
from .db import (
    add_artifact,
    artifact_kinds,
    circuit_breaker_reason,
    claim_next_ready_task,
    create_task,
    get_task,
    next_ready_task,
    release_task_lease,
    set_task_branch_and_worktree,
    transition_task,
)
from .discovery import list_findings
from .planner import plan_finding
from .policy import check_changed_files, check_task_draft
from .tasks import parse_task_markdown, scan_inbox, write_generated_task
from .gitops import (
    collect_changed_files,
    commit_all,
    create_worktree,
    diff_patch,
    merge_branch_to_default,
    push_branch,
)
from .review import run_quality_review, run_spec_review
from .verify import run_verification
from .memory import LoopMemoryEntry, append_loop_memory, loop_memory_path
from .review_inbox import write_review_packet


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


def _write_review_packet_if_needed(
    conn: sqlite3.Connection,
    root: Path,
    task: dict,
    state: str,
    *,
    changed_files: list[str] | None = None,
    verifier_result: str = "",
    spec_review_result: str = "",
    quality_review_result: str = "",
    suggested_action: str = "review and approve or reject",
) -> None:
    """Write a review packet and link it as an artifact when awaiting human."""
    if state != "awaiting_human":
        return
    packet_path = write_review_packet(
        root,
        task,
        changed_files=changed_files,
        verifier_result=verifier_result,
        spec_review_result=spec_review_result,
        quality_review_result=quality_review_result,
        suggested_action=suggested_action,
    )
    add_artifact(conn, task["id"], "review_packet", packet_path)


def _missing_completion_evidence(
    conn: sqlite3.Connection,
    task_id: str,
    repo: RepoConfig,
) -> list[str]:
    required = {"verifier_log"}
    if repo.review_policy != "tests_only":
        required.update({"spec_review_log", "quality_review_log"})
    return sorted(required - artifact_kinds(conn, task_id))


PLANNED_FINDINGS_RELATIVE_PATH = Path("state") / "discovery" / "planned_findings.json"


@dataclass(frozen=True)
class DaemonCycleResult:
    imported_tasks: int
    planned_tasks: int
    tasks_processed: int
    failures: int
    blocked: int
    skipped: int
    stop_reason: str | None


@dataclass(frozen=True)
class ContinuousDaemonResult:
    message: str
    tasks_processed: int
    failures: int
    stop_reason: str | None


def _planned_findings_path(root: Path) -> Path:
    return root / PLANNED_FINDINGS_RELATIVE_PATH


def _load_planned_finding_ids(root: Path) -> set[str]:
    path = _planned_findings_path(root)
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("planned findings state must be a JSON array")
    return {str(item) for item in payload}


def _mark_finding_planned(root: Path, finding_id: str, planned_ids: set[str]) -> None:
    planned_ids.add(finding_id)
    path = _planned_findings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(planned_ids), indent=2) + "\n", encoding="utf-8")


def _move_to_accepted(root: Path, source_path: str) -> None:
    source = root / source_path
    accepted = root / "tasks" / "accepted" / source.name
    accepted.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(accepted))


def _scan_markdown_tasks(root: Path, relative_dir: str) -> list:
    directory = root / relative_dir
    if not directory.is_dir():
        return []
    drafts = []
    for path in sorted(directory.glob("*.md")):
        drafts.append(parse_task_markdown(path.read_text(encoding="utf-8"), str(path.relative_to(root))))
    return drafts


def _import_task_draft(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    draft,
) -> bool:
    reasons = list(check_task_draft(draft, config.policy).reasons)
    if draft.repo not in config.repos:
        reasons.append(f"repo is not allowlisted: {draft.repo}")
    if reasons:
        return False
    create_task(
        conn,
        title=draft.title,
        repo=draft.repo,
        source_path=draft.source_path,
        priority=draft.priority,
        capabilities=draft.capabilities,
        goal=draft.goal,
        acceptance_criteria=draft.acceptance_criteria,
        verification_commands=draft.verification_commands,
    )
    _move_to_accepted(root, draft.source_path)
    return True


def _import_discovered_tasks(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
) -> int:
    imported = 0
    for draft in scan_inbox(root):
        if _import_task_draft(conn, config, root, draft):
            imported += 1
    for draft in _scan_markdown_tasks(root, "tasks/generated"):
        if _import_task_draft(conn, config, root, draft):
            imported += 1
    return imported


def _plan_persisted_findings(root: Path) -> int:
    planned_ids = _load_planned_finding_ids(root)
    planned_count = 0
    for finding in list_findings(root):
        if finding.id in planned_ids:
            continue
        result = plan_finding(finding)
        if result.needs_split:
            continue
        for task in result.tasks:
            write_generated_task(root, task)
            planned_count += 1
        _mark_finding_planned(root, finding.id, planned_ids)
    return planned_count


def run_discovery_phase(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
) -> tuple[int, int]:
    if not config.daemon_policy.run_discovery_before_tasks:
        return 0, 0
    imported = _import_discovered_tasks(conn, config, root)
    planned = _plan_persisted_findings(root)
    if planned:
        imported += _import_discovered_tasks(conn, config, root)
    return imported, planned


def run_daemon_cycle(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
) -> DaemonCycleResult:
    stop_reason = circuit_breaker_reason(conn, config.policy)
    if stop_reason is not None:
        return DaemonCycleResult(0, 0, 0, 0, 0, 0, stop_reason)

    imported, planned = run_discovery_phase(conn, config, root)
    tasks_processed = 0
    failures = 0
    blocked = 0
    skipped = 0
    limit = max(1, config.policy.max_tasks_per_run)

    for _ in range(limit):
        stop_reason = circuit_breaker_reason(conn, config.policy)
        if stop_reason is not None:
            return DaemonCycleResult(
                imported,
                planned,
                tasks_processed,
                failures,
                blocked,
                skipped,
                stop_reason,
            )

        candidate = next_ready_task(conn)
        if candidate is None:
            break

        if candidate["state"] == "blocked":
            blocked += 1
            skipped += 1
            continue

        processed = run_one_ready_task(conn, config, root)
        if not processed:
            skipped += 1
            break

        tasks_processed += 1
        if get_task(conn, candidate["id"])["state"] == "failed":
            failures += 1

    stop_reason = None if tasks_processed else "no ready tasks"
    return DaemonCycleResult(
        imported,
        planned,
        tasks_processed,
        failures,
        blocked,
        skipped,
        stop_reason,
    )


def run_continuous_daemon(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    *,
    sleep_fn=time.sleep,
    monotonic_fn=time.monotonic,
) -> ContinuousDaemonResult:
    started_at = monotonic_fn()
    total_processed = 0
    total_failures = 0
    stop_reason = None
    while True:
        elapsed = monotonic_fn() - started_at
        if elapsed >= config.policy.max_daemon_runtime_seconds:
            stop_reason = "max daemon runtime reached"
            break

        stop_reason = circuit_breaker_reason(conn, config.policy)
        if stop_reason is not None:
            break

        result = run_daemon_cycle(conn, config, root)
        total_processed += result.tasks_processed
        total_failures += result.failures
        if result.stop_reason in {"no ready tasks"} and result.tasks_processed == 0:
            sleep_fn(config.daemon_policy.idle_sleep_seconds)
            continue
        if result.tasks_processed > 0:
            sleep_fn(config.daemon_policy.loop_interval_seconds)
            continue
        if result.stop_reason and result.stop_reason != "no ready tasks":
            stop_reason = result.stop_reason
            break
        sleep_fn(config.daemon_policy.idle_sleep_seconds)

    if stop_reason == "max daemon runtime reached":
        message = f"stopped: {stop_reason}"
    elif stop_reason:
        message = f"stopped: {stop_reason}"
    elif total_processed == 0:
        message = "no ready tasks"
        stop_reason = "no ready tasks"
    elif total_processed == 1:
        message = "processed 1 task"
    else:
        message = f"processed {total_processed} tasks"
    return ContinuousDaemonResult(
        message=message,
        tasks_processed=total_processed,
        failures=total_failures,
        stop_reason=stop_reason,
    )


def run_one_ready_task(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    agent_id: str | None = None,
) -> bool:
    if circuit_breaker_reason(conn, config.policy) is not None:
        return False
    if agent_id is not None:
        task = claim_next_ready_task(conn, agent_id)
    else:
        task = next_ready_task(conn)
    if task is None:
        return False
    try:
        return _process_task(conn, config, root, task, agent_id)
    finally:
        if agent_id is not None:
            release_task_lease(conn, task["id"])


def _process_task(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    task: dict,
    agent_id: str | None,
) -> bool:
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
            _write_review_packet_if_needed(
                conn, root, task, state,
                changed_files=changed_files,
                verifier_result="passed",
                spec_review_result="failed",
                suggested_action="address spec review feedback",
            )
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
            _write_review_packet_if_needed(
                conn, root, task, state,
                changed_files=changed_files,
                verifier_result="passed",
                spec_review_result="passed",
                quality_review_result="failed",
                suggested_action="address quality review feedback",
            )
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
        _write_review_packet_if_needed(
            conn, root, task, state,
            changed_files=changed_files,
            verifier_result=verifier_result,
            suggested_action=f"provide missing evidence: {', '.join(missing_evidence)}",
        )
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
