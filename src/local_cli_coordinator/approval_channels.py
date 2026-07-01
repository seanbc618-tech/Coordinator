"""External approval channel persistence and safe local delivery."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import NotificationsPolicyConfig
from .macos_notifications import deliver_macos_notification
from .notification_policy import command_sink_allowed
from .notification_sinks import deliver_notification
from .webhook_notifications import deliver_webhook_notification

VALID_REQUEST_STATUS = frozenset({
    "pending",
    "approved",
    "rejected",
    "expired",
    "cancelled",
    "consumed",
    "failed",
})
VALID_CHANNEL_TYPES = frozenset({"file", "macos", "stdout", "webhook", "command"})
VALID_DELIVERY_STATUS = frozenset({"sent", "skipped", "failed"})
VALID_AUDIT_EVENTS = frozenset({
    "created",
    "sent",
    "approved",
    "rejected",
    "expired",
    "failed",
    "consumed",
})

DEFAULT_CHANNEL_SEEDS: tuple[tuple[str, bool, dict[str, Any]], ...] = (
    ("file", True, {"path": "approvals.jsonl"}),
    ("stdout", False, {}),
    ("macos", False, {}),
    ("webhook", False, {"dry_run": True, "url": ""}),
    ("command", False, {"argv": []}),
)


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    project_id: str
    operator_item_id: str | None
    action_method: str
    action_params: dict[str, Any]
    status: str
    token_hash: str
    token_hint: str
    expires_at: str
    created_at: str
    decided_at: str | None
    decided_by: str


@dataclass(frozen=True)
class ApprovalChannelConfig:
    id: str
    channel_type: str
    enabled: bool
    project_id: str | None
    min_severity: str
    config: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ApprovalAuditEvent:
    id: str
    approval_request_id: str
    project_id: str
    event_type: str
    data: dict[str, Any]
    created_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _row_to_request(row: sqlite3.Row) -> ApprovalRequest:
    return ApprovalRequest(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        operator_item_id=str(row["operator_item_id"])
        if row["operator_item_id"]
        else None,
        action_method=str(row["action_method"]),
        action_params=json.loads(row["action_params_json"]),
        status=str(row["status"]),
        token_hash=str(row["token_hash"]),
        token_hint=str(row["token_hint"]),
        expires_at=str(row["expires_at"]),
        created_at=str(row["created_at"]),
        decided_at=str(row["decided_at"]) if row["decided_at"] else None,
        decided_by=str(row["decided_by"]),
    )


def _row_to_channel(row: sqlite3.Row) -> ApprovalChannelConfig:
    return ApprovalChannelConfig(
        id=str(row["id"]),
        channel_type=str(row["channel_type"]),
        enabled=bool(row["enabled"]),
        project_id=str(row["project_id"]) if row["project_id"] else None,
        min_severity=str(row["min_severity"]),
        config=json.loads(row["config_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def insert_approval_request(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    action_method: str,
    action_params: Mapping[str, Any],
    token_hash: str,
    token_hint: str,
    expires_at: str,
    operator_item_id: str | None = None,
    status: str = "pending",
    commit: bool = False,
) -> ApprovalRequest:
    if status not in VALID_REQUEST_STATUS:
        raise ValueError(f"invalid approval status: {status!r}")
    request_id = _new_id("apprq")
    now = _iso_now()
    conn.execute(
        """
        insert into approval_requests(
            id, project_id, operator_item_id, action_method, action_params_json,
            status, token_hash, token_hint, expires_at, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            project_id,
            operator_item_id,
            action_method,
            json.dumps(dict(action_params)),
            status,
            token_hash,
            token_hint,
            expires_at,
            now,
        ),
    )
    record_audit_event(
        conn,
        approval_request_id=request_id,
        project_id=project_id,
        event_type="created",
        data={"action_method": action_method},
        commit=False,
    )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from approval_requests where id = ?", (request_id,)
    ).fetchone()
    assert row is not None
    return _row_to_request(row)


def get_approval_request(
    conn: sqlite3.Connection, *, request_id: str
) -> ApprovalRequest | None:
    row = conn.execute(
        "select * from approval_requests where id = ?", (request_id,)
    ).fetchone()
    return _row_to_request(row) if row is not None else None


