import json
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
import re
import sqlite3

from .agent import run_agent
from .agent_result import AgentResultClass, classify_agent_output
from .config import CoordinatorConfig, RepoConfig, select_agent_by_role, select_fallback_agent
from .fallback import FallbackDecision, decide_fallback
from .reporting import NULL_REPORTER, ExecutionEvent, Reporter
from .db import (
    _try_acquire_task_lease,
    active_lease_count,
    add_artifact,
    artifact_kinds,
    circuit_breaker_reason,
    create_task,
    fallback_count_for_task,
    finish_attempt,
    get_task,
    release_task_lease,
    set_task_branch_and_worktree,
    start_attempt,
    transition_task,
)
from .discovery import list_findings, run_configured_discovery
from .planner import plan_finding
from .policy import (
    allows_auto_merge,
    check_changed_files,
    check_task_draft,
    should_require_human_review,
)
from .tasks import parse_task_markdown, scan_inbox, write_generated_task
from .gitops import (
    collect_changed_files,
    collect_changed_files_since,
    commit_all,
    create_worktree,
    diff_patch,
    merge_branch_to_default,
    merge_base,
    push_branch,
)
from .review import run_quality_review, run_spec_review
from .execution_policy import (
    ExecutionPolicyError,
    check_policy_stage,
    format_policy_prompt_section,
    policy_is_restrictive,
    ExecutionPolicy,
)
from .verify import run_verification
from .reporting import NULL_REPORTER, Reporter
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


def _is_report_only_task(task: dict) -> bool:
    capabilities = {
        part.strip()
        for part in str(task.get("capabilities", "")).split(",")
        if part.strip()
    }
    text = " ".join([
        str(task.get("title", "")),
        str(task.get("goal", "")),
        str(task.get("acceptance_criteria", "")),
    ]).lower()
    edit_caps = {"code", "implementation", "frontend", "backend"}
    markers = (
        "report",
        "baseline",
        "acceptance checks",
        "without changing code",
        "read-only",
    )
    return (
        "tests" in capabilities
        and not (capabilities & edit_caps)
        and any(marker in text for marker in markers)
    )


