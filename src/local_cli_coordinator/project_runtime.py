"""Project runtime adapter for multi-project Supervisor.

Wraps the existing engine's _process_task with project-scoped task
claiming. Preserves the full pipeline: worktree, agent, verification,
review, commit/push, fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from .config import AgentConfig, CoordinatorConfig
from .db import (
    claim_project_ready_task,
    get_task,
    peek_project_claim,
    project_has_claimable_task,
    release_task_lease,
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


def select_available_agent(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    project_id: str,
) -> tuple[AgentConfig | None, sqlite3.Row | None]:
    """Return the first task/agent pair that can be claimed (peek only)."""
    task, agent_id = peek_project_claim(conn, project_id, config)
    if task is None or agent_id is None:
        return None, None
    agent = config.agents.get(agent_id)
    if agent is None:
        return None, task
    return agent, task


def _register_cycle_summary(
    conn: sqlite3.Connection,
    runtime: ProjectRuntime,
    *,
    task_id: str,
) -> None:
    from .artifact_registry import (
        ArtifactRegistryError,
        register_artifact,
        resolve_warehouse_paths,
    )

    paths = resolve_warehouse_paths()
    if paths is None:
        return
    summary_path = runtime.runs_dir / f"{task_id}-cycle.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "project_id": runtime.project_id,
                "task_id": task_id,
                "repo_root": str(runtime.repo_root),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        register_artifact(
            conn,
            paths=paths,
            project_id=runtime.project_id,
            artifact_type="summary",
            path=summary_path,
            task_id=task_id,
            provenance={"source": "project_runtime"},
            commit=True,
        )
    except ArtifactRegistryError:
        pass


def project_is_runnable(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    project_id: str,
) -> bool:
    """Return True when the project has a claimable ready task."""
    return project_has_claimable_task(conn, project_id, config)


def run_project_cycle(
    conn: sqlite3.Connection,
    runtime: ProjectRuntime,
    reporter: Reporter = NULL_REPORTER,
    *,
    agent_id: str | None = None,
    task_id: str | None = None,
) -> ProjectCycleResult:
    """Run one task cycle scoped to a single project.

    Claims a project-scoped ready task atomically with a matching agent,
    then delegates to the existing engine's _process_task for the full
    pipeline (worktree, agent, verification, review, commit/push,
    fallback).
    """
    from .engine import _process_task

    if task_id is None:
        task, claimed_agent_id = claim_project_ready_task(
            conn,
            runtime.project_id,
            runtime.config,
            preferred_agent_id=agent_id,
        )
        if task is None or claimed_agent_id is None:
            return ProjectCycleResult(
                project_id=runtime.project_id,
                stop_reason="no ready tasks",
            )
        agent_id = claimed_agent_id
        task_id = task["id"]
    else:
        if agent_id is None:
            return ProjectCycleResult(
                project_id=runtime.project_id,
                stop_reason="agent_id required for pre-claimed task",
            )
        task = get_task(conn, task_id)

    try:
        processed = _process_task(
            conn,
            runtime.config,
            runtime.repo_root,
            dict(task),
            agent_id=agent_id,
            reporter=reporter,
        )

        if processed:
            _register_cycle_summary(conn, runtime, task_id=task_id)
            return ProjectCycleResult(
                project_id=runtime.project_id,
                task_id=task_id,
                tasks_processed=1,
            )
        return ProjectCycleResult(
            project_id=runtime.project_id,
            task_id=task_id,
            stop_reason="task skipped",
        )
    except Exception as exc:
        from .worker_state import write_worker_state_snapshot

        write_worker_state_snapshot(
            conn,
            project_id=runtime.project_id,
            task_id=task_id,
            attempt_id=None,
            agent_id=agent_id,
            run_id=None,
            state_type="failure",
            payload={
                "task_id": task_id,
                "agent_id": agent_id,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return ProjectCycleResult(
            project_id=runtime.project_id,
            task_id=task_id,
            tasks_processed=1,
            failures=1,
            stop_reason=str(exc),
        )
    finally:
        release_task_lease(conn, task_id)