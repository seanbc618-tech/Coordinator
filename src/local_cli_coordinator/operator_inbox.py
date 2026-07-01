"""Operator control tower inbox: durable, deduped, project-scoped attention items."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import CoordinatorConfig
from .readiness import check_loop_readiness

VALID_SEVERITIES = frozenset({"info", "warning", "error", "critical"})
VALID_STATUSES = frozenset({"open", "acknowledged", "resolved", "dismissed"})
VALID_SOURCE_TYPES = frozenset({
    "task",
    "review",
    "risk",
    "delivery",
    "ci",
    "recovery",
    "run",
    "config",
    "supervisor",
})

_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|token)\s*[=:]\s*)(\S+)"
)


@dataclass(frozen=True)
class OperatorItem:
    id: str
    project_id: str
    source_type: str
    source_id: str
    severity: str
    status: str
    title: str
    summary: str
    action_label: str
    action_method: str | None
    action_params: dict[str, Any]
    dedupe_key: str
    created_at: str
    updated_at: str
    resolved_at: str | None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "opitem") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _validate_enum(value: str, allowed: frozenset[str], field: str) -> str:
    text = value.strip()
    if text not in allowed:
        raise ValueError(f"invalid {field}: {value!r}")
    return text


def _redact_text(text: str) -> str:
    return _SECRET_RE.sub(r"\1[REDACTED]", text)


def _row_to_item(row: sqlite3.Row) -> OperatorItem:
    return OperatorItem(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        source_type=str(row["source_type"]),
        source_id=str(row["source_id"]),
        severity=str(row["severity"]),
        status=str(row["status"]),
        title=_redact_text(str(row["title"])),
        summary=_redact_text(str(row["summary"])),
        action_label=str(row["action_label"]),
        action_method=str(row["action_method"]) if row["action_method"] else None,
        action_params=json.loads(row["action_params_json"]),
        dedupe_key=str(row["dedupe_key"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        resolved_at=str(row["resolved_at"]) if row["resolved_at"] else None,
    )


def _item_payload(item: OperatorItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "severity": item.severity,
        "status": item.status,
        "title": item.title,
        "summary": item.summary,
        "action_label": item.action_label,
        "action_method": item.action_method,
        "action_params": item.action_params,
        "dedupe_key": item.dedupe_key,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "resolved_at": item.resolved_at,
    }


def upsert_operator_item(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    source_type: str,
    source_id: str,
    severity: str,
    title: str,
    dedupe_key: str,
    summary: str = "",
    action_label: str = "",
    action_method: str | None = None,
    action_params: Mapping[str, Any] | None = None,
    status: str = "open",
    commit: bool = False,
) -> OperatorItem:
    _validate_enum(source_type, VALID_SOURCE_TYPES, "source_type")
    _validate_enum(severity, VALID_SEVERITIES, "severity")
    _validate_enum(status, VALID_STATUSES, "status")
    now = _iso_now()
    existing = conn.execute(
        """
        select id from operator_items
        where project_id = ? and dedupe_key = ? and status in ('open', 'acknowledged')
        """,
        (project_id, dedupe_key),
    ).fetchone()
    params_json = json.dumps(dict(action_params or {}))
    if existing is not None:
        item_id = str(existing["id"])
        conn.execute(
            """
            update operator_items
            set source_type = ?, source_id = ?, severity = ?, title = ?, summary = ?,
                action_label = ?, action_method = ?, action_params_json = ?,
                updated_at = ?
            where id = ?
            """,
            (
                source_type,
                source_id,
                severity,
                _redact_text(title),
                _redact_text(summary),
                action_label,
                action_method,
                params_json,
                now,
                item_id,
            ),
        )
    else:
        item_id = _new_id()
        conn.execute(
            """
            insert into operator_items(
                id, project_id, source_type, source_id, severity, status, title,
                summary, action_label, action_method, action_params_json,
                dedupe_key, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                project_id,
                source_type,
                source_id,
                severity,
                status,
                _redact_text(title),
                _redact_text(summary),
                action_label,
                action_method,
                params_json,
                dedupe_key,
                now,
                now,
            ),
        )
    if commit:
        conn.commit()
    row = conn.execute("select * from operator_items where id = ?", (item_id,)).fetchone()
    assert row is not None
    return _row_to_item(row)