def _git_info_exclude_path(worktree: Path) -> Path | None:
    git_path = worktree / ".git"
    if git_path.is_file():
        text = git_path.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir: "):
            gitdir = Path(text.split("gitdir: ", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (worktree / gitdir).resolve()
            return gitdir / "info" / "exclude"
        return None
    if git_path.is_dir():
        return git_path / "info" / "exclude"
    return None


def _ensure_coordinator_git_exclude(worktree: Path) -> None:
    exclude_path = _git_info_exclude_path(worktree)
    if exclude_path is None:
        return
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    marker = "# Coordinator runtime prompts\n.coordinator/\n"
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    if ".coordinator/" not in existing:
        with exclude_path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(marker)


def _worktree_prompt_path(worktree: Path, task_id: str, prompt: Path) -> Path:
    worktree_prompt = worktree / ".coordinator" / task_id / "prompt.md"
    worktree_prompt.parent.mkdir(parents=True, exist_ok=True)
    worktree_prompt.write_text(prompt.read_text(encoding="utf-8"), encoding="utf-8")
    _ensure_coordinator_git_exclude(worktree)
    return worktree_prompt


def _resolve_memory_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _write_prompt(task, run_dir: Path, root: Path, repo: RepoConfig) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    verification_commands = [
        line for line in task["verification_commands"].splitlines() if line
    ] or repo.verify_commands
    sections = [
        f"# Task: {task['title']}\n",
        f"Repo: {task['repo']}\n",
        f"## Goal\n\n{task['goal']}\n",
        f"## Acceptance Criteria\n\n{task['acceptance_criteria']}\n",
        "## Required Verification\n\n"
        "You must execute every command exactly as written before returning. "
        "If a referenced path does not exist, adjust the implementation or test filename. "
        "Do not substitute a different command.\n\n"
        + "\n".join(f"- `{command}`" for command in verification_commands)
        + "\n",
    ]
    loop_memory = _read_optional_text(loop_memory_path(root))
    if loop_memory is not None:
        sections.append(f"## Loop Memory\n\n{loop_memory}\n")
    if repo.memory_path is not None:
        repo_memory = _read_optional_text(_resolve_memory_path(root, repo.memory_path))
        if repo_memory is not None:
            sections.append(f"## Repo Memory\n\n{repo_memory}\n")
    policy_json = str(task.get("execution_policy") or "{}")
    if policy_is_restrictive(policy_json):
        policy = ExecutionPolicy.from_json(policy_json)
        sections.append(format_policy_prompt_section(policy) + "\n")
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


def _reviewer_outcomes_for_policy(
    repo: RepoConfig,
    *,
    spec_review_passed: bool,
    quality_review_passed: bool,
) -> tuple[bool, bool]:
    if repo.review_policy == "tests_only":
        return True, True
    return spec_review_passed, quality_review_passed


def _pause_for_human_review_before_merge(
    conn: sqlite3.Connection,
    root: Path,
    task: dict,
    repo: RepoConfig,
    config: CoordinatorConfig,
    *,
    changed_files: list[str],
    spec_review_passed: bool,
    quality_review_passed: bool,
) -> bool:
    spec_ok, quality_ok = _reviewer_outcomes_for_policy(
        repo,
        spec_review_passed=spec_review_passed,
        quality_review_passed=quality_review_passed,
    )
    if allows_auto_merge(
        repo,
        changed_files=changed_files,
        max_files_touched=config.policy.max_files_touched,
        spec_review_passed=spec_ok,
        quality_review_passed=quality_ok,
    ):
        return False

    _, reasons = should_require_human_review(
        repo,
        changed_files=changed_files,
        max_files_touched=config.policy.max_files_touched,
        spec_review_passed=spec_ok,
        quality_review_passed=quality_ok,
    )
    note = f"human review required: {'; '.join(reasons)}"
    _write_review_packet_if_needed(
        conn,
        root,
        task,
        "awaiting_human",
        changed_files=changed_files,
        verifier_result="passed",
        spec_review_result="passed" if spec_ok else "not run",
        quality_review_result="passed" if quality_ok else "not run",
        suggested_action="review packet in tasks/review and approve merge",
    )
    _finish_task(
        conn,
        root,
        task["id"],
        "awaiting_human",
        note,
        verifier_result="passed",
        next_action="review packet in tasks/review and merge manually",
    )
    return True


def _fallback_claim_agent_id(config: CoordinatorConfig) -> str:
    if not config.agents:
        return "daemon"
    worker = select_agent_by_role(config, "worker")
    if worker is not None:
        return worker.id
    return next(iter(config.agents.values())).id


def _claim_next_ready_task(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    *,
    forced_agent_id: str | None = None,
) -> tuple[dict | None, str | None]:
    if not config.agents:
        max_global = 1
    else:
        max_global = sum(item.max_concurrency for item in config.agents.values())

    conn.execute("begin immediate")
    try:
        if active_lease_count(conn) >= max_global:
            conn.commit()
            return None, None

        candidates = conn.execute(
            "select * from tasks where state = 'ready' order by created_at, id"
        ).fetchall()

        for task in candidates:
            capabilities = [part for part in task["capabilities"].split(",") if part]
            if forced_agent_id is not None:
                agent = config.agents.get(forced_agent_id)
                if agent is None:
                    conn.commit()
                    return None, None
                if capabilities and not set(capabilities).issubset(set(agent.capabilities)):
                    continue
            else:
                agent = _select_agent(config, capabilities)
            if agent is None:
                continue
            if active_lease_count(conn, agent.id) >= agent.max_concurrency:
                continue
            try:
                if _try_acquire_task_lease(conn, task["id"], agent.id):
                    conn.commit()
                    return dict(task), agent.id
            except sqlite3.IntegrityError:
                continue

        if forced_agent_id is not None:
            conn.commit()
            return None, None

        fallback_id = _fallback_claim_agent_id(config)
        for task in candidates:
            capabilities = [part for part in task["capabilities"].split(",") if part]
            if _select_agent(config, capabilities) is not None:
                continue
            if active_lease_count(conn) >= max_global:
                break
            try:
                if _try_acquire_task_lease(conn, task["id"], fallback_id):
                    conn.commit()
                    return dict(task), fallback_id
            except sqlite3.IntegrityError:
                continue

        conn.commit()
        return None, None
    except sqlite3.Error:
        conn.rollback()
        raise


def _task_execution_policy(task: dict) -> str:
    return str(task.get("execution_policy") or "{}")


def _policy_allows(
    policy_json: str,
    stage: str,
    *,
    has_changes: bool = False,
) -> bool:
    try:
        check_policy_stage(policy_json, stage, has_changes=has_changes)
        return True
    except ExecutionPolicyError:
        return False


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
        dict(get_task(conn, task["id"])),
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
    commander_tasks_admitted: int = 0
    commander_status: str | None = None


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
    repo = config.repos.get(draft.repo)
    if (
        config.policy.require_verification_commands
        and not draft.verification_commands
        and repo is not None
    ):
        draft = replace(
            draft,
            verification_commands=list(repo.verify_commands),
        )
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
    *,
    reporter: Reporter = NULL_REPORTER,
) -> tuple[int, int]:
    if not config.daemon_policy.run_discovery_before_tasks:
        return 0, 0
    run_configured_discovery(config, root, reporter=reporter)
    imported = _import_discovered_tasks(conn, config, root)
    planned = _plan_persisted_findings(root)
    if planned:
        imported += _import_discovered_tasks(conn, config, root)
    return imported, planned


def run_daemon_cycle(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    *,
    reporter: Reporter = NULL_REPORTER,
) -> DaemonCycleResult:
    stop_reason = circuit_breaker_reason(conn, config.policy)
    if stop_reason is not None:
        return DaemonCycleResult(0, 0, 0, 0, 0, 0, stop_reason)

    reporter.emit(ExecutionEvent(kind="cycle_started", stage="engine"))
    imported, planned = run_discovery_phase(conn, config, root, reporter=reporter)

    # Replenish goal queue if needed
    commander_admitted = 0
    commander_status = None
    try:
        from .commander_service import maybe_replenish_goal
        replenishment = maybe_replenish_goal(conn, config, root, reporter=reporter)
        commander_status = replenishment.status
        commander_admitted = len(replenishment.admitted_task_ids)
    except Exception as exc:
        commander_status = f"replenishment_error:{type(exc).__name__}:{exc}"

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
                commander_admitted,
                commander_status,
            )

        task, agent_id = _claim_next_ready_task(conn, config)
        if task is None or agent_id is None:
            break

        if task["state"] == "blocked":
            release_task_lease(conn, task["id"])
            blocked += 1
            skipped += 1
            continue

        try:
            processed = _process_task(conn, config, root, task, agent_id, reporter=reporter)
        finally:
            release_task_lease(conn, task["id"])

        if not processed:
            skipped += 1
            break

        tasks_processed += 1
        if get_task(conn, task["id"])["state"] == "failed":
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
        commander_admitted,
        commander_status,
    )


