"""Update PR body evidence sections without erasing prior failure history."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Mapping

from .config import CoordinatorConfig
from .github_cli import GitHubCli
from .github_delivery import get_delivery_record
from .pr_health import (
    complete_healing_attempt,
    create_healing_attempt,
    get_pr_health_record,
    upsert_pr_health_record,
)

_EVIDENCE_MARKER = "## Coordinator Evidence"
_LATEST_MARKER = "## Coordinator Evidence (latest)"


@dataclass(frozen=True)
class EvidenceUpdateResult:
    updated: bool
    body: str
    attempt_id: str
    dry_run: bool


def _render_latest_section(sections: Mapping[str, object]) -> str:
    lines = [_LATEST_MARKER, ""]
    ci = sections.get("ci")
    if ci is not None:
        lines.append(f"### CI ({ci})")
    checks = sections.get("checks")
    if isinstance(checks, list):
        for item in checks:
            lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def _merge_body(existing: str, latest_section: str) -> str:
    body = existing.strip()
    if _LATEST_MARKER in body:
        prefix, _, _rest = body.partition(_LATEST_MARKER)
        merged = prefix.rstrip() + "\n\n" + latest_section.strip() + "\n"
        return merged
    if _EVIDENCE_MARKER in body:
        return body.rstrip() + "\n\n" + latest_section.strip() + "\n"
    if body:
        return body + "\n\n" + latest_section.strip() + "\n"
    return latest_section.strip() + "\n"


def update_pr_evidence(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    project_id: str,
    delivery_id: int,
    sections: Mapping[str, object],
    gh_executable: str = "gh",
    gh_prefix: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
    commit: bool = True,
) -> EvidenceUpdateResult:
    record = get_delivery_record(conn, delivery_id=delivery_id)
    if record is None or record.project_id != project_id:
        raise ValueError(
            f"delivery {delivery_id} not found for project {project_id!r}"
        )
    if record.pr_number is None:
        raise ValueError(f"delivery {delivery_id} has no PR number")

    health = get_pr_health_record(
        conn, project_id=project_id, delivery_id=delivery_id
    )
    if health is None:
        health = upsert_pr_health_record(
            conn,
            project_id=project_id,
            delivery_id=delivery_id,
            pr_number=record.pr_number,
            head_branch=record.branch_name,
            base_branch=record.base_branch,
            commit=False,
        )
    attempt = create_healing_attempt(
        conn,
        project_id=project_id,
        delivery_id=delivery_id,
        pr_health_id=health.id,
        action="evidence_update",
        status="started",
        commit=False,
    )

    repo = config.repos.get(record.repo_id)
    if repo is None:
        raise ValueError(f"repo {record.repo_id!r} is not in allowlist")
    cli = GitHubCli(
        executable=gh_executable,
        extra_prefix=gh_prefix,
        cwd=repo.path,
        env=env,
    )
    existing_body = cli.pr_body(record.pr_number) or ""
    latest_section = _render_latest_section(sections)
    merged = _merge_body(existing_body, latest_section)

    updated = False
    if not dry_run:
        result = cli.pr_edit(record.pr_number, body=merged)
        updated = result.returncode == 0
        status = "succeeded" if updated else "failed"
        error = "" if updated else result.stderr.strip()
    else:
        status = "skipped"
        error = ""

    complete_healing_attempt(
        conn,
        attempt_id=attempt.id,
        status=status,
        error=error,
        commit=False,
    )
    if commit:
        conn.commit()
    return EvidenceUpdateResult(
        updated=updated or dry_run,
        body=merged,
        attempt_id=attempt.id,
        dry_run=dry_run,
    )