"""Project runtime adapter for multi-project Supervisor.

Runs one task cycle scoped to a single project: claims a project-scoped
ready task, executes the agent, and transitions the task state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .config import CoordinatorConfig
from .db import (
    claim_project_task,
    get_task,
    release_task_lease,
    transition_task,
)
from .reporting import NULL_REPORTER, Reporter


@dataclass(frozen=True)
class ProjectRuntime:
    """Immutable runtime context for a single project."""

    project_id: str
    repo_root: Path
    state_root: Path
    config: CoordinatorConfig

    @property
    def runs_dir(self) -> Path:
        return self.state_root / "projects" / self.project_id / "runs"


@dataclass(frozen=True)
class ProjectCycleResult:
    """Result of one project-scoped daemon cycle."""

    project_id: str
    task_id: str | None = None
    tasks_processed: int = 0
    failures: int = 0
    stop_reason: str | None = None


def run_project_cycle(
    conn: sqlite3.Connection,
    runtime: ProjectRuntime,
    reporter: Reporter = NULL_REPORTER,
) -> ProjectCycleResult:
    """Run one task cycle scoped to a single project.

    Claims a project-scoped ready task, executes the configured agent,
    and transitions the task. Returns the cycle result.
    """
    # Claim a task for this project only
    task = claim_project_task(
        conn,
        runtime.project_id,
        agent_id="supervisor",
    )

    if task is None:
        return ProjectCycleResult(
            project_id=runtime.project_id,
            stop_reason="no ready tasks",
        )

    task_id = task["id"]

    try:
        # Transition to running
        transition_task(conn, task_id, "running", f"claimed by supervisor for {runtime.project_id}")

        # Execute the agent (real or fake)
        agent_result = _execute_agent(conn, runtime, task, reporter)

        if agent_result["success"]:
            transition_task(conn, task_id, "done", "completed by supervisor")
            return ProjectCycleResult(
                project_id=runtime.project_id,
                task_id=task_id,
                tasks_processed=1,
            )
        else:
            transition_task(conn, task_id, "failed", agent_result.get("reason", "agent failed"))
            return ProjectCycleResult(
                project_id=runtime.project_id,
                task_id=task_id,
                tasks_processed=1,
                failures=1,
            )
    except Exception as exc:
        try:
            transition_task(conn, task_id, "failed", f"exception: {exc}")
        except Exception:
            pass
        return ProjectCycleResult(
            project_id=runtime.project_id,
            task_id=task_id,
            tasks_processed=1,
            failures=1,
            stop_reason=str(exc),
        )
    finally:
        release_task_lease(conn, task_id)


def _execute_agent(
    conn: sqlite3.Connection,
    runtime: ProjectRuntime,
    task: sqlite3.Row,
    reporter: Reporter,
) -> dict:
    """Execute the configured agent for a task.

    Returns {"success": True} on success, or {"success": False, "reason": ...} on failure.
    """
    from .agent import run_agent
    from .config import select_agent_by_role

    capabilities = [c for c in task["capabilities"].split(",") if c]
    agent = select_agent_by_role(runtime.config, "worker", capabilities)

    if agent is None:
        return {"success": False, "reason": f"no agent for capabilities: {capabilities}"}

    # Resolve repo path from task's repo field
    repo_config = runtime.config.repos.get(task["repo"])
    repo_root = repo_config.path if repo_config else runtime.repo_root

    # Set up run directory
    run_dir = runtime.runs_dir / task["id"]
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write prompt
    prompt_path = run_dir / "prompt.md"
    prompt_path.write_text(
        f"# Task: {task['title']}\n\n"
        f"Repo: {task['repo']}\n"
        f"Goal: {task['goal']}\n"
        f"Acceptance: {task['acceptance_criteria']}\n",
    )

    # Run agent in the repo directory
    result = run_agent(
        agent,
        prompt_path,
        repo_root,
        run_dir,
        timeout_seconds=runtime.config.policy.max_task_runtime_seconds,
        reporter=reporter,
        task_id=task["id"],
    )

    if result.exit_code != 0:
        return {"success": False, "reason": f"exit_code={result.exit_code}"}
    if result.timed_out:
        return {"success": False, "reason": "timeout"}

    return {"success": True}


# Allow tests to override the agent executor
_execute_agent_fn = _execute_agent


def set_agent_executor(fn) -> None:
    """Override the agent executor for testing."""
    global _execute_agent_fn
    _execute_agent_fn = fn


def reset_agent_executor() -> None:
    """Reset to the default agent executor."""
    global _execute_agent_fn
    _execute_agent_fn = _execute_agent
