"""Overnight schedule windows and morning summaries for Phase 7."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any

from .config import CoordinatorConfig


@dataclass(frozen=True)
class OvernightWindow:
    quiet_start: str
    quiet_end: str


@dataclass(frozen=True)
class ParsedOvernightArgs:
    enabled: bool
    until_time: str | None = None


@dataclass(frozen=True)
class QuietHoursDecision:
    should_pause: bool
    kill_workers: bool = False
    reason: str = ""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_hhmm(value: str) -> time:
    hour_text, minute_text = value.split(":", 1)
    return time(hour=int(hour_text), minute=int(minute_text))


def overnight_window_from_config(config: CoordinatorConfig) -> OvernightWindow:
    overnight = getattr(config, "overnight", None)
    if overnight is None:
        return OvernightWindow(quiet_start="22:00", quiet_end="08:00")
    return OvernightWindow(
        quiet_start=str(getattr(overnight, "quiet_start", "22:00")),
        quiet_end=str(getattr(overnight, "quiet_end", "08:00")),
    )


def is_within_quiet_hours(
    moment: datetime,
    window: OvernightWindow,
) -> bool:
    """Return True when *moment* falls inside the configured quiet window."""
    current = time(hour=moment.hour, minute=moment.minute)
    start = _parse_hhmm(window.quiet_start)
    end = _parse_hhmm(window.quiet_end)
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def parse_overnight_until(args: str) -> ParsedOvernightArgs:
    parts = args.strip().split()
    enabled = bool(parts) and parts[0].lower() == "start"
    until_time: str | None = None
    for index, token in enumerate(parts):
        if token == "--until" and index + 1 < len(parts):
            until_time = parts[index + 1]
    return ParsedOvernightArgs(enabled=enabled, until_time=until_time)


def should_pause_for_quiet_hours(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    window: OvernightWindow,
    now: datetime | None = None,
) -> QuietHoursDecision:
    """Request a safe pause during quiet hours without killing active workers."""
    moment = now or datetime.now(timezone.utc)
    if not is_within_quiet_hours(moment, window):
        return QuietHoursDecision(should_pause=False, kill_workers=False)
    running = conn.execute(
        """
        select 1 from tasks
        where project_id = ? and state = 'running'
        limit 1
        """,
        (project_id,),
    ).fetchone()
    reason = "quiet hours active"
    if running is not None:
        reason = "quiet hours active; waiting for running tasks at safe boundary"
    return QuietHoursDecision(
        should_pause=True,
        kill_workers=False,
        reason=reason,
    )


def _redact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    blocked_keys = {"env", "environment", "prompt", "secret", "token", "password"}
    redacted: dict[str, Any] = {}
    for key, value in summary.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in blocked_keys):
            continue
        redacted[key] = value
    return redacted


def persist_overnight_summary(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    run_session_id: str | None,
    window_started_at: str,
    window_ended_at: str,
    summary: dict[str, Any],
    commit: bool = True,
) -> int:
    now = _iso_now()
    cursor = conn.execute(
        """
        insert into overnight_summaries(
            project_id, run_session_id, window_started_at,
            window_ended_at, summary_json, created_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            run_session_id,
            window_started_at,
            window_ended_at,
            json.dumps(_redact_summary(summary)),
            now,
        ),
    )
    summary_id = int(cursor.lastrowid)
    if commit:
        conn.commit()
    return summary_id


def get_latest_overnight_summary(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        select summary_json from overnight_summaries
        where project_id = ?
        order by created_at desc, id desc
        limit 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["summary_json"])


def build_overnight_summary_for_run(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    run_session_id: str | None,
) -> dict[str, Any]:
    """Aggregate a redacted overnight summary from durable run/session state."""
    from .strategy import list_milestones

    completed = conn.execute(
        """
        select count(*) as count from tasks
        where project_id = ? and state = 'done'
        """,
        (project_id,),
    ).fetchone()["count"]
    failed = conn.execute(
        """
        select count(*) as count from tasks
        where project_id = ? and state in ('failed', 'blocked', 'cancelled')
        """,
        (project_id,),
    ).fetchone()["count"]
    milestones = [
        milestone.title
        for milestone in list_milestones(conn, project_id=project_id, status="active")
    ]
    return {
        "project_id": project_id,
        "run_session_id": run_session_id,
        "tasks_completed": int(completed),
        "tasks_failed": int(failed),
        "milestones_touched": milestones[:5],
        "notes": "overnight window summary",
    }


def maybe_pause_for_quiet_hours(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
    now: datetime | None = None,
) -> QuietHoursDecision:
    """Pause an active autonomous run during quiet hours and persist summary."""
    from .autonomous_runs import get_active_run_session, pause_run_session

    window = overnight_window_from_config(config)
    decision = should_pause_for_quiet_hours(
        conn,
        project_id=project_id,
        window=window,
        now=now,
    )
    if not decision.should_pause:
        return decision

    session = get_active_run_session(conn, project_id=project_id)
    if session is None or session.status != "running":
        return decision

    pause_run_session(conn, project_id=project_id)
    summary = build_overnight_summary_for_run(
        conn,
        project_id=project_id,
        run_session_id=session.id,
    )
    moment = now or datetime.now(timezone.utc)
    persist_overnight_summary(
        conn,
        project_id=project_id,
        run_session_id=session.id,
        window_started_at=moment.isoformat(),
        window_ended_at=moment.isoformat(),
        summary=summary,
        commit=False,
    )
    conn.commit()
    return decision