def list_operator_items(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    status: str | None = None,
    min_severity: str | None = None,
) -> list[OperatorItem]:
    severity_order = {"info": 0, "warning": 1, "error": 2, "critical": 3}
    if status is None:
        rows = conn.execute(
            """
            select * from operator_items
            where project_id = ? and status in ('open', 'acknowledged')
            order by updated_at desc
            """,
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select * from operator_items
            where project_id = ? and status = ?
            order by updated_at desc
            """,
            (project_id, status),
        ).fetchall()
    items = [_row_to_item(row) for row in rows]
    if min_severity is not None:
        floor = severity_order.get(min_severity, 0)
        items = [item for item in items if severity_order.get(item.severity, 0) >= floor]
    return items


def get_operator_item(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    project_id: str | None = None,
) -> OperatorItem | None:
    if project_id is None:
        row = conn.execute(
            "select * from operator_items where id = ?",
            (item_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "select * from operator_items where id = ? and project_id = ?",
            (item_id, project_id),
        ).fetchone()
    return _row_to_item(row) if row is not None else None


def resolve_operator_item(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    commit: bool = False,
) -> None:
    now = _iso_now()
    conn.execute(
        """
        update operator_items
        set status = 'resolved', resolved_at = ?, updated_at = ?
        where id = ? and status in ('open', 'acknowledged')
        """,
        (now, now, item_id),
    )
    if commit:
        conn.commit()


def dismiss_operator_item(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    project_id: str,
    commit: bool = False,
) -> OperatorItem | None:
    now = _iso_now()
    conn.execute(
        """
        update operator_items
        set status = 'dismissed', updated_at = ?
        where id = ? and project_id = ? and status in ('open', 'acknowledged')
        """,
        (now, item_id, project_id),
    )
    if commit:
        conn.commit()
    return get_operator_item(conn, item_id=item_id, project_id=project_id)


def _collect_task_items(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    rows = conn.execute(
        """
        select id, title, state, updated_at from tasks
        where project_id = ? and state in ('awaiting_human', 'failed', 'blocked', 'running')
        """,
        (project_id,),
    ).fetchall()
    max_runtime = config.policy.max_task_runtime_seconds
    now = datetime.now(timezone.utc)
    for row in rows:
        task_id = str(row["id"])
        state = str(row["state"])
        title = str(row["title"])
        if state == "awaiting_human":
            drafts.append(
                {
                    "source_type": "task",
                    "source_id": task_id,
                    "severity": "warning",
                    "title": f"Task awaiting human review: {title}",
                    "summary": f"Task {task_id} is blocked on human approval.",
                    "action_label": "Approve task",
                    "action_method": "project.task.approve",
                    "action_params": {"task_id": task_id},
                    "dedupe_key": f"task:{task_id}:awaiting_human",
                }
            )
        elif state in {"failed", "blocked"}:
            drafts.append(
                {
                    "source_type": "task",
                    "source_id": task_id,
                    "severity": "error",
                    "title": f"Task {state}: {title}",
                    "summary": f"Task {task_id} is {state}.",
                    "action_label": "Retry task",
                    "action_method": "project.task.retry",
                    "action_params": {"task_id": task_id},
                    "dedupe_key": f"task:{task_id}:{state}",
                }
            )
        elif state == "running":
            updated = str(row["updated_at"])
            try:
                updated_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
            except ValueError:
                updated_at = now
            elapsed = (now - updated_at).total_seconds()
            if elapsed > max_runtime:
                drafts.append(
                    {
                        "source_type": "task",
                        "source_id": task_id,
                        "severity": "critical",
                        "title": f"Task running beyond timeout: {title}",
                        "summary": (
                            f"Task {task_id} exceeded {max_runtime}s runtime budget."
                        ),
                        "action_label": "Cancel task",
                        "action_method": "project.task.cancel",
                        "action_params": {"task_id": task_id},
                        "dedupe_key": f"task:{task_id}:running_timeout",
                        "requires_confirmation": True,
                    }
                )
    return drafts


def _collect_delivery_items(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    rows = conn.execute(
        """
        select id, task_id, status, pr_number, last_check_state
        from delivery_records
        where project_id = ? and status in ('ci_failed', 'ready', 'draft')
        """,
        (project_id,),
    ).fetchall()
    for row in rows:
        delivery_id = int(row["id"])
        status = str(row["status"])
        task_id = str(row["task_id"] or "")
        if status == "ci_failed":
            drafts.append(
                {
                    "source_type": "delivery",
                    "source_id": str(delivery_id),
                    "severity": "error",
                    "title": f"CI failed for delivery #{delivery_id}",
                    "summary": (
                        f"PR #{row['pr_number']} has failing checks. "
                        "A bounded recovery proposal may be available."
                    ),
                    "action_label": "Open recovery proposals",
                    "action_method": "project.recoveries",
                    "action_params": {"status": "pending", "delivery_id": delivery_id},
                    "dedupe_key": f"delivery:{delivery_id}:ci_failed",
                }
            )
        elif status == "ready":
            drafts.append(
                {
                    "source_type": "delivery",
                    "source_id": str(delivery_id),
                    "severity": "info",
                    "title": f"Delivery ready for task {task_id}",
                    "summary": f"PR #{row['pr_number']} checks passed.",
                    "action_label": "Deliver",
                    "action_method": "project.deliver",
                    "action_params": {"task_id": task_id},
                    "dedupe_key": f"delivery:{delivery_id}:ready",
                }
            )
    return drafts


def _collect_recovery_items(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    rows = conn.execute(
        """
        select id, task_id, title, proposal_type from task_recovery_proposals
        where project_id = ? and status = 'pending'
        """,
        (project_id,),
    ).fetchall()
    for row in rows:
        proposal_id = int(row["id"])
        task_id = str(row["task_id"])
        drafts.append(
            {
                "source_type": "recovery",
                "source_id": str(proposal_id),
                "severity": "warning",
                "title": str(row["title"]),
                "summary": f"Recovery proposal ({row['proposal_type']}) for {task_id}.",
                "action_label": "View recoveries",
                "action_method": "project.recoveries",
                "action_params": {"status": "pending"},
                "dedupe_key": f"recovery:{proposal_id}:pending",
            }
        )
    return drafts


def _collect_run_items(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    row = conn.execute(
        """
        select id, status, stop_reason from autonomous_run_sessions
        where project_id = ? and status in ('paused', 'failed', 'expired')
        order by updated_at desc limit 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return drafts
    session_id = str(row["id"])
    status = str(row["status"])
    drafts.append(
        {
            "source_type": "run",
            "source_id": session_id,
            "severity": "warning" if status == "paused" else "error",
            "title": f"Autonomous run {status}",
            "summary": str(row["stop_reason"] or f"Run session {session_id} is {status}."),
            "action_label": "View loop status",
            "action_method": "project.loop.status",
            "action_params": {},
            "dedupe_key": f"run:{session_id}:{status}",
        }
    )
    return drafts


def _collect_config_items(
    *,
    project_id: str,
    repo_root: Path,
    config: CoordinatorConfig,
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    for check in check_loop_readiness(repo_root, config):
        if check.status == "pass":
            continue
        drafts.append(
            {
                "source_type": "config",
                "source_id": check.name,
                "severity": "warning" if check.status == "warn" else "error",
                "title": f"Config blocker: {check.name}",
                "summary": check.message,
                "action_label": "Scan project",
                "action_method": "project.scan",
                "action_params": {},
                "dedupe_key": f"config:{check.name}:{check.status}",
            }
        )
    return drafts


def refresh_operator_inbox(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
    repo_root: Path,
    commit: bool = False,
) -> list[OperatorItem]:
    """Collect durable state, upsert open items, resolve stale ones."""
    drafts: list[dict[str, Any]] = []
    drafts.extend(_collect_task_items(conn, project_id=project_id, config=config))
    drafts.extend(_collect_delivery_items(conn, project_id=project_id))
    drafts.extend(_collect_recovery_items(conn, project_id=project_id))
    drafts.extend(_collect_run_items(conn, project_id=project_id))
    drafts.extend(_collect_config_items(project_id=project_id, repo_root=repo_root, config=config))

    active_keys = {draft["dedupe_key"] for draft in drafts}
    for draft in drafts:
        upsert_operator_item(
            conn,
            project_id=project_id,
            source_type=draft["source_type"],
            source_id=draft["source_id"],
            severity=draft["severity"],
            title=draft["title"],
            summary=draft.get("summary", ""),
            action_label=draft.get("action_label", ""),
            action_method=draft.get("action_method"),
            action_params=draft.get("action_params"),
            dedupe_key=draft["dedupe_key"],
        )

    open_rows = conn.execute(
        """
        select id, dedupe_key from operator_items
        where project_id = ? and status in ('open', 'acknowledged')
        """,
        (project_id,),
    ).fetchall()
    for row in open_rows:
        if str(row["dedupe_key"]) not in active_keys:
            resolve_operator_item(conn, item_id=str(row["id"]))

    if commit:
        conn.commit()
    return list_operator_items(conn, project_id=project_id)


def build_inbox_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
    repo_root: Path,
) -> dict[str, Any]:
    items = refresh_operator_inbox(
        conn,
        project_id=project_id,
        config=config,
        repo_root=repo_root,
        commit=True,
    )
    return {
        "project_id": project_id,
        "items": [_item_payload(item) for item in items],
    }


def build_attention_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
    repo_root: Path,
) -> dict[str, Any]:
    items = refresh_operator_inbox(
        conn,
        project_id=project_id,
        config=config,
        repo_root=repo_root,
        commit=True,
    )
    attention = [
        _item_payload(item)
        for item in items
        if item.severity in {"warning", "error", "critical"}
    ]
    return {"project_id": project_id, "items": attention}


