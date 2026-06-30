"""Durable GitHub delivery records and PR lifecycle."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import CoordinatorConfig
from .db import get_task
from .delivery_policy import evaluate_delivery_policy
from .evidence import list_task_evidence
from .github_cli import GitHubCli, classify_check_bucket
from .review_packets_v2 import get_review_packet_v2


@dataclass(frozen=True)
class DeliveryRecord:
    id: int
    project_id: str
    task_id: str | None
    repo_id: str
    branch_name: str
    base_branch: str
    provider: str
    status: str
    pr_number: int | None
    pr_url: str | None
    last_check_state: str | None
    merge_ready: bool
    requires_human_review: bool
    evidence_packet_path: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DeliveryEvent:
    id: int
    delivery_id: int
    project_id: str
    event_type: str
    status: str
    data: dict[str, Any]
    created_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_record(row: sqlite3.Row) -> DeliveryRecord:
    return DeliveryRecord(
        id=int(row["id"]),
        project_id=str(row["project_id"]),
        task_id=str(row["task_id"]) if row["task_id"] else None,
        repo_id=str(row["repo_id"]),
        branch_name=str(row["branch_name"]),
        base_branch=str(row["base_branch"]),
        provider=str(row["provider"]),
        status=str(row["status"]),
        pr_number=int(row["pr_number"]) if row["pr_number"] is not None else None,
        pr_url=str(row["pr_url"]) if row["pr_url"] else None,
        last_check_state=(
            str(row["last_check_state"]) if row["last_check_state"] else None
        ),
        merge_ready=bool(row["merge_ready"]),
        requires_human_review=bool(row["requires_human_review"]),
        evidence_packet_path=(
            str(row["evidence_packet_path"]) if row["evidence_packet_path"] else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_event(row: sqlite3.Row) -> DeliveryEvent:
    return DeliveryEvent(
        id=int(row["id"]),
        delivery_id=int(row["delivery_id"]),
        project_id=str(row["project_id"]),
        event_type=str(row["event_type"]),
        status=str(row["status"]),
        data=json.loads(row["data_json"]),
        created_at=str(row["created_at"]),
    )


def create_delivery_record(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str | None,
    repo_id: str,
    branch_name: str,
    base_branch: str,
    status: str = "draft",
    pr_number: int | None = None,
    pr_url: str | None = None,
    last_check_state: str | None = None,
    merge_ready: bool = False,
    requires_human_review: bool = True,
    evidence_packet_path: str | None = None,
    commit: bool = False,
) -> DeliveryRecord:
    now = _iso_now()
    cursor = conn.execute(
        """
        insert into delivery_records(
            project_id, task_id, repo_id, branch_name, base_branch, provider,
            status, pr_number, pr_url, last_check_state, merge_ready,
            requires_human_review, evidence_packet_path, created_at, updated_at
        ) values (?, ?, ?, ?, ?, 'github', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            task_id,
            repo_id,
            branch_name,
            base_branch,
            status,
            pr_number,
            pr_url,
            last_check_state,
            1 if merge_ready else 0,
            1 if requires_human_review else 0,
            evidence_packet_path,
            now,
            now,
        ),
    )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from delivery_records where id = ?",
        (int(cursor.lastrowid),),
    ).fetchone()
    assert row is not None
    return _row_to_record(row)


def get_delivery_record(
    conn: sqlite3.Connection,
    *,
    delivery_id: int,
) -> DeliveryRecord | None:
    row = conn.execute(
        "select * from delivery_records where id = ?",
        (delivery_id,),
    ).fetchone()
    return _row_to_record(row) if row is not None else None


def get_delivery_for_branch(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    repo_id: str,
    branch_name: str,
) -> DeliveryRecord | None:
    row = conn.execute(
        """
        select * from delivery_records
        where project_id = ? and repo_id = ? and branch_name = ?
          and status in ('draft', 'pushed', 'pr_open', 'ci_pending', 'ci_failed', 'ready')
        order by updated_at desc, id desc
        limit 1
        """,
        (project_id, repo_id, branch_name),
    ).fetchone()
    return _row_to_record(row) if row is not None else None