def run_continuous_daemon(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    *,
    sleep_fn=time.sleep,
    monotonic_fn=time.monotonic,
    reporter: Reporter = NULL_REPORTER,
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

        result = run_daemon_cycle(conn, config, root, reporter=reporter)
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
    *,
    reporter: Reporter = NULL_REPORTER,
) -> bool:
    if circuit_breaker_reason(conn, config.policy) is not None:
        return False
    task, claim_agent_id = _claim_next_ready_task(
        conn,
        config,
        forced_agent_id=agent_id,
    )
    if task is None or claim_agent_id is None:
        return False
    try:
        return _process_task(conn, config, root, task, claim_agent_id, reporter=reporter)
    finally:
        release_task_lease(conn, task["id"])


def run_worker_attempt(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    task_id: str,
    agent,
    prompt: Path,
    worktree: Path,
    run_dir: Path,
    *,
    fallback_from_attempt_id: int | None = None,
    reporter: Reporter = NULL_REPORTER,
):
    """Run one worker attempt with full attempt lifecycle tracking.

    Returns (agent_result, classified_result, attempt_id).
    """
    from .db import list_attempts as _list_attempts
    all_attempts = _list_attempts(conn, task_id)
    attempt_number = len(all_attempts) + 1
    attempt_dir = run_dir / f"attempt-{attempt_number}"
    attempt_dir.mkdir(parents=True, exist_ok=True)

    attempt_id = start_attempt(
        conn, task_id, agent.id, agent.command,
        fallback_from_attempt_id=fallback_from_attempt_id,
    )

    try:
        agent_result = run_agent(
            agent,
            prompt,
            worktree,
            attempt_dir,
            timeout_seconds=config.policy.max_task_runtime_seconds,
            reporter=reporter,
            task_id=task_id,
        )
        classified = classify_agent_output(
            agent_result.log_path.read_text() if agent_result.log_path.exists() else "",
            exit_code=agent_result.exit_code,
            timed_out=agent_result.timed_out,
        )
        finish_attempt(
            conn,
            attempt_id,
            exit_code=agent_result.exit_code,
            result_class=classified.classification.value,
            result_reason=classified.reason,
            log_path=str(agent_result.log_path),
        )
        # Add attempt log as artifact
        add_artifact(conn, task_id, "attempt_log", agent_result.log_path)
        # Keep compatibility pointer
        add_artifact(conn, task_id, "agent_log", agent_result.log_path)
        return agent_result, classified, attempt_id
    except Exception:
        finish_attempt(
            conn, attempt_id, exit_code=127, result_class="command_failed",
            result_reason="exception",
        )
        raise


