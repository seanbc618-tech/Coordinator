"""Safe PR rebase in isolated worktrees — never force-push by default."""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import CoordinatorConfig
from .github_delivery import get_delivery_record
from .pr_health import (
    PrHealingAttempt,
    complete_healing_attempt,
    create_healing_attempt,
    get_pr_health_record,
    upsert_pr_health_record,
)
from . import gitops


@dataclass(frozen=True)
class RebaseResult:
    action: str
    status: str
    error: str
    attempt_id: str
    worktree_path: str


def _repo_path(config: CoordinatorConfig, repo_id: str) -> Path:
    repo = config.repos.get(repo_id)
    if repo is None:
        raise ValueError(f"repo {repo_id!r} is not in allowlist")
    return repo.path


def _ensure_health_id(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    delivery_id: int,
    pr_number: int,
    head_branch: str,
    base_branch: str,
) -> str:
    existing = get_pr_health_record(
        conn, project_id=project_id, delivery_id=delivery_id
    )
    if existing is not None:
        return existing.id
    created = upsert_pr_health_record(
        conn,
        project_id=project_id,
        delivery_id=delivery_id,
        pr_number=pr_number,
        head_branch=head_branch,
        base_branch=base_branch,
        commit=False,
    )
    return created.id


def _cleanup_worktree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def dry_run_rebase(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    project_id: str,
    delivery_id: int,
    worktrees_root: Path,
    commit: bool = True,
) -> RebaseResult:
    record = get_delivery_record(conn, delivery_id=delivery_id)
    if record is None or record.project_id != project_id:
        raise ValueError(
            f"delivery {delivery_id} not found for project {project_id!r}"
        )
    repo_path = _repo_path(config, record.repo_id)
    health_id = _ensure_health_id(
        conn,
        project_id=project_id,
        delivery_id=delivery_id,
        pr_number=record.pr_number or 0,
        head_branch=record.branch_name,
        base_branch=record.base_branch,
    )
    attempt = create_healing_attempt(
        conn,
        project_id=project_id,
        delivery_id=delivery_id,
        pr_health_id=health_id,
        action="rebase_dry_run",
        status="started",
        commit=False,
    )
    worktree_path = worktrees_root / f"rebase-{delivery_id}-{uuid.uuid4().hex[:8]}"
    _cleanup_worktree(worktree_path)
    worktrees_root.mkdir(parents=True, exist_ok=True)
    add_result = gitops.git(
        [
            "worktree",
            "add",
            "--detach",
            str(worktree_path),
            record.branch_name,
        ],
        cwd=repo_path,
    )
    if add_result.returncode != 0:
        completed = complete_healing_attempt(
            conn,
            attempt_id=attempt.id,
            status="failed",
            error=add_result.stderr.strip() or "worktree add failed",
            commit=False,
        )
        if commit:
            conn.commit()
        return RebaseResult(
            action="rebase_dry_run",
            status=completed.status,
            error=completed.error,
            attempt_id=attempt.id,
            worktree_path="",
        )

    rebase_result = gitops.git(
        ["rebase", record.base_branch],
        cwd=worktree_path,
    )
    status = "succeeded" if rebase_result.returncode == 0 else "failed"
    error = "" if rebase_result.returncode == 0 else rebase_result.stderr.strip()
    gitops.git(["worktree", "remove", "--force", str(worktree_path)], cwd=repo_path)
    _cleanup_worktree(worktree_path)
    completed = complete_healing_attempt(
        conn,
        attempt_id=attempt.id,
        status=status,
        error=error,
        worktree_path=str(worktree_path),
        commit=False,
    )
    if commit:
        conn.commit()
    return RebaseResult(
        action="rebase_dry_run",
        status=completed.status,
        error=completed.error,
        attempt_id=attempt.id,
        worktree_path=str(worktree_path),
    )


