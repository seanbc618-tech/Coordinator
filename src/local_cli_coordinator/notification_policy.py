"""Notification delivery policy: dedupe, quiet hours, sink enablement."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import NotificationsPolicyConfig
from .overnight import OvernightWindow, is_within_quiet_hours

SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


@dataclass(frozen=True)
class NotificationDecision:
    allowed: bool
    reason: str = ""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def command_sink_allowed(
    policy: NotificationsPolicyConfig,
    *,
    rule_enabled: bool,
) -> bool:
    return bool(rule_enabled and policy.allow_command_sink)


def _load_rule(conn: sqlite3.Connection, rule_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "select * from notification_rules where id = ?",
        (rule_id,),
    ).fetchone()


def should_deliver_notification(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    severity: str,
    project_id: str,
    dedupe_key: str | None = None,
    now: datetime | None = None,
) -> NotificationDecision:
    rule = _load_rule(conn, rule_id)
    if rule is None:
        return NotificationDecision(False, "rule not found")
    if not bool(rule["enabled"]):
        return NotificationDecision(False, "rule disabled")

    min_severity = str(rule["min_severity"])
    if SEVERITY_ORDER.get(severity, 0) < SEVERITY_ORDER.get(min_severity, 0):
        return NotificationDecision(False, "below min_severity")

    quiet_start = rule["quiet_start"]
    quiet_end = rule["quiet_end"]
    if quiet_start and quiet_end:
        moment = now or datetime.now(timezone.utc)
        window = OvernightWindow(
            quiet_start=str(quiet_start),
            quiet_end=str(quiet_end),
        )
        if is_within_quiet_hours(moment, window) and severity != "critical":
            return NotificationDecision(False, "quiet hours active")

    if dedupe_key:
        existing = conn.execute(
            """
            select 1 from notification_deliveries
            where rule_id = ? and dedupe_key = ? and status = 'sent'
            """,
            (rule_id, dedupe_key),
        ).fetchone()
        if existing is not None:
            return NotificationDecision(False, "duplicate dedupe_key")

    return NotificationDecision(True)


def record_notification_delivery(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    project_id: str,
    sink: str,
    status: str,
    dedupe_key: str,
    payload: dict,
    operator_item_id: str | None = None,
    error: str = "",
    commit: bool = True,
) -> str:
    delivery_id = f"notify-{uuid.uuid4().hex[:12]}"
    import json

    conn.execute(
        """
        insert into notification_deliveries(
            id, rule_id, operator_item_id, project_id, sink, status,
            dedupe_key, payload_json, error, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            delivery_id,
            rule_id,
            operator_item_id,
            project_id,
            sink,
            status,
            dedupe_key,
            json.dumps(payload),
            error,
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()
    return delivery_id


def dispatch_project_notifications(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config,
    state_dir,
    items: list,
    dry_run: bool = False,
) -> dict:
    """Evaluate rules and deliver or record skipped notifications."""
    from .notification_sinks import deliver_notification

    rules = conn.execute(
        """
        select * from notification_rules
        where enabled = 1 and (project_id is null or project_id = ?)
        """,
        (project_id,),
    ).fetchall()
    deliveries: list[dict] = []
    for item in items:
        for rule in rules:
            sink = str(rule["sink"])
            if sink == "command" and not command_sink_allowed(
                config.notifications,
                rule_enabled=bool(rule["enabled"]),
            ):
                record_notification_delivery(
                    conn,
                    rule_id=str(rule["id"]),
                    project_id=project_id,
                    sink=sink,
                    status="skipped",
                    dedupe_key=item.dedupe_key,
                    payload={"title": item.title, "severity": item.severity},
                    operator_item_id=item.id,
                    error="command sink disabled by policy",
                    commit=False,
                )
                deliveries.append({"status": "skipped", "reason": "command sink disabled"})
                continue

            dedupe_key = f"{rule['id']}:{item.dedupe_key}"
            decision = should_deliver_notification(
                conn,
                rule_id=str(rule["id"]),
                severity=item.severity,
                project_id=project_id,
                dedupe_key=dedupe_key,
            )
            payload = {
                "project_id": project_id,
                "item_id": item.id,
                "title": item.title,
                "severity": item.severity,
                "summary": item.summary,
            }
            if not decision.allowed:
                record_notification_delivery(
                    conn,
                    rule_id=str(rule["id"]),
                    project_id=project_id,
                    sink=sink,
                    status="skipped",
                    dedupe_key=dedupe_key,
                    payload=payload,
                    operator_item_id=item.id,
                    error=decision.reason,
                    commit=False,
                )
                deliveries.append({"status": "skipped", "reason": decision.reason})
                continue

            if dry_run:
                deliveries.append({"status": "dry_run", "sink": sink, "item_id": item.id})
                continue

            if sink == "stdout":
                result = deliver_notification(sink="stdout", payload=payload)
            elif sink == "file":
                result = deliver_notification(
                    sink="file",
                    payload=payload,
                    state_dir=state_dir,
                )
            else:
                deliveries.append({"status": "skipped", "reason": "command sink not configured"})
                continue

            record_notification_delivery(
                conn,
                rule_id=str(rule["id"]),
                project_id=project_id,
                sink=sink,
                status=result.status,
                dedupe_key=dedupe_key,
                payload=payload,
                operator_item_id=item.id,
                error=result.error,
                commit=False,
            )
            deliveries.append({"status": result.status, "sink": sink, "item_id": item.id})

    conn.commit()
    return {"project_id": project_id, "deliveries": deliveries, "dry_run": dry_run}