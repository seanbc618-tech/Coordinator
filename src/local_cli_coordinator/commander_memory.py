from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .goals import (
    active_goal,
    get_goal,
    get_latest_commander_run,
    linked_task_counts,
    list_linked_tasks,
)

COMMANDER_MEMORY_RELATIVE_PATH = Path("state/commander_memory.md")
_MAX_FIELD_CHARS = 2000


def commander_memory_path(root: Path) -> Path:
    return root / COMMANDER_MEMORY_RELATIVE_PATH


def _sanitize_text(value: str, *, max_chars: int = _MAX_FIELD_CHARS) -> str:
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(value))
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_chars:
        return cleaned[: max_chars - 3] + "..."
    return cleaned


def goal_status_summary(conn: sqlite3.Connection) -> tuple[str, str]:
    goal = active_goal(conn)
    if goal is None:
        return "Goal: none", "waiting for a long-term goal"
    counts = linked_task_counts(conn, goal["id"])
    if goal["status"] == "active" and counts.get("ready", 0) == 0:
        return "Goal: active", "waiting for Commander replenishment"
    detail = goal["stop_reason"] or goal["progress_summary"] or "no progress summary yet"
    return f"Goal: {goal['status']}", _sanitize_text(detail)


def write_commander_memory(
    conn: sqlite3.Connection,
    root: Path,
    goal_id: int,
) -> Path:
    goal = get_goal(conn, goal_id)
    tasks = list_linked_tasks(conn, goal_id)[:10]
    latest_run = get_latest_commander_run(conn, goal_id)
    constraints = json.loads(goal["constraints"])
    completion_criteria = json.loads(goal["completion_criteria"])
    current_batch = ""
    if tasks:
        current_batch = _sanitize_text(str(tasks[-1]["batch_id"] or ""))

    headline, next_action = goal_status_summary(conn)
    lines = [
        "# Commander Memory",
        "",
        f"Status: {goal['status']}",
        f"Objective: {_sanitize_text(goal['objective'])}",
        f"Progress: {_sanitize_text(goal['progress_summary'])}",
        f"Constraints: {_sanitize_text(', '.join(constraints) if constraints else '(none)')}",
        f"Completion criteria: {_sanitize_text(', '.join(completion_criteria) if completion_criteria else '(none)')}",
        f"Current batch: {current_batch or '(none)'}",
        "",
        "## Recent outcomes",
    ]
    if tasks:
        lines.extend(
            f"- {_sanitize_text(task['title'])}: {task['state']}" for task in tasks
        )
    else:
        lines.append("- (none)")

    if latest_run is not None:
        lines.extend([
            "",
            "## Latest run",
            f"Latest run: {latest_run['status']}",
            f"Trigger: {_sanitize_text(latest_run['trigger'])}",
            f"Progress: {_sanitize_text(latest_run['progress_summary'])}",
            f"Stop reason: {_sanitize_text(latest_run['stop_reason'] or '(none)')}",
        ])

    if goal["stop_reason"]:
        lines.extend([
            "",
            "## Stop reason",
            _sanitize_text(goal["stop_reason"]),
        ])

    lines.extend([
        "",
        "## Next action",
        next_action,
        "",
        f"Summary: {headline}",
    ])

    path = commander_memory_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path