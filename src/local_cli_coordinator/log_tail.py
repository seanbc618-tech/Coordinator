"""Task-scoped log tail from registered artifacts only."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .db import project_get_task_detail, task_list_artifacts_for_project

DEFAULT_MAX_BYTES = 65536
MAX_BYTES_CAP = 65536

_LOG_KINDS = {
    "attempt": "attempt_log",
    "verifier": "verifier_log",
    "agent": "agent_log",
}

_TAIL_ALLOWED_STATES = frozenset({
    "running",
    "verifying",
    "reviewing_quality",
    "reviewing_human",
    "done",
    "failed",
    "blocked",
    "rejected",
    "awaiting_human",
})


class LogTailError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _resolve_artifact_path(
    artifacts: list[sqlite3.Row],
    kind: str,
) -> Path | None:
    mapped = _LOG_KINDS.get(kind, kind)
    for art in artifacts:
        if art["kind"] == mapped:
            return Path(str(art["path"]))
    for art in artifacts:
        if art["kind"] == kind:
            return Path(str(art["path"]))
    return None


def read_task_log_tail(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    kind: str = "attempt",
    offset: int = 0,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    row = project_get_task_detail(conn, project_id=project_id, task_id=task_id)
    if row is None:
        raise LogTailError(
            "task_not_found",
            f"task {task_id!r} not found in project {project_id!r}",
        )

    state = str(row["state"])
    if state not in _TAIL_ALLOWED_STATES:
        raise LogTailError(
            "invalid_state",
            f"task {task_id!r} is {state!r}; log tail not available",
        )

    capped = min(max(0, max_bytes), MAX_BYTES_CAP)
    safe_offset = max(0, offset)
    artifacts = task_list_artifacts_for_project(
        conn, project_id=project_id, task_id=task_id
    )
    log_path = _resolve_artifact_path(artifacts, kind)
    if log_path is None or not log_path.is_file():
        return {
            "task_id": task_id,
            "kind": kind,
            "offset": safe_offset,
            "next_offset": safe_offset,
            "content": "",
            "eof": True,
            "truncated": False,
        }

    try:
        size = log_path.stat().st_size
    except OSError as exc:
        raise LogTailError("artifact_not_found", f"cannot read log: {exc}") from exc

    if safe_offset > size:
        safe_offset = size

    to_read = min(capped, max(0, size - safe_offset))
    truncated = (size - safe_offset) > capped
    content = ""
    if to_read > 0:
        try:
            with log_path.open("rb") as handle:
                handle.seek(safe_offset)
                raw = handle.read(to_read)
            content = raw.decode("utf-8", errors="replace")
        except OSError as exc:
            raise LogTailError("artifact_not_found", f"cannot read log: {exc}") from exc

    next_offset = safe_offset + len(content.encode("utf-8", errors="replace"))
    eof = next_offset >= size
    return {
        "task_id": task_id,
        "kind": kind,
        "offset": safe_offset,
        "next_offset": next_offset,
        "content": content,
        "eof": eof,
        "truncated": truncated,
    }


def format_log_tail_error(exc: LogTailError) -> str:
    return f"{exc.code}: {exc.message}"