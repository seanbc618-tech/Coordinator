"""Retention planning with dry-run default and safe apply."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .evidence_export import export_evidence_bundle
from .runtime_paths import RuntimePaths

_RETENTION_SCOPES = frozenset({"project", "global"})
_RETENTION_MODES = frozenset({"dry_run", "apply"})


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_created_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _select_stale_artifacts(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    max_age_days: int,
    limit: int = 500,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, max_age_days))
    query = "select * from artifacts where created_at < ?"
    params: list[Any] = [cutoff.isoformat()]
    if project_id is not None:
        query += " and project_id = ?"
        params.append(project_id)
    query += " order by created_at asc, id asc limit ?"
    params.append(max(1, limit))
    rows = conn.execute(query, params).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        path = Path(str(row["path"]))
        if not path.is_file():
            continue
        candidates.append(
            {
                "artifact_id": str(row["id"]),
                "project_id": str(row["project_id"]),
                "task_id": str(row["task_id"]) if row["task_id"] else None,
                "artifact_type": str(row["artifact_type"]),
                "path": str(path),
                "created_at": str(row["created_at"]),
                "size_bytes": int(row["size_bytes"]),
            }
        )
    return candidates


def plan_retention(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    scope: str = "project",
    project_id: str | None = None,
    mode: str = "dry_run",
    max_age_days: int = 30,
    commit: bool = True,
) -> dict[str, Any]:
    if scope not in _RETENTION_SCOPES:
        raise ValueError(f"unsupported retention scope: {scope!r}")
    if mode not in _RETENTION_MODES:
        raise ValueError(f"unsupported retention mode: {mode!r}")
    if scope == "project" and not project_id:
        raise ValueError("project_id is required for project retention")

    run_id = f"ret-{uuid.uuid4().hex[:12]}"
    effective_project_id = project_id if scope == "project" else None
    candidates = _select_stale_artifacts(
        conn,
        project_id=effective_project_id,
        max_age_days=max_age_days,
    )
    plan = {
        "scope": scope,
        "project_id": effective_project_id,
        "mode": mode,
        "max_age_days": max_age_days,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "db_history_preserved": True,
    }

    result: dict[str, Any] = {
        "deleted_files": 0,
        "export_id": None,
        "manifest_path": None,
        "errors": [],
    }
    status = "planned" if mode == "dry_run" else "completed"

    if mode == "apply" and candidates:
        export_scope = "project" if effective_project_id else "global"
        export_result = export_evidence_bundle(
            conn,
            paths=paths,
            scope=export_scope,
            project_id=effective_project_id,
            limit=len(candidates),
            commit=False,
        )
        result["export_id"] = export_result["export_id"]
        result["manifest_path"] = export_result["manifest_path"]

        for candidate in candidates:
            path = Path(candidate["path"])
            try:
                if path.is_file():
                    path.unlink()
                    result["deleted_files"] += 1
                conn.execute(
                    """
                    update artifacts
                    set redaction_status = 'blocked'
                    where id = ?
                    """,
                    (candidate["artifact_id"],),
                )
            except OSError as exc:
                result["errors"].append(f"{candidate['artifact_id']}: {exc}")
                status = "partial"

    conn.execute(
        """
        insert into retention_runs(
            id, scope, project_id, mode, status, plan_json, result_json,
            created_at, finished_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            scope,
            effective_project_id,
            mode,
            status,
            json.dumps(plan),
            json.dumps(result),
            _iso_now(),
            _iso_now() if mode == "apply" else None,
        ),
    )
    if commit:
        conn.commit()

    return {
        "retention_run_id": run_id,
        "scope": scope,
        "project_id": effective_project_id,
        "mode": mode,
        "status": status,
        "plan": plan,
        "result": result,
        "dry_run": mode == "dry_run",
    }