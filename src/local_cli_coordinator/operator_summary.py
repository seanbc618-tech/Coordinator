"""Deterministic operator summaries with redacted highlights."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .operator_inbox import list_operator_items

_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|token)\s*[=:]\s*)(\S+)"
)
_ENV_RE = re.compile(r"(?i)(env[\"']?\s*:\s*[\"'])([^\"']+)([\"'])")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        text = _SECRET_RE.sub(r"\1[REDACTED]", value)
        return _ENV_RE.sub(r"\1[REDACTED]\3", text)
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _count_items(items) -> dict[str, int]:
    counts = {"info": 0, "warning": 0, "error": 0, "critical": 0, "total": 0}
    for item in items:
        counts["total"] += 1
        counts[item.severity] = counts.get(item.severity, 0) + 1
    return counts


def build_project_summary(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> dict[str, Any]:
    items = list_operator_items(conn, project_id=project_id)
    highlights = [
        _redact({"title": item.title, "severity": item.severity, "source_type": item.source_type})
        for item in items[:10]
    ]
    return {
        "scope": "project",
        "project_id": project_id,
        "summary_kind": "current",
        "counts": _count_items(items),
        "highlights": highlights,
    }


def build_global_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    project_rows = conn.execute(
        "select distinct project_id from operator_items where status in ('open', 'acknowledged')"
    ).fetchall()
    projects: list[dict[str, Any]] = []
    total_counts = {"info": 0, "warning": 0, "error": 0, "critical": 0, "total": 0}
    for row in project_rows:
        project_id = str(row["project_id"])
        summary = build_project_summary(conn, project_id=project_id)
        projects.append(summary)
        for key, value in summary["counts"].items():
            total_counts[key] = total_counts.get(key, 0) + int(value)
    return {
        "scope": "global",
        "project_id": None,
        "summary_kind": "current",
        "counts": total_counts,
        "projects": projects,
    }


def build_morning_summary(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    from .overnight import get_latest_overnight_summary

    if project_id is None:
        base = build_global_summary(conn)
        base["summary_kind"] = "morning"
        return base

    items = list_operator_items(conn, project_id=project_id)
    overnight = get_latest_overnight_summary(conn, project_id=project_id)
    highlights: list[dict[str, Any]] = []
    if overnight is not None:
        highlights.append(
            _redact(
                {
                    "kind": "overnight",
                    "tasks_completed": overnight.get("tasks_completed", 0),
                    "tasks_failed": overnight.get("tasks_failed", 0),
                }
            )
        )
    for item in items[:5]:
        highlights.append(
            _redact(
                {
                    "kind": "attention",
                    "title": item.title,
                    "severity": item.severity,
                }
            )
        )
    summary_id = f"opsum-{uuid.uuid4().hex[:12]}"
    counts = _count_items(items)
    conn.execute(
        """
        insert into operator_summaries(
            id, scope, project_id, summary_kind, counts_json, highlights_json, created_at
        ) values (?, 'project', ?, 'morning', ?, ?, ?)
        """,
        (
            summary_id,
            project_id,
            json.dumps(counts),
            json.dumps(highlights),
            _iso_now(),
        ),
    )
    conn.commit()
    return {
        "scope": "project",
        "project_id": project_id,
        "summary_kind": "morning",
        "counts": counts,
        "highlights": highlights,
        "summary_id": summary_id,
    }


def build_summary_payload(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    kind: str = "current",
) -> dict[str, Any]:
    if kind == "morning":
        return build_morning_summary(conn, project_id=project_id)
    return build_project_summary(conn, project_id=project_id)