def list_approval_requests(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    status: str | None = None,
) -> list[ApprovalRequest]:
    if status is None:
        rows = conn.execute(
            """
            select * from approval_requests
            where project_id = ?
            order by created_at desc
            """,
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            select * from approval_requests
            where project_id = ? and status = ?
            order by created_at desc
            """,
            (project_id, status),
        ).fetchall()
    return [_row_to_request(row) for row in rows]


def update_approval_request_status(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    status: str,
    decided_by: str = "",
    audit_event: str | None = None,
    audit_data: Mapping[str, Any] | None = None,
    commit: bool = False,
) -> ApprovalRequest:
    if status not in VALID_REQUEST_STATUS:
        raise ValueError(f"invalid approval status: {status!r}")
    now = _iso_now()
    conn.execute(
        """
        update approval_requests
        set status = ?, decided_at = ?, decided_by = ?
        where id = ?
        """,
        (status, now, decided_by, request_id),
    )
    row = conn.execute(
        "select * from approval_requests where id = ?", (request_id,)
    ).fetchone()
    assert row is not None
    if audit_event:
        record_audit_event(
            conn,
            approval_request_id=request_id,
            project_id=str(row["project_id"]),
            event_type=audit_event,
            data=dict(audit_data or {}),
            commit=False,
        )
    if commit:
        conn.commit()
    return _row_to_request(row)


def upsert_channel_config(
    conn: sqlite3.Connection,
    *,
    channel_type: str,
    enabled: bool,
    config_json: Mapping[str, Any] | None = None,
    project_id: str | None = None,
    min_severity: str = "warning",
    commit: bool = False,
) -> ApprovalChannelConfig:
    if channel_type not in VALID_CHANNEL_TYPES:
        raise ValueError(f"invalid channel_type: {channel_type!r}")
    now = _iso_now()
    existing = conn.execute(
        """
        select id from approval_channel_configs
        where channel_type = ? and (
            (project_id is null and ? is null) or project_id = ?
        )
        """,
        (channel_type, project_id, project_id),
    ).fetchone()
    payload = json.dumps(dict(config_json or {}))
    if existing is not None:
        config_id = str(existing["id"])
        conn.execute(
            """
            update approval_channel_configs
            set enabled = ?, min_severity = ?, config_json = ?, updated_at = ?
            where id = ?
            """,
            (int(enabled), min_severity, payload, now, config_id),
        )
    else:
        config_id = _new_id("apch")
        conn.execute(
            """
            insert into approval_channel_configs(
                id, channel_type, enabled, project_id, min_severity,
                config_json, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                config_id,
                channel_type,
                int(enabled),
                project_id,
                min_severity,
                payload,
                now,
                now,
            ),
        )
    if commit:
        conn.commit()
    row = conn.execute(
        "select * from approval_channel_configs where id = ?", (config_id,)
    ).fetchone()
    assert row is not None
    return _row_to_channel(row)


def seed_default_channel_configs(
    conn: sqlite3.Connection, *, commit: bool = False
) -> list[ApprovalChannelConfig]:
    configs: list[ApprovalChannelConfig] = []
    for channel_type, enabled, config in DEFAULT_CHANNEL_SEEDS:
        configs.append(
            upsert_channel_config(
                conn,
                channel_type=channel_type,
                enabled=enabled,
                config_json=config,
                commit=False,
            )
        )
    if commit:
        conn.commit()
    return configs


