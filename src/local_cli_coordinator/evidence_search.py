"""Project-scoped evidence search over the artifact warehouse."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from .artifact_registry import artifact_to_payload, list_warehouse_artifacts
from .project_indexer import redact_text
from .runtime_paths import RuntimePaths

_SEARCH_SCOPES = frozenset({"project", "global", "task"})

_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|token)\s*[=:]\s*)(\S+)"
)


def _redact_mapping(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(_SECRET_RE.sub(r"\1[REDACTED]", value))
    if isinstance(value, dict):
        return {key: _redact_mapping(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_mapping(item) for item in value]
    return value


def _parse_created_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value!r}") from exc
    return value


def search_evidence(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    scope: str = "project",
    project_id: str | None = None,
    task_id: str | None = None,
    artifact_type: str | None = None,
    agent_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    include_paths: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    if scope not in _SEARCH_SCOPES:
        raise ValueError(f"unsupported search scope: {scope!r}")
    if scope in {"project", "task"} and not project_id:
        raise ValueError("project_id is required for project/task scope")
    if scope == "task" and not task_id:
        raise ValueError("task_id is required for task scope")

    since_ts = _parse_created_at(since)
    until_ts = _parse_created_at(until)

    if scope == "global" and not include_paths:
        return _search_global_aggregate(
            conn,
            artifact_type=artifact_type,
            since=since_ts,
            until=until_ts,
        )

    query = "select * from artifacts where 1 = 1"
    params: list[Any] = []
    if project_id is not None:
        query += " and project_id = ?"
        params.append(project_id)
    if task_id is not None:
        query += " and task_id = ?"
        params.append(task_id)
    if artifact_type is not None:
        query += " and artifact_type = ?"
        params.append(artifact_type)
    if since_ts is not None:
        query += " and created_at >= ?"
        params.append(since_ts)
    if until_ts is not None:
        query += " and created_at <= ?"
        params.append(until_ts)
    query += " order by created_at desc, id desc limit ?"
    params.append(max(1, limit))

    rows = conn.execute(query, params).fetchall()
    artifacts = []
    for row in rows:
        provenance = json.loads(row["provenance_json"])
        if agent_id is not None:
            provenance_agent = str(provenance.get("agent_id") or "")
            if provenance_agent != agent_id:
                continue
        from .artifact_registry import WarehouseArtifact

        artifact = WarehouseArtifact(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            task_id=str(row["task_id"]) if row["task_id"] else None,
            run_id=str(row["run_id"]) if row["run_id"] else None,
            artifact_type=str(row["artifact_type"]),
            path=str(row["path"]),
            sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]),
            redaction_status=str(row["redaction_status"]),
            provenance=provenance,
            created_at=str(row["created_at"]),
        )
        payload = artifact_to_payload(
            artifact,
            paths=paths,
            redact_paths=scope == "global" and not include_paths,
        )
        artifacts.append(_redact_mapping(payload))

    return {
        "scope": scope,
        "project_id": project_id,
        "task_id": task_id,
        "count": len(artifacts),
        "artifacts": artifacts,
        "redacted": True,
    }


def _search_global_aggregate(
    conn: sqlite3.Connection,
    *,
    artifact_type: str | None,
    since: str | None,
    until: str | None,
) -> dict[str, Any]:
    query = """
        select project_id, artifact_type, count(*) as cnt
        from artifacts
        where 1 = 1
    """
    params: list[Any] = []
    if artifact_type is not None:
        query += " and artifact_type = ?"
        params.append(artifact_type)
    if since is not None:
        query += " and created_at >= ?"
        params.append(since)
    if until is not None:
        query += " and created_at <= ?"
        params.append(until)
    query += " group by project_id, artifact_type order by project_id, artifact_type"
    rows = conn.execute(query, params).fetchall()

    by_project: dict[str, dict[str, Any]] = {}
    total = 0
    for row in rows:
        project = str(row["project_id"])
        entry = by_project.setdefault(
            project,
            {"project_id": project, "artifact_count": 0, "types": {}},
        )
        count = int(row["cnt"])
        entry["artifact_count"] += count
        entry["types"][str(row["artifact_type"])] = count
        total += count

    return {
        "scope": "global",
        "count": total,
        "projects": list(by_project.values()),
        "redacted": True,
        "paths_hidden": True,
    }


def build_artifact_list_payload(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    project_id: str,
    task_id: str | None = None,
    artifact_type: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    artifacts = list_warehouse_artifacts(
        conn,
        project_id=project_id,
        task_id=task_id,
        artifact_type=artifact_type,
        limit=limit,
    )
    return {
        "project_id": project_id,
        "count": len(artifacts),
        "artifacts": [
            _redact_mapping(artifact_to_payload(item, paths=paths))
            for item in artifacts
        ],
    }