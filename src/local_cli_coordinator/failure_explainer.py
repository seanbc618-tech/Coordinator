"""Deterministic task failure explanations from durable records."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .db import get_task, task_latest_attempt

_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|token)\s*[=:]\s*)(\S+)"
)
_ENV_RE = re.compile(r"(?i)(env[\"']?\s*:\s*[\"'])([^\"']+)([\"'])")

_CLASSIFIED_REASONS = frozenset({
    "no_changed_files",
    "verification_failed",
    "worker_timeout",
    "agent_unavailable",
    "policy_blocked",
    "approval_required",
    "unknown",
})

_MAX_LOG_LINES = 20


def _redact_text(value: str) -> str:
    text = _SECRET_RE.sub(r"\1[REDACTED]", value)
    return _ENV_RE.sub(r"\1[REDACTED]\3", text)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _normalize_reason(result_class: str, task_state: str) -> str:
    normalized = (result_class or "").strip().lower().replace("-", "_")
    mapping = {
        "verification_failed": "verification_failed",
        "verificationfailed": "verification_failed",
        "no_changed_files": "no_changed_files",
        "worker_timeout": "worker_timeout",
        "agent_unavailable": "agent_unavailable",
        "policy_blocked": "policy_blocked",
    }
    if normalized in mapping:
        return mapping[normalized]
    if task_state == "awaiting_human":
        return "approval_required"
    if normalized:
        return normalized if normalized in _CLASSIFIED_REASONS else "unknown"
    return "unknown"


def _next_action(classified_reason: str) -> str:
    actions = {
        "verification_failed": "Review verification output and retry with /retry",
        "no_changed_files": "Confirm the worker modified expected files, then retry",
        "worker_timeout": "Reduce scope or increase timeout, then retry",
        "agent_unavailable": "Check agent command availability with /health",
        "policy_blocked": "Review policy constraints before retrying",
        "approval_required": "Approve or reject with /approve or /reject",
        "unknown": "Inspect /why output and recent task events",
    }
    return actions.get(classified_reason, actions["unknown"])


def _read_log_lines(log_path: str) -> list[str]:
    if not log_path:
        return []
    path = Path(log_path)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    redacted = [_redact_text(line) for line in lines]
    sensitive = [line for line in redacted if "[REDACTED]" in line]
    tail = redacted[-_MAX_LOG_LINES:]
    merged: list[str] = []
    for line in sensitive + tail:
        if line not in merged:
            merged.append(line)
    return merged[:_MAX_LOG_LINES]


def explain_task_failure(
    conn: sqlite3.Connection,
    *,
    task_id: str,
) -> dict[str, Any]:
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"unknown task: {task_id!r}")
    attempt = task_latest_attempt(conn, task_id)
    classified_reason = _normalize_reason(
        str(attempt["result_class"]) if attempt is not None else "",
        str(task["state"]),
    )
    latest_attempt: dict[str, Any] | None = None
    if attempt is not None:
        latest_attempt = {
            "id": int(attempt["id"]),
            "exit_code": attempt["exit_code"],
            "elapsed_seconds": None,
            "verification_command": str(task["verification_commands"]).split("\n")[0],
            "verification_result": str(attempt["result_reason"] or ""),
            "changed_file_count": conn.execute(
                "select count(*) as cnt from task_artifacts where task_id = ?",
                (task_id,),
            ).fetchone()["cnt"],
        }
    payload = {
        "task_id": task_id,
        "title": str(task["title"]),
        "status": str(task["state"]),
        "assigned_agent": str(attempt["agent_id"]) if attempt is not None else "",
        "latest_attempt": latest_attempt,
        "classified_reason": classified_reason,
        "next_action": _next_action(classified_reason),
        "log_lines": _read_log_lines(str(attempt["log_path"]) if attempt is not None else ""),
    }
    return _redact_value(payload)