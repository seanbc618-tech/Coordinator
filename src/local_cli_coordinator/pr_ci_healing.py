"""Bounded PR/CI self-healing cycle orchestration."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from .ci_failure_classifier import classify_check_failure
from .config import CoordinatorConfig
from .delivery_recovery import propose_recovery_for_classified_ci_failure
from .github_cli import GitHubCli
from .github_delivery import list_delivery_records
from .pr_health import list_ci_failure_records, list_pr_health_records
from .pr_watcher import watch_delivery_pr_health


def build_pr_health_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    stale_only: bool = False,
    ci_failed_only: bool = False,
) -> dict[str, Any]:
    records = list_pr_health_records(
        conn,
        project_id=project_id,
        stale_only=stale_only,
    )
    if ci_failed_only:
        records = [r for r in records if r.status == "ci_failed" or r.ci_state == "fail"]
    return {
        "project_id": project_id,
        "records": [
            {
                "id": record.id,
                "delivery_id": record.delivery_id,
                "pr_number": record.pr_number,
                "status": record.status,
                "head_branch": record.head_branch,
                "base_branch": record.base_branch,
                "stale": record.stale,
                "ci_state": record.ci_state,
                "review_state": record.review_state,
                "updated_at": record.updated_at,
            }
            for record in records
        ],
    }


def run_pr_heal_cycle(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    project_id: str,
    gh_executable: str = "gh",
    gh_prefix: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
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
        attempts.append(
            {
                "delivery_id": delivery.id,
                "action": "watch",
                "status": health.status,
            }
        )
        if health.ci_state != "fail":
            continue
        repo = config.repos.get(delivery.repo_id)
        if repo is None:
            continue
        cli = GitHubCli(
            executable=gh_executable,
            extra_prefix=gh_prefix,
            cwd=repo.path,
            env=env,
        )
        checks = cli.pr_checks(delivery.pr_number)
        for check in checks:
            if check.bucket != "fail":
                continue
            classified = classify_check_failure(
                check_name=check.name,
                state=check.state,
                bucket=check.bucket,
                log_excerpt=f"{check.name} {check.state}",
            )
            if dry_run:
                attempts.append(
                    {
                        "delivery_id": delivery.id,
                        "action": "ci_repair",
                        "status": "skipped",
                        "check_name": check.name,
                        "failure_class": classified.failure_class,
                    }
                )
                continue
            proposal_id = propose_recovery_for_classified_ci_failure(
                conn,
                project_id=project_id,
                delivery_id=delivery.id,
                classified=classified,
                commit=False,
            )
            attempts.append(
                {
                    "delivery_id": delivery.id,
                    "action": "ci_repair",
                    "status": "succeeded" if proposal_id else "skipped",
                    "check_name": check.name,
                    "failure_class": classified.failure_class,
                    "proposal_id": proposal_id,
                }
            )
    conn.commit()
    return {"project_id": project_id, "dry_run": dry_run, "attempts": attempts}


def build_pr_reviews_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
    gh_executable: str = "gh",
    gh_prefix: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    delivery_id: int | None = None,
) -> dict[str, Any]:
    from .review_comment_ingest import ingest_pr_review_comments

    reviews: list[dict[str, Any]] = []
    for delivery in list_delivery_records(conn, project_id=project_id):
        if delivery_id is not None and delivery.id != delivery_id:
            continue
        if delivery.pr_number is None:
            continue
        result = ingest_pr_review_comments(
            conn,
            config=config,
            project_id=project_id,
            delivery_id=delivery.id,
            gh_executable=gh_executable,
            gh_prefix=gh_prefix,
            env=env,
            commit=False,
        )
        reviews.append(
            {
                "delivery_id": delivery.id,
                "pr_number": delivery.pr_number,
                "unresolved_count": result.unresolved_count,
                "evidence_path": result.evidence_path,
            }
        )
    conn.commit()
    failures = list_ci_failure_records(conn, project_id=project_id, delivery_id=delivery_id)
    return {
        "project_id": project_id,
        "reviews": reviews,
        "ci_failures": [
            {
                "delivery_id": item.delivery_id,
                "check_name": item.check_name,
                "failure_class": item.failure_class,
                "summary": item.summary,
            }
            for item in failures
        ],
    }