"""Coordinator config snapshot and rollback helpers."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_runtime import REQUIRED_CONFIG_FILES
from .onboarding_profiles import record_onboarding_run
from .runtime_paths import RuntimePaths


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def create_config_snapshot(
    paths: RuntimePaths,
    conn: sqlite3.Connection,
    *,
    scope: str,
    project_id: str | None,
    reason: str,
) -> str:
    if scope not in {"global", "project"}:
        raise ValueError(f"invalid snapshot scope: {scope}")
    files: dict[str, str] = {}
    for name in REQUIRED_CONFIG_FILES:
        path = paths.config_dir / name
        if path.is_file():
            files[name] = path.read_text(encoding="utf-8")
        else:
            files[name] = ""
    snapshot_id = str(uuid.uuid4())
    conn.execute(
        """
        insert into config_snapshots(
            id, scope, project_id, config_dir, files_json, reason, created_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            scope,
            project_id,
            str(paths.config_dir),
            json.dumps(files),
            reason,
            _iso_now(),
        ),
    )
    return snapshot_id


def rollback_config_snapshot(
    paths: RuntimePaths,
    conn: sqlite3.Connection,
    snapshot_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        "select * from config_snapshots where id = ?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"snapshot not found: {snapshot_id}")
    if str(row["config_dir"]) != str(paths.config_dir):
        raise ValueError("snapshot belongs to a different Coordinator home")

    files = json.loads(row["files_json"])
    if not isinstance(files, dict):
        raise ValueError("snapshot files_json is invalid")

    for name, content in files.items():
        if name not in REQUIRED_CONFIG_FILES:
            continue
        _atomic_write_text(paths.config_dir / name, str(content))

    run_id = record_onboarding_run(
        conn,
        mode="rollback",
        status="rolled_back",
        profile_name="unknown",
        preset_name="observe",
        repo_path=str(paths.config_dir),
        snapshot_id=snapshot_id,
        project_id=row["project_id"],
        applied_json={"restored_files": list(files.keys())},
        finished_at=_iso_now(),
    )
    return {"restored": True, "snapshot_id": snapshot_id, "run_id": run_id}