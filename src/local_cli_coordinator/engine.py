from pathlib import Path
import re
import sqlite3

from .agent import run_agent
from .config import CoordinatorConfig
from .db import (
    add_artifact,
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
from .verify import run_verification
from .memory import LoopMemoryEntry, append_loop_memory


def _slug(text: str) -> str:
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned[:40] or "task"


def _select_agent(config: CoordinatorConfig, capabilities: list[str]):
    if not config.agents:
        return None
    if not capabilities:
        return None
    required = set(capabilities)
    for agent in config.agents.values():
        if required.issubset(set(agent.capabilities)):
            return agent
    return None


def _write_prompt(task, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = run_dir / "prompt.md"
    prompt.write_text(
        f"# Task: {task['title']}\n\n"
        f"Repo: {task['repo']}\n\n"
        f"## Goal\n\n{task['goal']}\n\n"
        f"## Acceptance Criteria\n\n{task['acceptance_criteria']}\n"
    )
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


def run_one_ready_task(conn: sqlite3.Connection, config: CoordinatorConfig, root: Path) -> bool:
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
    prompt = _write_prompt(task, run_dir)
    agent_result = run_agent(agent, prompt, worktree, run_dir)
    add_artifact(conn, task["id"], "agent_log", agent_result.log_path)
    if agent_result.exit_code != 0:
        _finish_task(
            conn,
            root,
            task["id"],
            "failed",
            "agent command failed",
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
    verification = run_verification(commands, worktree, run_dir)
    add_artifact(conn, task["id"], "verifier_log", verification.log_path)
    if not verification.passed:
        _finish_task(
            conn,
            root,
            task["id"],
            "failed",
            "verification failed",
            verifier_result="failed",
            next_action="inspect verifier log and retry",
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
