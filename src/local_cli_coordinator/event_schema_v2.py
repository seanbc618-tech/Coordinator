"""Canonical supervisor event schema v2."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

ALLOWED_SEVERITIES = frozenset({"debug", "info", "warn", "error"})
ALLOWED_PROVENANCE = frozenset(
    {"supervisor", "commander", "worker", "evaluator", "operator", "tui", "cli"}
)

_LEGACY_NAME_MAP = {
    "task_created": "task.created",
    "task_started": "task.started",
    "task_completed": "task.completed",
    "task_done": "task.completed",
    "task_failed": "task.failed",
    "task_cancelled": "task.cancelled",
    "chat_message": "chat.received",
    "backlog_item": "backlog.generated",
    "backlog_generated": "backlog.generated",
    "goal_created": "goal.created",
    "goal_activated": "goal.activated",
    "project_registered": "project.registered",
    "commander_started": "commander.started",
    "run_started": "run.started",
    "run_stopped": "run.stopped",
    "diagnostic_warning": "diagnostic.warning",
}


@dataclass(frozen=True)
class EventV2:
    project_id: str
    seq: int
    name: str
    source: str
    actor: str | None
    severity: str
    provenance: str
    terminal_fingerprint: str | None
    payload: dict[str, object]
    legacy_cursor: int | None


def normalize_legacy_event_name(event_type: str) -> str:
    """Map a legacy supervisor event type to its canonical v2 name."""
    mapped = _LEGACY_NAME_MAP.get(event_type)
    if mapped is not None:
        return mapped
    if "." in event_type:
        return event_type
    return event_type.replace("_", ".")


def _infer_source(name: str) -> str:
    return name.split(".", 1)[0]


def _infer_actor(payload: dict[str, Any]) -> str | None:
    for key in ("actor", "agent_id", "started_by"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _infer_provenance(name: str, payload: dict[str, Any]) -> str:
    explicit = payload.get("provenance")
    if isinstance(explicit, str) and explicit in ALLOWED_PROVENANCE:
        return explicit
    prefix = name.split(".", 1)[0]
    if prefix in ALLOWED_PROVENANCE:
        return prefix
    if prefix == "commander":
        return "commander"
    return "supervisor"


def _infer_severity(name: str) -> str:
    lowered = name.lower()
    if "failed" in lowered or "error" in lowered:
        return "error"
    if "warning" in lowered or "warn" in lowered:
        return "warn"
    return "info"


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "seq": row["seq"],
        "name": row["name"],
        "source": row["source"],
        "actor": row["actor"],
        "severity": row["severity"],
        "provenance": row["provenance"],
        "terminal_fingerprint": row["terminal_fingerprint"],
        "payload": json.loads(row["payload"]),
        "legacy_cursor": row["legacy_cursor"],
        "created_at": row["created_at"],
    }


def mirror_legacy_event(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    legacy_event_type: str,
    payload: dict[str, Any],
    legacy_cursor: int,
) -> str:
    """Mirror one legacy supervisor event into ``supervisor_events_v2``."""
    name = normalize_legacy_event_name(legacy_event_type)
    event = EventV2(
        project_id=project_id,
        seq=0,
        name=name,
        source=_infer_source(name),
        actor=_infer_actor(payload),
        severity=_infer_severity(name),
        provenance=_infer_provenance(name, payload),
        terminal_fingerprint=None,
        payload=dict(payload),
        legacy_cursor=legacy_cursor,
    )
    while True:
        conn.execute("begin immediate")
        try:
            row = conn.execute(
                "select coalesce(max(seq), 0) as max_seq "
                "from supervisor_events_v2 where project_id = ?",
                (project_id,),
            ).fetchone()
            seq = row["max_seq"] + 1
            event_id = f"evt2-{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                insert into supervisor_events_v2(
                    id, project_id, seq, name, source, actor, severity,
                    provenance, terminal_fingerprint, payload, legacy_cursor
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event.project_id,
                    seq,
                    event.name,
                    event.source,
                    event.actor,
                    event.severity,
                    event.provenance,
                    event.terminal_fingerprint,
                    json.dumps(event.payload),
                    event.legacy_cursor,
                ),
            )
            conn.commit()
            return event_id
        except sqlite3.IntegrityError:
            conn.rollback()
        except sqlite3.Error:
            conn.rollback()
            raise


def list_events_v2(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    after: int = 0,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Return v2 events for a project with ``seq`` greater than ``after``."""
    rows = conn.execute(
        """
        select * from supervisor_events_v2
        where project_id = ? and seq > ?
        order by seq asc
        limit ?
        """,
        (project_id, after, limit),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]