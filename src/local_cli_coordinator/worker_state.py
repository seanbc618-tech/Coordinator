"""Worker state snapshots for operability and replay."""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .config import AgentConfig

_SENSITIVE_KEY_MARKERS = ("token", "secret", "password", "api_key", "key")
_TOKEN_VALUE_RE = re.compile(r"(?<![a-zA-Z])sk-[a-zA-Z0-9_-]+", re.IGNORECASE)
_SECRET_ARG_FLAGS = frozenset(
    {"--token", "--api-key", "--api_key", "--password", "--secret"}
)

ALLOWED_STATE_TYPES = frozenset(
    {"pre_prompt", "post_attempt", "failure", "cancellation", "handoff"}
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _redact_string(value: str) -> str:
    return _TOKEN_VALUE_RE.sub("[REDACTED]", value)


def _redact_list(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    redact_next = False
    for item in values:
        if redact_next:
            result.append("[REDACTED]")
            redact_next = False
            continue
        if isinstance(item, str) and item.lower() in _SECRET_ARG_FLAGS:
            result.append(item)
            redact_next = True
            continue
        result.append(redact_worker_state(item))
    return result


def redact_worker_state(value: object) -> object:
    """Return JSON-safe state with secrets removed."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() == "env":
                continue
            if _is_sensitive_key(key):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_worker_state(item)
        return redacted
    if isinstance(value, list):
        return _redact_list(value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


def write_worker_state_snapshot(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str | None,
    attempt_id: int | None,
    agent_id: str | None,
    run_id: str | None,
    state_type: str,
    payload: dict,
) -> str:
    """Persist a redacted worker state snapshot and return its id."""
    if state_type not in ALLOWED_STATE_TYPES:
        raise ValueError(f"invalid state_type: {state_type}")
    snapshot_id = f"wsnap-{uuid.uuid4().hex[:12]}"
    redacted = redact_worker_state(payload)
    conn.execute(
        """
        insert into worker_state_snapshots(
            id, project_id, task_id, attempt_id, agent_id, run_id,
            state_type, payload, redaction_version
        ) values (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            snapshot_id,
            project_id,
            task_id,
            attempt_id,
            agent_id,
            run_id,
            state_type,
            json.dumps(redacted),
        ),
    )
    return snapshot_id


def list_worker_state_snapshots(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Return recent snapshots newest first."""
    query = """
        select id, project_id, task_id, attempt_id, agent_id, run_id,
               state_type, payload, redaction_version, created_at
        from worker_state_snapshots
        where project_id = ?
    """
    params: list[Any] = [project_id]
    if task_id is not None:
        query += " and task_id = ?"
        params.append(task_id)
    query += " order by created_at desc, id desc limit ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    results: list[dict] = []
    for row in rows:
        entry = dict(row)
        entry["payload"] = json.loads(entry["payload"])
        results.append(entry)
    return results


def record_post_attempt_snapshot(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    attempt_id: int,
    agent: AgentConfig,
    attempt_number: int,
    worktree: Path,
    exit_code: int,
    timed_out: bool,
    log_path: str,
) -> str:
    """Write a ``post_attempt`` snapshot for a finished worker attempt."""
    command = shlex.split(agent.command) if agent.command else []
    return write_worker_state_snapshot(
        conn,
        project_id=project_id,
        task_id=task_id,
        attempt_id=attempt_id,
        agent_id=agent.id,
        run_id=None,
        state_type="post_attempt",
        payload={
            "task_id": task_id,
            "agent_id": agent.id,
            "attempt": attempt_number,
            "command": command,
            "cwd": str(worktree),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "log_path": log_path,
        },
    )