def list_channel_configs(
    conn: sqlite3.Connection, *, project_id: str | None = None
) -> list[ApprovalChannelConfig]:
    rows = conn.execute(
        """
        select * from approval_channel_configs
        where project_id is null or project_id = ?
        order by channel_type
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_channel(row) for row in rows]


def record_audit_event(
    conn: sqlite3.Connection,
    *,
    approval_request_id: str,
    project_id: str,
    event_type: str,
    data: Mapping[str, Any] | None = None,
    commit: bool = False,
) -> str:
    if event_type not in VALID_AUDIT_EVENTS:
        raise ValueError(f"invalid audit event: {event_type!r}")
    event_id = _new_id("apaud")
    conn.execute(
        """
        insert into approval_audit_events(
            id, approval_request_id, project_id, event_type, data_json, created_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            approval_request_id,
            project_id,
            event_type,
            json.dumps(dict(data or {})),
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return event_id


def list_audit_events(
    conn: sqlite3.Connection, *, approval_request_id: str
) -> list[ApprovalAuditEvent]:
    rows = conn.execute(
        """
        select * from approval_audit_events
        where approval_request_id = ?
        order by created_at
        """,
        (approval_request_id,),
    ).fetchall()
    return [
        ApprovalAuditEvent(
            id=str(row["id"]),
            approval_request_id=str(row["approval_request_id"]),
            project_id=str(row["project_id"]),
            event_type=str(row["event_type"]),
            data=json.loads(row["data_json"]),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def _record_delivery(
    conn: sqlite3.Connection,
    *,
    approval_request_id: str,
    channel_config_id: str | None,
    project_id: str,
    channel_type: str,
    status: str,
    payload: Mapping[str, Any],
    error: str = "",
) -> None:
    conn.execute(
        """
        insert into approval_deliveries(
            id, approval_request_id, channel_config_id, project_id,
            channel_type, status, payload_json, error, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _new_id("apdel"),
            approval_request_id,
            channel_config_id,
            project_id,
            channel_type,
            status,
            json.dumps(dict(payload)),
            error,
            _iso_now(),
        ),
    )


def deliver_approval_request(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    project_id: str,
    state_dir: Path,
    policy: NotificationsPolicyConfig,
    commit: bool = False,
) -> dict[str, Any]:
    request = get_approval_request(conn, request_id=request_id)
    if request is None:
        raise ValueError(f"approval request {request_id!r} not found")
    if request.project_id != project_id:
        raise ValueError("approval request project mismatch")

    configs = [
        cfg
        for cfg in list_channel_configs(conn, project_id=project_id)
        if cfg.enabled
    ]
    if not configs:
        seed_default_channel_configs(conn, commit=False)
        configs = [
            cfg
            for cfg in list_channel_configs(conn, project_id=project_id)
            if cfg.enabled
        ]

    payload = {
        "project_id": project_id,
        "request_id": request.id,
        "action_method": request.action_method,
        "token_hint": request.token_hint,
        "status": request.status,
    }
    deliveries: list[dict[str, Any]] = []

    for cfg in configs:
        channel_type = cfg.channel_type
        if channel_type == "command" and not command_sink_allowed(
            policy, rule_enabled=cfg.enabled
        ):
            _record_delivery(
                conn,
                approval_request_id=request.id,
                channel_config_id=cfg.id,
                project_id=project_id,
                channel_type=channel_type,
                status="skipped",
                payload=payload,
                error="command sink disabled by policy",
            )
            deliveries.append(
                {
                    "channel_type": channel_type,
                    "status": "skipped",
                    "reason": "command sink disabled",
                }
            )
            continue

        if channel_type == "file":
            from .notification_sinks import deliver_to_file_sink

            path = state_dir / str(cfg.config.get("path", "approvals.jsonl"))
            result = deliver_to_file_sink(path, payload=payload)
        elif channel_type == "stdout":
            result = deliver_notification(sink="stdout", payload=payload)
        elif channel_type == "macos":
            result = deliver_macos_notification(
                title="Coordinator approval",
                body=f"{request.action_method} ({request.token_hint})",
                enabled=cfg.enabled,
            )
        elif channel_type == "webhook":
            result = deliver_webhook_notification(
                url=str(cfg.config.get("url", "")),
                payload=payload,
                dry_run=bool(cfg.config.get("dry_run", True)),
            )
        elif channel_type == "command":
            argv = list(cfg.config.get("argv") or [])
            result = deliver_notification(
                sink="command",
                payload=payload,
                command_argv=argv,
            )
        else:
            result = type("R", (), {"status": "failed", "error": "unknown"})()

        _record_delivery(
            conn,
            approval_request_id=request.id,
            channel_config_id=cfg.id,
            project_id=project_id,
            channel_type=channel_type,
            status=result.status,
            payload=payload,
            error=getattr(result, "error", ""),
        )
        deliveries.append(
            {
                "channel_type": channel_type,
                "status": result.status,
                "error": getattr(result, "error", ""),
            }
        )

    record_audit_event(
        conn,
        approval_request_id=request.id,
        project_id=project_id,
        event_type="sent",
        data={"deliveries": deliveries},
        commit=False,
    )
    if commit:
        conn.commit()
    return {"request_id": request.id, "deliveries": deliveries}


def build_channels_payload(
    conn: sqlite3.Connection, *, project_id: str
) -> dict[str, Any]:
    configs = list_channel_configs(conn, project_id=project_id)
    if not configs:
        configs = seed_default_channel_configs(conn, commit=True)
    return {
        "project_id": project_id,
        "channels": [
            {
                "id": cfg.id,
                "channel_type": cfg.channel_type,
                "enabled": cfg.enabled,
                "min_severity": cfg.min_severity,
                "config": cfg.config,
            }
            for cfg in configs
        ],
    }


def build_approvals_payload(
    conn: sqlite3.Connection, *, project_id: str
) -> dict[str, Any]:
    requests = list_approval_requests(conn, project_id=project_id)
    return {
        "project_id": project_id,
        "requests": [
            {
                "id": req.id,
                "status": req.status,
                "action_method": req.action_method,
                "action_params": req.action_params,
                "token_hint": req.token_hint,
                "expires_at": req.expires_at,
                "operator_item_id": req.operator_item_id,
            }
            for req in requests
        ],
    }