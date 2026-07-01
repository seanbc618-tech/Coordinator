"""Global pause and resume controls with durable audit."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .operator_hardening import get_latest_global_control_event, record_global_control_event
from .runtime_paths import RuntimePaths


def global_pause_state_path(paths: RuntimePaths) -> Path:
    return paths.state_dir / "global_pause.json"


def read_global_pause_state(paths: RuntimePaths) -> dict[str, Any]:
    path = global_pause_state_path(paths)
    if not path.is_file():
        return {"active": False, "affected_projects": [], "reason": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active": False, "affected_projects": [], "reason": ""}
    if not isinstance(data, dict):
        return {"active": False, "affected_projects": [], "reason": ""}
    return data


def write_global_pause_state(paths: RuntimePaths, payload: dict[str, Any]) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    path = global_pause_state_path(paths)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def is_global_paused(paths: RuntimePaths) -> bool:
    return bool(read_global_pause_state(paths).get("active"))


def _count_running_workers(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        select count(*) as cnt
        from task_leases
        where released_at is null
          and expires_at > datetime('now')
        """
    ).fetchone()
    return int(row["cnt"]) if row is not None else 0


def _list_active_project_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        select id from projects
        where coalesce(status, 'active') = 'active'
        order by updated_at desc
        """
    ).fetchall()
    return [str(row["id"]) for row in rows]


def pause_all(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    reason: str = "",
) -> dict[str, Any]:
    affected: list[str] = []
    for project_id in _list_active_project_ids(conn):
        affected.append(project_id)
        conn.execute(
            """
            update projects
            set status = 'paused', pause_reason = 'global', updated_at = current_timestamp
            where id = ?
            """,
            (project_id,),
        )
    running_workers = _count_running_workers(conn)
    record_global_control_event(
        conn,
        action="pause",
        scope="global",
        status="completed",
        affected_projects=affected,
        reason=reason,
        commit=False,
    )
    conn.commit()
    write_global_pause_state(
        paths,
        {
            "active": True,
            "reason": reason,
            "affected_projects": affected,
        },
    )
    return {
        "global_pause": True,
        "affected_projects": affected,
        "running_workers": running_workers,
        "workers_killed": False,
        "drain_hint": (
            "running workers were not killed; use task cancel or wait for drain"
            if running_workers
            else ""
        ),
    }


def resume_all(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    include_manual: bool = False,
) -> dict[str, Any]:
    state = read_global_pause_state(paths)
    whitelist = list(state.get("affected_projects") or [])
    if not whitelist:
        latest_pause = get_latest_global_control_event(conn, action="pause")
        if latest_pause is not None:
            whitelist = list(latest_pause.affected_projects)
    resumed: list[str] = []
    for project_id in whitelist:
        row = conn.execute(
            "select status, pause_reason from projects where id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            continue
        pause_reason = str(row["pause_reason"] or "")
        if pause_reason == "global" or include_manual:
            conn.execute(
                """
                update projects
                set status = 'active', pause_reason = '', updated_at = current_timestamp
                where id = ?
                """,
                (project_id,),
            )
            resumed.append(project_id)
    record_global_control_event(
        conn,
        action="resume",
        scope="global",
        status="completed",
        affected_projects=resumed,
        commit=False,
    )
    conn.commit()
    write_global_pause_state(
        paths,
        {"active": False, "affected_projects": [], "reason": ""},
    )
    return {
        "global_pause": False,
        "resumed_projects": resumed,
        "whitelist": whitelist,
    }