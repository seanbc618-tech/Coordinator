"""Watch delivery PR health via fake or real gh without mutating git by default."""

from __future__ import annotations

import sqlite3
from typing import Mapping

from .config import CoordinatorConfig
from .github_cli import GitHubCli, classify_check_bucket
from .github_delivery import get_delivery_record
from .operator_inbox import upsert_operator_item
from .pr_health import (
    PrHealthRecord,
    complete_healing_attempt,
    create_healing_attempt,
    upsert_pr_health_record,
)
from . import gitops


def _resolve_repo_path(config: CoordinatorConfig, repo_id: str):
    repo = config.repos.get(repo_id)
    if repo is None:
        raise ValueError(f"repo {repo_id!r} is not in allowlist")
    return repo.path


def _branch_is_stale(repo_path, *, head_branch: str, base_branch: str) -> bool:
    head_result = gitops.git(["rev-parse", head_branch], cwd=repo_path)
    base_result = gitops.git(["rev-parse", base_branch], cwd=repo_path)
    if head_result.returncode != 0 or base_result.returncode != 0:
        return False
    head_sha = head_result.stdout.strip()
    base_sha = base_result.stdout.strip()
    merge_result = gitops.git(
        ["merge-base", head_sha, base_sha],
        cwd=repo_path,
    )
    if merge_result.returncode != 0:
        return False
    merge_base = merge_result.stdout.strip()
    return merge_base != base_sha


def _derive_status(
    *,
    pr_state: str,
    ci_state: str,
    stale: bool,
) -> str:
    state = pr_state.upper()
    if state in {"CLOSED", "MERGED"}:
        return state.lower()
    if stale:
        return "stale"
    if ci_state == "fail":
        return "ci_failed"
    if ci_state == "pass":
        return "healthy"
    return "observed"


def watch_delivery_pr_health(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    project_id: str,
    delivery_id: int,
    gh_executable: str = "gh",
    gh_prefix: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    commit: bool = True,
) -> PrHealthRecord:
    record = get_delivery_record(conn, delivery_id=delivery_id)
    if record is None or record.project_id != project_id:
        raise ValueError(
            f"delivery {delivery_id} not found for project {project_id!r}"
        )
    if record.pr_number is None:
        raise ValueError(f"delivery {delivery_id} has no PR number")

    repo_path = _resolve_repo_path(config, record.repo_id)
    health = upsert_pr_health_record(
        conn,
        project_id=project_id,
        delivery_id=delivery_id,
        pr_number=record.pr_number,
        head_branch=record.branch_name,
        base_branch=record.base_branch,
        status="observed",
        commit=False,
    )
    attempt = create_healing_attempt(
        conn,
        project_id=project_id,
        delivery_id=delivery_id,
        pr_health_id=health.id,
        action="watch",
        status="started",
        commit=False,
    )

    cli = GitHubCli(
        executable=gh_executable,
        extra_prefix=gh_prefix,
        cwd=repo_path,
        env=env,
    )
    pr_view = cli.pr_view(record.pr_number)
    gh_failed = pr_view is None
    if gh_failed:
        upsert_operator_item(
            conn,
            project_id=project_id,
            source_type="delivery",
            source_id=str(delivery_id),
            severity="warning",
            title=f"PR #{record.pr_number} watch needs gh",
            summary="GitHub CLI unavailable; PR health recorded without remote state.",
            dedupe_key=f"pr-watch-gh-missing:{delivery_id}",
            action_label="Retry watch",
            action_method="project.pr.health",
            action_params={"delivery_id": delivery_id},
            commit=False,
        )
        head_branch = record.branch_name
        base_branch = record.base_branch
        pr_state = "OPEN"
        ci_state = record.last_check_state or "unknown"
    else:
        assert pr_view is not None
        head_branch = pr_view.head_ref or record.branch_name
        base_branch = pr_view.base_ref or record.base_branch
        pr_state = pr_view.state
        checks = cli.pr_checks(record.pr_number)
        ci_state = classify_check_bucket(checks)

    stale = _branch_is_stale(
        repo_path,
        head_branch=head_branch,
        base_branch=base_branch,
    )
    head_sha = ""
    base_sha = ""
    head_rev = gitops.git(["rev-parse", head_branch], cwd=repo_path)
    base_rev = gitops.git(["rev-parse", base_branch], cwd=repo_path)
    if head_rev.returncode == 0:
        head_sha = head_rev.stdout.strip()
    if base_rev.returncode == 0:
        base_sha = base_rev.stdout.strip()

    status = _derive_status(pr_state=pr_state, ci_state=ci_state, stale=stale)
    health = upsert_pr_health_record(
        conn,
        project_id=project_id,
        delivery_id=delivery_id,
        pr_number=record.pr_number,
        head_branch=head_branch,
        base_branch=base_branch,
        status=status,
        head_sha=head_sha,
        base_sha=base_sha,
        merge_state="stale" if stale else "clean",
        ci_state=ci_state,
        review_state="unknown",
        stale=stale,
        commit=False,
    )
    complete_healing_attempt(
        conn,
        attempt_id=attempt.id,
        status="succeeded" if not gh_failed else "failed",
        error="" if not gh_failed else "gh unavailable",
        commit=False,
    )
    if commit:
        conn.commit()
    return health


def watch_project_pr_health(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    project_id: str,
    gh_executable: str = "gh",
    gh_prefix: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    stale_only: bool = False,
) -> list[PrHealthRecord]:
    from .github_delivery import list_delivery_records

    records: list[PrHealthRecord] = []
    for delivery in list_delivery_records(conn, project_id=project_id):
        if delivery.pr_number is None:
            continue
        if delivery.status in {"merged", "closed"}:
            continue
        health = watch_delivery_pr_health(
            conn,
            config=config,
            project_id=project_id,
            delivery_id=delivery.id,
            gh_executable=gh_executable,
            gh_prefix=gh_prefix,
            env=env,
            commit=False,
        )
        if stale_only and not health.stale:
            continue
        records.append(health)
    conn.commit()
    return records