DESTRUCTIVE_METHODS = frozenset({"project.task.cancel"})


def build_operator_decision(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    item_id: str,
    dry_run: bool = False,
    confirmed: bool = False,
) -> dict[str, Any]:
    item = get_operator_item(conn, item_id=item_id, project_id=project_id)
    if item is None:
        raise ValueError(f"operator item {item_id!r} not found")
    if item.status not in {"open", "acknowledged"}:
        raise ValueError(f"operator item {item_id!r} is not actionable")

    routed_method = item.action_method
    if routed_method is None:
        raise ValueError(f"operator item {item_id!r} has no action")

    requires_confirmation = routed_method in DESTRUCTIVE_METHODS
    payload = {
        "project_id": project_id,
        "item_id": item.id,
        "routed_method": routed_method,
        "routed_params": dict(item.action_params),
        "dry_run": dry_run,
        "requires_confirmation": requires_confirmation,
    }
    if dry_run or (requires_confirmation and not confirmed):
        payload["executed"] = False
        if requires_confirmation and not confirmed:
            payload["confirmation_hint"] = "provide confirmed=true to proceed"
        return payload

    payload["executed"] = True
    payload["note"] = "caller must invoke routed_method via existing Supervisor RPC"

    from .artifact_registry import (
        ArtifactRegistryError,
        register_artifact,
        resolve_warehouse_paths,
    )

    paths = resolve_warehouse_paths()
    if paths is not None:
        audit_dir = paths.data_dir / "warehouse" / "operator_decisions" / project_id
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / f"{item.id}.json"
        audit_path.write_text(
            json.dumps(
                {
                    "item_id": item.id,
                    "routed_method": routed_method,
                    "routed_params": dict(item.action_params),
                    "executed": True,
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
                project_id=project_id,
                artifact_type="summary",
                path=audit_path,
                provenance={"source": "operator_decision", "item_id": item.id},
                redaction_status="redacted",
                commit=True,
            )
        except ArtifactRegistryError:
            pass

    return payload