def apply_rebase(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    project_id: str,
    delivery_id: int,
    worktrees_root: Path,
    force: bool = False,
    commit: bool = True,
) -> RebaseResult:
    record = get_delivery_record(conn, delivery_id=delivery_id)
    if record is None or record.project_id != project_id:
        raise ValueError(
            f"delivery {delivery_id} not found for project {project_id!r}"
        )
    repo = config.repos.get(record.repo_id)
    health_id = _ensure_health_id(
        conn,
        project_id=project_id,
        delivery_id=delivery_id,
        pr_number=record.pr_number or 0,
        head_branch=record.branch_name,
        base_branch=record.base_branch,
    )
    attempt = create_healing_attempt(
        conn,
        project_id=project_id,
        delivery_id=delivery_id,
        pr_health_id=health_id,
        action="rebase_apply",
        status="started",
        commit=False,
    )
    if force and not (repo and getattr(repo, "allow_force_update", False)):
        completed = complete_healing_attempt(
            conn,
            attempt_id=attempt.id,
            status="blocked",
            error="force rebase requires allow_force_update=true in repo policy",
            commit=False,
        )
        if commit:
            conn.commit()
        return RebaseResult(
            action="rebase_apply",
            status=completed.status,
            error=completed.error,
            attempt_id=attempt.id,
            worktree_path="",
        )
    if repo is None or not repo.allow_push:
        completed = complete_healing_attempt(
            conn,
            attempt_id=attempt.id,
            status="blocked",
            error="allow_push=false blocks branch update",
            commit=False,
        )
        if commit:
            conn.commit()
        return RebaseResult(
            action="rebase_apply",
            status=completed.status,
            error=completed.error,
            attempt_id=attempt.id,
            worktree_path="",
        )
    if record.requires_human_review:
        completed = complete_healing_attempt(
            conn,
            attempt_id=attempt.id,
            status="blocked",
            error="human review required before branch update",
            commit=False,
        )
        if commit:
            conn.commit()
        return RebaseResult(
            action="rebase_apply",
            status=completed.status,
            error=completed.error,
            attempt_id=attempt.id,
            worktree_path="",
        )

    dry = dry_run_rebase(
        conn,
        config=config,
        project_id=project_id,
        delivery_id=delivery_id,
        worktrees_root=worktrees_root,
        commit=False,
    )
    if dry.status != "succeeded":
        completed = complete_healing_attempt(
            conn,
            attempt_id=attempt.id,
            status="failed",
            error=dry.error or "dry-run rebase failed",
            commit=False,
        )
        if commit:
            conn.commit()
        return RebaseResult(
            action="rebase_apply",
            status=completed.status,
            error=completed.error,
            attempt_id=attempt.id,
            worktree_path=dry.worktree_path,
        )

    repo_path = _repo_path(config, record.repo_id)
    checkout = gitops.git(["checkout", record.branch_name], cwd=repo_path)
    if checkout.returncode != 0:
        completed = complete_healing_attempt(
            conn,
            attempt_id=attempt.id,
            status="failed",
            error=checkout.stderr.strip(),
            commit=False,
        )
        if commit:
            conn.commit()
        return RebaseResult(
            action="rebase_apply",
            status=completed.status,
            error=completed.error,
            attempt_id=attempt.id,
            worktree_path="",
        )
    rebase_result = gitops.git(["rebase", record.base_branch], cwd=repo_path)
    if rebase_result.returncode != 0:
        gitops.git(["rebase", "--abort"], cwd=repo_path)
        completed = complete_healing_attempt(
            conn,
            attempt_id=attempt.id,
            status="failed",
            error=rebase_result.stderr.strip(),
            commit=False,
        )
        if commit:
            conn.commit()
        return RebaseResult(
            action="rebase_apply",
            status=completed.status,
            error=completed.error,
            attempt_id=attempt.id,
            worktree_path="",
        )
    completed = complete_healing_attempt(
        conn,
        attempt_id=attempt.id,
        status="succeeded",
        commit=False,
    )
    if commit:
        conn.commit()
    return RebaseResult(
        action="rebase_apply",
        status=completed.status,
        error=completed.error,
        attempt_id=attempt.id,
        worktree_path="",
    )