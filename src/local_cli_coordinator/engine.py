from pathlib import Path
import re
import sqlite3

from .agent import run_agent
from .config import CoordinatorConfig
from .db import add_artifact, next_ready_task, set_task_branch_and_worktree, transition_task
from .gitops import collect_changed_files, commit_all, create_worktree, diff_patch
from .policy import check_changed_files
from .verify import run_verification


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


def run_one_ready_task(conn: sqlite3.Connection, config: CoordinatorConfig, root: Path) -> bool:
    task = next_ready_task(conn)
    if task is None:
        return False
    repo = config.repos.get(task["repo"])
    if repo is None:
        transition_task(
            conn,
            task["id"],
            "blocked",
            f"repo is not configured: {task['repo']}",
        )
        return True
    capabilities = [part for part in task["capabilities"].split(",") if part]
    if not config.agents:
        transition_task(conn, task["id"], "blocked", "no configured agents")
        return True
    agent = _select_agent(config, capabilities)
    if agent is None:
        capability_text = ", ".join(capabilities) if capabilities else "(none)"
        transition_task(
            conn,
            task["id"],
            "blocked",
            f"no matching agent for capabilities: {capability_text}",
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
        transition_task(conn, task["id"], "failed", f"worktree creation failed: {exc}")
        return True
    set_task_branch_and_worktree(conn, task["id"], branch, worktree)
    prompt = _write_prompt(task, run_dir)
    agent_result = run_agent(agent, prompt, worktree, run_dir)
    add_artifact(conn, task["id"], "agent_log", agent_result.log_path)
    if agent_result.exit_code != 0:
        transition_task(conn, task["id"], "failed", "agent command failed")
        return True

    changed_files = collect_changed_files(worktree)
    if not changed_files:
        transition_task(conn, task["id"], "failed", "no changed files")
        return True
    policy_result = check_changed_files(changed_files, config.policy)
    if not policy_result.accepted:
        transition_task(conn, task["id"], "needs_split", "; ".join(policy_result.reasons))
        return True

    patch_path = run_dir / "diff.patch"
    patch_path.write_text(diff_patch(worktree))
    add_artifact(conn, task["id"], "diff", patch_path)

    transition_task(conn, task["id"], "verifying", "running verification")
    commands = [line for line in task["verification_commands"].splitlines() if line] or repo.verify_commands
    verification = run_verification(commands, worktree, run_dir)
    add_artifact(conn, task["id"], "verifier_log", verification.log_path)
    if not verification.passed:
        transition_task(conn, task["id"], "failed", "verification failed")
        return True

    transition_task(conn, task["id"], "committing", "creating commit")
    commit_all(
        worktree,
        f"{task['title']}\n\nTask: {task['id']}\nAgent: {agent.id}",
    )
    transition_task(conn, task["id"], "done", "committed locally")
    return True