def _process_task(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    root: Path,
    task: dict,
    agent_id: str | None,
    *,
    reporter: Reporter = NULL_REPORTER,
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
    if not capabilities:
        agent = None
    elif agent_id is not None:
        agent = config.agents.get(agent_id)
        if agent is None:
            _finish_task(
                conn,
                root,
                task["id"],
                "blocked",
                f"assigned agent is not configured: {agent_id}",
                verifier_result="not run",
                next_action="configure the assigned agent",
            )
            return True
        if not set(capabilities).issubset(set(agent.capabilities)):
            capability_text = ", ".join(capabilities)
            _finish_task(
                conn,
                root,
                task["id"],
                "blocked",
                f"assigned agent {agent_id} lacks capabilities: {capability_text}",
                verifier_result="not run",
                next_action="assign a capable agent or split the task",
            )
            return True
    else:
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
    reporter.emit(ExecutionEvent(
        kind="task_started",
        stage="engine",
        task_id=task["id"],
        actor=agent.id,
    ))
    try:
        registered_worktree = Path(task["worktree_path"]).resolve() if task["worktree_path"] else None
        if registered_worktree is not None and registered_worktree.is_dir():
            worktree = registered_worktree
        else:
            worktree = create_worktree(
                repo_path=repo.path,
                worktrees_root=root / "worktrees" / repo.id,
                task_id=task["id"],
                branch_name=branch,
                reporter=reporter,
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
    try:
        base_commit = merge_base(worktree, repo.default_branch)
    except (RuntimeError, OSError) as exc:
        _finish_task(
            conn,
            root,
            task["id"],
            "failed",
            f"merge-base lookup failed: {exc}",
            verifier_result="not run",
            next_action="inspect worktree branch history and retry",
        )
        return True
    prompt = _write_prompt(task, run_dir, root, repo)
    worktree_prompt = _worktree_prompt_path(worktree, task["id"], prompt)
    add_artifact(conn, task["id"], "worktree_prompt", worktree_prompt)
    agent_result, classified, attempt_id = run_worker_attempt(
        conn, config, task["id"], agent, worktree_prompt, worktree, run_dir,
        reporter=reporter,
    )

    # Check for interactive block and attempt fallback
    if classified.classification == AgentResultClass.INTERACTIVE_BLOCKED:
        fallback_agent = select_fallback_agent(config, agent.id, capabilities)
        fallback_decision = decide_fallback(
            conn,
            task["id"],
            classified,
            fallback_agent_id=fallback_agent.id if fallback_agent else None,
            worktree=worktree,
        )
        if fallback_decision == FallbackDecision.RUN and fallback_agent is not None:
            reporter.emit(ExecutionEvent(
                kind="fallback_started",
                stage="engine",
                task_id=task["id"],
                actor=fallback_agent.id,
            ))
            agent_result, classified, attempt_id = run_worker_attempt(
                conn, config, task["id"], fallback_agent, worktree_prompt, worktree, run_dir,
                fallback_from_attempt_id=attempt_id,
                reporter=reporter,
            )
        elif fallback_decision == FallbackDecision.HUMAN_REVIEW:
            _finish_task(
                conn,
                root,
                task["id"],
                "awaiting_human",
                "agent blocked, no eligible fallback",
                verifier_result="not run",
                next_action="operator must manually approve or reassign",
            )
            return True

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

    policy_json = _task_execution_policy(task)
    changed_files = collect_changed_files_since(worktree, base_commit)
    if changed_files:
        try:
            check_policy_stage(policy_json, "edit", has_changes=True)
        except ExecutionPolicyError as exc:
            _finish_task(
                conn,
                root,
                task["id"],
                "failed",
                str(exc),
                verifier_result="not run",
                next_action="respect execution policy or update task restrictions",
            )
            return True
    if not changed_files and _is_report_only_task(task):
        commands = [
            line for line in task["verification_commands"].splitlines() if line
        ] or repo.verify_commands
        if commands and not _policy_allows(policy_json, "test"):
            _finish_task(
                conn,
                root,
                task["id"],
                "failed",
                "execution policy forbids test",
                verifier_result="not run",
                next_action="allow test stage or remove verification commands",
            )
            return True
        transition_task(conn, task["id"], "verifying", "running report-only verification")
        verification = run_verification(
            commands,
            worktree,
            run_dir,
            timeout_seconds=config.policy.max_task_runtime_seconds,
            reporter=reporter,
            task_id=task["id"],
        )
        add_artifact(conn, task["id"], "verifier_log", verification.log_path)
        if verification.passed:
            _finish_task(
                conn,
                root,
                task["id"],
                "done",
                "report-only verification passed",
                verifier_result="passed",
                next_action="none",
            )
        else:
            summary = (
                f"verification timed out after {config.policy.max_task_runtime_seconds} seconds"
                if verification.timed_out
                else "report-only verification failed"
            )
            _finish_task(
                conn,
                root,
                task["id"],
                "failed",
                summary,
                verifier_result="timed out" if verification.timed_out else "failed",
                next_action="inspect verification log",
            )
        return True
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
    patch_path.write_text(diff_patch(worktree, base_commit))
    add_artifact(conn, task["id"], "diff", patch_path)

    commands = [line for line in task["verification_commands"].splitlines() if line] or repo.verify_commands
    verification_passed = True
    verification_timed_out = False
    verifier_log_path: Path | None = None
    if commands and _policy_allows(policy_json, "test"):
        transition_task(conn, task["id"], "verifying", "running verification")
        verification = run_verification(
            commands,
            worktree,
            run_dir,
            timeout_seconds=config.policy.max_task_runtime_seconds,
            reporter=reporter,
            task_id=task["id"],
        )
        verifier_log_path = verification.log_path
        add_artifact(conn, task["id"], "verifier_log", verification.log_path)
        verification_passed = verification.passed
        verification_timed_out = verification.timed_out
        if not verification_passed:
            _finish_task(
                conn,
                root,
                task["id"],
                "failed",
                (
                    f"verification timed out after "
                    f"{config.policy.max_task_runtime_seconds} seconds"
                    if verification_timed_out
                    else "verification failed"
                ),
                verifier_result="timed out" if verification_timed_out else "failed",
                next_action="inspect verifier log and retry",
            )
            return True
    elif commands:
        skip_log = run_dir / "verifier-skipped.log"
        skip_log.write_text(
            "verification skipped: execution policy forbids test\n",
            encoding="utf-8",
        )
        verifier_log_path = skip_log
        add_artifact(conn, task["id"], "verifier_log", skip_log)

    spec_review_passed = False
    quality_review_passed = False
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
            reporter=reporter,
            task_id=task["id"],
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
            verifier_log_path or (run_dir / "verifier-skipped.log"),
            repo,
            worktree,
            run_dir,
            timeout_seconds=config.policy.max_task_runtime_seconds,
            reporter=reporter,
            task_id=task["id"],
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
        quality_review_passed = True

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

    if not _policy_allows(policy_json, "commit"):
        _write_review_packet_if_needed(
            conn,
            root,
            task,
            "awaiting_human",
            changed_files=changed_files,
            verifier_result="passed",
            spec_review_result="passed" if spec_review_passed else "not run",
            quality_review_result="passed" if quality_review_passed else "not run",
            suggested_action="commit manually from preserved worktree",
        )
        _finish_task(
            conn,
            root,
            task["id"],
            "awaiting_human",
            "execution policy forbids commit",
            verifier_result="passed",
            next_action="commit manually from preserved worktree",
        )
        return True

    uncommitted_files = collect_changed_files(worktree)
    if uncommitted_files:
        transition_task(conn, task["id"], "committing", "creating commit")
        commit_all(
            worktree,
            f"{task['title']}\n\nTask: {task['id']}\nAgent: {agent.id}",
            reporter=reporter,
            task_id=task["id"],
        )
    else:
        transition_task(conn, task["id"], "committing", "using agent-created commit")
    if (
        repo.allow_push
        and repo.merge_policy != "no_push"
        and _policy_allows(policy_json, "push")
    ):
        transition_task(conn, task["id"], "pushing", "pushing branch")
        try:
            push_branch(
                worktree,
                repo.remote,
                branch,
                reporter=reporter,
                task_id=task["id"],
            )
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
        if (
            repo.merge_policy == "auto_merge_default_branch"
            and _policy_allows(policy_json, "merge")
        ):
            if _pause_for_human_review_before_merge(
                conn,
                root,
                task,
                repo,
                config,
                changed_files=changed_files,
                spec_review_passed=spec_review_passed,
                quality_review_passed=quality_review_passed,
            ):
                return True
            transition_task(conn, task["id"], "merging", "merging to default branch")
            try:
                merge_branch_to_default(
                    repo.path,
                    branch,
                    repo.default_branch,
                    repo.remote,
                    reporter=reporter,
                    task_id=task["id"],
                )
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