def get_delivery_for_task(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> DeliveryRecord | None:
    row = conn.execute(
        """
        select * from delivery_records
        where project_id = ? and task_id = ?
        order by updated_at desc, id desc
        limit 1
        """,
        (project_id, task_id),
    ).fetchone()
    return _row_to_record(row) if row is not None else None


def list_delivery_records(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> list[DeliveryRecord]:
    rows = conn.execute(
        """
        select * from delivery_records
        where project_id = ?
        order by updated_at desc, id desc
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def update_delivery_record(
    conn: sqlite3.Connection,
    *,
    delivery_id: int,
    **fields: Any,
) -> DeliveryRecord:
    allowed = {
        "status",
        "pr_number",
        "pr_url",
        "last_check_state",
        "merge_ready",
        "requires_human_review",
        "evidence_packet_path",
    }
    updates: list[str] = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"unsupported delivery field: {key}")
        if key in {"merge_ready", "requires_human_review"}:
            value = 1 if value else 0
        updates.append(f"{key} = ?")
        values.append(value)
    updates.append("updated_at = ?")
    values.append(_iso_now())
    values.append(delivery_id)
    conn.execute(
        f"update delivery_records set {', '.join(updates)} where id = ?",
        values,
    )
    record = get_delivery_record(conn, delivery_id=delivery_id)
    assert record is not None
    return record


def append_delivery_event(
    conn: sqlite3.Connection,
    *,
    delivery_id: int,
    project_id: str,
    event_type: str,
    status: str,
    data: Mapping[str, Any] | None = None,
    commit: bool = False,
) -> DeliveryEvent:
    now = _iso_now()
    cursor = conn.execute(
        """
        insert into delivery_events(
            delivery_id, project_id, event_type, status, data_json, created_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            delivery_id,
            project_id,
            event_type,
            status,
            json.dumps(dict(data or {})),
            now,
        ),
    )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from delivery_events where id = ?",
        (int(cursor.lastrowid),),
    ).fetchone()
    assert row is not None
    return _row_to_event(row)


def list_delivery_events(
    conn: sqlite3.Connection,
    *,
    delivery_id: int,
) -> list[DeliveryEvent]:
    rows = conn.execute(
        """
        select * from delivery_events
        where delivery_id = ?
        order by created_at asc, id asc
        """,
        (delivery_id,),
    ).fetchall()
    return [_row_to_event(row) for row in rows]


def _evidence_summary_for_task(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    commands_passed = 0
    changed_files: list[str] = []
    for row in list_task_evidence(conn, project_id=project_id, task_id=task_id):
        if row.evidence_type == "command" and row.status == "passed":
            commands_passed += 1
        if row.evidence_type != "diff":
            continue
        files = row.data.get("changed_files")
        if isinstance(files, list):
            changed_files.extend(str(path) for path in files)
    return {
        "commands_passed": commands_passed,
        "changed_files": changed_files,
    }


def build_pr_body_from_task(
    *,
    task_title: str,
    task_id: str,
    evidence_summary: Mapping[str, Any],
    review_verdict: str,
    evidence_packet_path: str,
) -> str:
    lines = [
        f"## Coordinator delivery: {task_title}",
        "",
        f"- **Task ID:** {task_id}",
        f"- **Review verdict:** {review_verdict}",
        f"- **Evidence packet:** `{evidence_packet_path}`",
        "",
        "### Evidence summary",
        "",
        json.dumps(dict(evidence_summary), indent=2),
        "",
    ]
    return "\n".join(lines)


def _github_client(
    repo_root: Path,
    *,
    gh_executable: str,
    gh_prefix: list[str] | None,
    env: Mapping[str, str] | None,
) -> GitHubCli:
    return GitHubCli(
        executable=gh_executable,
        extra_prefix=gh_prefix,
        cwd=repo_root,
        env=env,
    )


def create_or_update_pr(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    delivery_id: int,
    gh_executable: str = "gh",
    gh_prefix: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    commit: bool = True,
) -> DeliveryRecord:
    record = get_delivery_record(conn, delivery_id=delivery_id)
    if record is None:
        raise ValueError(f"delivery record {delivery_id} not found")
    if record.task_id is None:
        raise ValueError("delivery record missing task_id")

    task = get_task(conn, record.task_id)
    repo = config.repos.get(record.repo_id)
    if repo is None:
        raise ValueError(f"repo {record.repo_id!r} is not configured")

    packet = get_review_packet_v2(
        conn, project_id=record.project_id, task_id=record.task_id
    )
    verdict = packet.verdict if packet is not None else "unknown"
    packet_path = (
        str(packet.json_path)
        if packet is not None
        else f".coordinator/review_packets_v2/{record.task_id}.json"
    )
    body = build_pr_body_from_task(
        task_title=str(task["title"]),
        task_id=record.task_id,
        evidence_summary=_evidence_summary_for_task(
            conn, project_id=record.project_id, task_id=record.task_id
        ),
        review_verdict=verdict,
        evidence_packet_path=packet_path,
    )
    client = _github_client(
        Path(repo.path),
        gh_executable=gh_executable,
        gh_prefix=gh_prefix,
        env=env,
    )

    if record.pr_number is not None:
        result = client.pr_edit(record.pr_number, body=body)
        if result.returncode != 0:
            append_delivery_event(
                conn,
                delivery_id=record.id,
                project_id=record.project_id,
                event_type="pr_edit",
                status="failed",
                data={"stderr": result.stderr},
            )
            raise RuntimeError(result.stderr.strip() or "pr edit failed")
        updated = update_delivery_record(
            conn,
            delivery_id=record.id,
            status="pr_open",
            evidence_packet_path=packet_path,
        )
        append_delivery_event(
            conn,
            delivery_id=record.id,
            project_id=record.project_id,
            event_type="pr_edit",
            status="ok",
            data={"pr_number": record.pr_number},
        )
        if commit:
            conn.commit()
        return updated

    result = client.pr_create(
        title=f"Coordinator: {task['title']}",
        body=body,
        base=record.base_branch,
        head=record.branch_name,
    )
    if result.returncode != 0:
        append_delivery_event(
            conn,
            delivery_id=record.id,
            project_id=record.project_id,
            event_type="pr_create",
            status="failed",
            data={"stderr": result.stderr},
        )
        raise RuntimeError(result.stderr.strip() or "pr create failed")

    pr_url = result.stdout.strip()
    pr_number = None
    if "/pull/" in pr_url:
        try:
            pr_number = int(pr_url.rsplit("/pull/", 1)[-1].split("/", 1)[0])
        except ValueError:
            pr_number = None

    updated = update_delivery_record(
        conn,
        delivery_id=record.id,
        status="pr_open",
        pr_url=pr_url,
        pr_number=pr_number,
        evidence_packet_path=packet_path,
    )
    append_delivery_event(
        conn,
        delivery_id=record.id,
        project_id=record.project_id,
        event_type="pr_create",
        status="ok",
        data={"pr_url": pr_url, "pr_number": pr_number},
    )
    if commit:
        conn.commit()
    return updated


def poll_ci_status(
    conn: sqlite3.Connection,
    *,
    delivery_id: int,
    gh_executable: str = "gh",
    gh_prefix: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    config: CoordinatorConfig | None = None,
    commit: bool = True,
) -> DeliveryRecord:
    record = get_delivery_record(conn, delivery_id=delivery_id)
    if record is None:
        raise ValueError(f"delivery record {delivery_id} not found")
    if record.pr_number is None:
        raise ValueError("delivery record has no PR number")

    repo_root = None
    if config is not None:
        repo = config.repos.get(record.repo_id)
        if repo is not None:
            repo_root = Path(repo.path)
    if repo_root is None:
        repo_root = Path(".")

    client = _github_client(
        repo_root,
        gh_executable=gh_executable,
        gh_prefix=gh_prefix,
        env=env,
    )
    checks = client.pr_checks(record.pr_number)
    bucket = classify_check_bucket(checks)
    status = {
        "pass": "ready",
        "fail": "ci_failed",
        "pending": "ci_pending",
        "cancelled": "ci_failed",
        "skipped": "ready",
    }.get(bucket, "ci_pending")

    updated = update_delivery_record(
        conn,
        delivery_id=record.id,
        status=status,
        last_check_state=bucket,
    )
    append_delivery_event(
        conn,
        delivery_id=record.id,
        project_id=record.project_id,
        event_type="ci_poll",
        status=bucket,
        data={"checks": [{"name": c.name, "bucket": c.bucket} for c in checks]},
    )
    if commit:
        conn.commit()
    return updated


def deliver_task(
    conn: sqlite3.Connection,
    *,
    config: CoordinatorConfig,
    project_id: str,
    task_id: str,
    branch_name: str | None = None,
    gh_executable: str = "gh",
    gh_prefix: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    task = get_task(conn, task_id)
    if str(task["project_id"]) != project_id:
        raise ValueError(f"task {task_id!r} is not in project {project_id!r}")

    repo_id = str(task["repo"])
    repo = config.repos.get(repo_id)
    if repo is None:
        raise ValueError(f"repo {repo_id!r} is not configured")

    resolved_branch = branch_name or f"{repo.branch_prefix}{task_id}"
    decision = evaluate_delivery_policy(
        conn,
        config=config,
        project_id=project_id,
        task_id=task_id,
        branch_name=resolved_branch,
        action="deliver",
    )
    if not decision.allowed:
        return {
            "project_id": project_id,
            "task_id": task_id,
            "allowed": False,
            "blockers": list(decision.blockers),
            "requires_human_review": decision.requires_human_review,
            "delivery": None,
        }

    record = get_delivery_for_branch(
        conn,
        project_id=project_id,
        repo_id=repo_id,
        branch_name=resolved_branch,
    )
    if record is None:
        record = create_delivery_record(
            conn,
            project_id=project_id,
            task_id=task_id,
            repo_id=repo_id,
            branch_name=resolved_branch,
            base_branch=repo.default_branch,
            merge_ready=True,
            requires_human_review=decision.requires_human_review,
        )

    record = create_or_update_pr(
        conn,
        config=config,
        delivery_id=record.id,
        gh_executable=gh_executable,
        gh_prefix=gh_prefix,
        env=env,
        commit=False,
    )
    record = poll_ci_status(
        conn,
        delivery_id=record.id,
        gh_executable=gh_executable,
        gh_prefix=gh_prefix,
        env=env,
        config=config,
        commit=False,
    )

    if record.last_check_state == "fail":
        from .delivery_recovery import propose_recovery_for_ci_failure

        propose_recovery_for_ci_failure(
            conn,
            project_id=project_id,
            delivery_id=record.id,
            commit=False,
        )

    if commit:
        conn.commit()

    return {
        "project_id": project_id,
        "task_id": task_id,
        "allowed": True,
        "blockers": [],
        "requires_human_review": decision.requires_human_review,
        "delivery": {
            "id": record.id,
            "status": record.status,
            "pr_number": record.pr_number,
            "pr_url": record.pr_url,
            "last_check_state": record.last_check_state,
            "evidence_packet_path": record.evidence_packet_path,
        },
    }


def build_delivery_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    record = get_delivery_for_task(
        conn, project_id=project_id, task_id=task_id
    )
    if record is None:
        return {
            "project_id": project_id,
            "task_id": task_id,
            "delivery": None,
        }
    return {
        "project_id": project_id,
        "task_id": task_id,
        "delivery": {
            "id": record.id,
            "status": record.status,
            "branch_name": record.branch_name,
            "pr_number": record.pr_number,
            "pr_url": record.pr_url,
            "last_check_state": record.last_check_state,
            "merge_ready": record.merge_ready,
            "requires_human_review": record.requires_human_review,
            "evidence_packet_path": record.evidence_packet_path,
        },
    }


def build_prs_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> dict[str, Any]:
    records = list_delivery_records(conn, project_id=project_id)
    return {
        "project_id": project_id,
        "prs": [
            {
                "id": record.id,
                "task_id": record.task_id,
                "repo_id": record.repo_id,
                "branch_name": record.branch_name,
                "status": record.status,
                "pr_number": record.pr_number,
                "pr_url": record.pr_url,
                "last_check_state": record.last_check_state,
            }
            for record in records
            if record.pr_number is not None or record.status in ("pr_open", "ci_pending", "ci_failed", "ready")
        ],
    }


def build_ci_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    config: CoordinatorConfig,
    gh_executable: str = "gh",
    gh_prefix: list[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    record = get_delivery_for_task(conn, project_id=project_id, task_id=task_id)
    if record is None or record.pr_number is None:
        return {
            "project_id": project_id,
            "task_id": task_id,
            "ci_state": None,
            "delivery": None,
        }
    record = poll_ci_status(
        conn,
        delivery_id=record.id,
        gh_executable=gh_executable,
        gh_prefix=gh_prefix,
        env=env,
        config=config,
        commit=True,
    )
    return {
        "project_id": project_id,
        "task_id": task_id,
        "ci_state": record.last_check_state,
        "delivery": {
            "id": record.id,
            "status": record.status,
            "pr_number": record.pr_number,
            "pr_url": record.pr_url,
            "last_check_state": record.last_check_state,
        },
    }


def build_merge_policy_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
) -> dict[str, Any]:
    repos = []
    for repo_id, repo in sorted(config.repos.items()):
        repos.append(
            {
                "repo_id": repo_id,
                "allow_push": repo.allow_push,
                "merge_policy": repo.merge_policy,
                "review_policy": repo.review_policy,
                "default_branch": repo.default_branch,
            }
        )
    return {"project_id": project_id, "repos": repos}