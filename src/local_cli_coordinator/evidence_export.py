"""Export redacted evidence bundles with manifests and checksums."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_registry import list_warehouse_artifacts
from .project_indexer import redact_text
from .runtime_paths import RuntimePaths

_EXPORT_SCOPES = frozenset({"project", "global", "task"})
_EXPORT_STATUSES = frozenset({"created", "failed"})
_TEXT_SUFFIXES = frozenset({
    ".txt",
    ".log",
    ".md",
    ".json",
    ".patch",
    ".diff",
    ".yaml",
    ".yml",
})


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative_display_path(path: Path, data_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(data_dir.resolve()))
    except ValueError:
        return str(path)


def _copy_redacted_file(source: Path, destination: Path) -> tuple[str, int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    redaction_count = 0
    if source.suffix.lower() in _TEXT_SUFFIXES:
        text = source.read_text(encoding="utf-8", errors="replace")
        redacted = redact_text(text)
        redaction_count = redacted.count("[REDACTED]")
        destination.write_text(redacted, encoding="utf-8")
        payload = redacted.encode("utf-8")
    else:
        shutil.copy2(source, destination)
        payload = destination.read_bytes()
    return _sha256_bytes(payload), len(payload), redaction_count


def export_evidence_bundle(
    conn: sqlite3.Connection,
    *,
    paths: RuntimePaths,
    scope: str = "project",
    project_id: str | None = None,
    task_id: str | None = None,
    limit: int = 500,
    commit: bool = True,
) -> dict[str, Any]:
    if scope not in _EXPORT_SCOPES:
        raise ValueError(f"unsupported export scope: {scope!r}")
    if scope in {"project", "task"} and not project_id:
        raise ValueError("project_id is required for project/task export")
    if scope == "task" and not task_id:
        raise ValueError("task_id is required for task export")

    export_id = f"exp-{uuid.uuid4().hex[:12]}"
    bundle_root = paths.data_dir / "exports" / export_id
    files_dir = bundle_root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    artifacts = []
    if scope == "global":
        rows = conn.execute(
            """
            select * from artifacts
            order by created_at desc, id desc
            limit ?
            """,
            (max(1, limit),),
        ).fetchall()
        from .artifact_registry import WarehouseArtifact

        for row in rows:
            artifacts.append(
                WarehouseArtifact(
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
            )
    else:
        artifacts = list_warehouse_artifacts(
            conn,
            project_id=project_id or "",
            task_id=task_id,
            limit=limit,
        )

    manifest_files: list[dict[str, Any]] = []
    total_redactions = 0
    status = "created"
    try:
        for artifact in artifacts:
            source = Path(artifact.path)
            if not source.is_file():
                continue
            dest_name = f"{artifact.id}_{source.name}"
            destination = files_dir / dest_name
            bundle_sha, bundle_size, redactions = _copy_redacted_file(source, destination)
            total_redactions += redactions
            manifest_files.append(
                {
                    "artifact_id": artifact.id,
                    "project_id": artifact.project_id,
                    "task_id": artifact.task_id,
                    "artifact_type": artifact.artifact_type,
                    "source_path": _relative_display_path(source, paths.data_dir),
                    "bundle_path": str(Path("files") / dest_name),
                    "sha256": bundle_sha,
                    "size_bytes": bundle_size,
                    "redaction_status": "redacted" if redactions else artifact.redaction_status,
                }
            )
    except OSError as exc:
        status = "failed"
        raise RuntimeError(f"evidence export failed: {exc}") from exc

    manifest = {
        "export_id": export_id,
        "project_id": project_id,
        "scope": scope,
        "task_id": task_id,
        "created_at": _iso_now(),
        "file_count": len(manifest_files),
        "files": manifest_files,
        "redaction_summary": {
            "redacted_values": total_redactions,
            "policy": "secret_token_password_api_key",
        },
    }
    manifest_path = bundle_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    conn.execute(
        """
        insert into evidence_exports(
            id, project_id, scope, status, bundle_path, manifest_json, created_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            export_id,
            project_id,
            scope,
            status,
            str(bundle_root),
            json.dumps(manifest),
            manifest["created_at"],
        ),
    )
    if commit:
        conn.commit()

    return {
        "export_id": export_id,
        "scope": scope,
        "project_id": project_id,
        "task_id": task_id,
        "status": status,
        "bundle_path": str(bundle_root),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
    }