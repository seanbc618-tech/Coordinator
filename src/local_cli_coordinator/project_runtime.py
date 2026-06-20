"""Project runtime adapter for multi-project Supervisor.

Wraps the existing engine's _process_task with project-scoped task
claiming. Preserves the full pipeline: worktree, agent, verification,
review, commit/push, fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .config import CoordinatorConfig
from .db import claim_project_task
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

    Claims a project-scoped ready task, then delegates to the existing
    engine's _process_task for the full pipeline (worktree, agent,
    verification, review, commit/push, fallback).
    """
    from .engine import _process_task

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
        processed = _process_task(
            conn,
            runtime.config,
            runtime.repo_root,
            task,
            agent_id=None,
            reporter=reporter,
        )

        if processed:
            return ProjectCycleResult(
                project_id=runtime.project_id,
                task_id=task_id,
                tasks_processed=1,
            )
        else:
            return ProjectCycleResult(
                project_id=runtime.project_id,
                task_id=task_id,
                stop_reason="task skipped",
            )
    except Exception as exc:
        return ProjectCycleResult(
            project_id=runtime.project_id,
            task_id=task_id,
            tasks_processed=1,
            failures=1,
            stop_reason=str(exc),
        )
