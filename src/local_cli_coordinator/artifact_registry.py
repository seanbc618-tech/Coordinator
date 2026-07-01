"""Phase 18 artifact registry with canonical paths and checksums."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .runtime_paths import RuntimePaths

ARTIFACT_TYPES = frozenset({
    "log",
    "patch",
    "review_packet",
    "pr_evidence",
    "command_output",
    "summary",
    "export",
})
REDACTION_STATUSES = frozenset({"unknown", "clean", "redacted", "blocked"})

TASK_KIND_TO_ARTIFACT_TYPE = {
    "diff": "patch",
    "attempt_log": "log",
    "agent_log": "log",
    "verifier_log": "log",
    "spec_review_log": "log",
    "quality_review_log": "log",
    "review_packet": "review_packet",
    "worktree_prompt": "summary",
    "command_output": "command_output",
    "pr_evidence": "pr_evidence",
}


class ArtifactRegistryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class WarehouseArtifact:
    id: str
    project_id: str
    task_id: str | None
    run_id: str | None
    artifact_type: str
    path: str
    sha256: str
    size_bytes: int
    redaction_status: str
    provenance: dict[str, Any]
    created_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_warehouse_paths() -> RuntimePaths | None:
    home = os.environ.get("COORDINATOR_HOME")
    if not home:
        return None
    base = Path(home)
    return RuntimePaths(base / "config", base / "data", base / "state")


def allowed_artifact_roots(
    paths: RuntimePaths,
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> list[Path]:
    roots = [paths.data_dir.resolve(), paths.state_dir.resolve()]
    row = conn.execute(
        "select canonical_path from projects where id = ?",
        (project_id,),
    ).fetchone()
    if row is not None and row["canonical_path"]:
        roots.append(Path(str(row["canonical_path"])).resolve())
    return roots


def canonicalize_artifact_path(
    raw_path: Path | str,
    *,
    allowed_roots: Sequence[Path],
) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise ArtifactRegistryError(
            "path_not_absolute",
            f"artifact path must be absolute: {raw_path!r}",
        )
    resolved = candidate.resolve()
    for root in allowed_roots:
        root_resolved = root.resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved
    raise ArtifactRegistryError(
        "path_outside_roots",
        f"artifact path {resolved} is outside coordinator-controlled directories",
    )


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _row_to_artifact(row: sqlite3.Row) -> WarehouseArtifact:
    return WarehouseArtifact(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        task_id=str(row["task_id"]) if row["task_id"] else None,
        run_id=str(row["run_id"]) if row["run_id"] else None,
        artifact_type=str(row["artifact_type"]),
        path=str(row["path"]),
        sha256=str(row["sha256"]),
        size_bytes=int(row["size_bytes"]),
        redaction_status=str(row["redaction_status"]),
        provenance=json.loads(row["provenance_json"]),
        created_at=str(row["created_at"]),
    )


def register_artifact(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    project_id: str,
    artifact_type: str,
    path: Path | str,
    task_id: str | None = None,
    run_id: str | None = None,
    provenance: Mapping[str, Any] | None = None,
    redaction_status: str = "unknown",
    commit: bool = True,
) -> WarehouseArtifact:
    if artifact_type not in ARTIFACT_TYPES:
        raise ArtifactRegistryError(
            "invalid_artifact_type",
            f"unsupported artifact type: {artifact_type!r}",
        )
    if redaction_status not in REDACTION_STATUSES:
        raise ArtifactRegistryError(
            "invalid_redaction_status",
            f"unsupported redaction status: {redaction_status!r}",
        )

    roots = allowed_artifact_roots(paths, conn, project_id=project_id)
    canonical = canonicalize_artifact_path(path, allowed_roots=roots)
    if not canonical.is_file():
        raise ArtifactRegistryError(
            "artifact_missing",
            f"artifact file does not exist: {canonical}",
        )

    sha256, size_bytes = _sha256_file(canonical)
    canonical_str = str(canonical)
    existing = conn.execute(
        """
        select * from artifacts
        where project_id = ? and path = ?
        order by created_at desc
        limit 1
        """,
        (project_id, canonical_str),
    ).fetchone()
    provenance_json = json.dumps(dict(provenance or {}))
    if existing is not None:
        conn.execute(
            """
            update artifacts
            set task_id = ?, run_id = ?, artifact_type = ?, sha256 = ?,
                size_bytes = ?, redaction_status = ?, provenance_json = ?
            where id = ?
            """,
            (
                task_id,
                run_id,
                artifact_type,
                sha256,
                size_bytes,
                redaction_status,
                provenance_json,
                existing["id"],
            ),
        )
        artifact_id = str(existing["id"])
    else:
        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            insert into artifacts(
                id, project_id, task_id, run_id, artifact_type, path,
                sha256, size_bytes, redaction_status, provenance_json, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                project_id,
                task_id,
                run_id,
                artifact_type,
                canonical_str,
                sha256,
                size_bytes,
                redaction_status,
                provenance_json,
                _iso_now(),
            ),
        )

    if commit:
        conn.commit()
    row = conn.execute(
        "select * from artifacts where id = ?",
        (artifact_id,),
    ).fetchone()
    assert row is not None
    return _row_to_artifact(row)


def register_task_kind_artifact(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    project_id: str,
    task_id: str,
    kind: str,
    path: Path | str,
    provenance: Mapping[str, Any] | None = None,
    commit: bool = False,
) -> WarehouseArtifact | None:
    artifact_type = TASK_KIND_TO_ARTIFACT_TYPE.get(kind)
    if artifact_type is None:
        return None
    try:
        return register_artifact(
            conn,
            paths=paths,
            project_id=project_id,
            artifact_type=artifact_type,
            path=path,
            task_id=task_id,
            provenance={"source": "task_artifact", "kind": kind, **dict(provenance or {})},
            commit=commit,
        )
    except ArtifactRegistryError:
        return None


def list_warehouse_artifacts(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str | None = None,
    artifact_type: str | None = None,
    limit: int = 100,
) -> list[WarehouseArtifact]:
    query = "select * from artifacts where project_id = ?"
    params: list[Any] = [project_id]
    if task_id is not None:
        query += " and task_id = ?"
        params.append(task_id)
    if artifact_type is not None:
        query += " and artifact_type = ?"
        params.append(artifact_type)
    query += " order by created_at desc, id desc limit ?"
    params.append(max(1, limit))
    rows = conn.execute(query, params).fetchall()
    return [_row_to_artifact(row) for row in rows]


def get_warehouse_artifact(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    project_id: str | None = None,
) -> WarehouseArtifact | None:
    if project_id is None:
        row = conn.execute(
            "select * from artifacts where id = ?",
            (artifact_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "select * from artifacts where id = ? and project_id = ?",
            (artifact_id, project_id),
        ).fetchone()
    if row is None:
        return None
    return _row_to_artifact(row)


def artifact_to_payload(
    artifact: WarehouseArtifact,
    *,
    paths: RuntimePaths | None = None,
    redact_paths: bool = False,
) -> dict[str, Any]:
    display_path = artifact.path
    if paths is not None:
        data_root = paths.data_dir.resolve()
        try:
            display_path = str(Path(artifact.path).resolve().relative_to(data_root))
        except ValueError:
            display_path = artifact.path
    if redact_paths:
        display_path = Path(display_path).name
    return {
        "id": artifact.id,
        "project_id": artifact.project_id,
        "task_id": artifact.task_id,
        "run_id": artifact.run_id,
        "artifact_type": artifact.artifact_type,
        "path": display_path,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
        "redaction_status": artifact.redaction_status,
        "provenance": artifact.provenance,
        "created_at": artifact.created_at,
    }