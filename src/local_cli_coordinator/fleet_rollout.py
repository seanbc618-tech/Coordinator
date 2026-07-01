"""Fleet scan and selective onboarding rollout."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .init_project import derive_repo_id
from .onboarding_plan import apply_onboarding_plan
from .onboarding_profiles import record_onboarding_run
from .project_inspector import inspect_project_shape
from .projects import inspect_project
from .runtime_paths import RuntimePaths

SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    ".venv",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_hidden_cache_dir(name: str) -> bool:
    return name.startswith(".") and name not in {".git"}


def _normalize_select_slug(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _repo_select_aliases(repo_root: Path) -> set[str]:
    repo_id = derive_repo_id(repo_root)
    aliases = {
        repo_id,
        repo_id.replace("_", "-"),
        repo_root.name,
        repo_root.name.lower(),
    }
    return {item.lower() for item in aliases}


def _discover_git_roots(root: Path, *, max_depth: int) -> list[Path]:
    discovered: list[Path] = []
    root = root.resolve()

    def walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(current.iterdir())
        except OSError:
            return
        if (current / ".git").is_dir():
            discovered.append(current.resolve())
            return
        for child in children:
            if not child.is_dir():
                continue
            name = child.name
            if name in SKIP_DIR_NAMES or _is_hidden_cache_dir(name):
                continue
            walk(child, depth + 1)

    walk(root, 0)
    return _dedupe_nested_roots(discovered)


def _dedupe_nested_roots(roots: list[Path]) -> list[Path]:
    unique = sorted(set(roots), key=lambda path: (len(path.parts), str(path)))
    kept: list[Path] = []
    for candidate in unique:
        if any(
            candidate != other and str(candidate).startswith(str(other) + "/")
            for other in unique
        ):
            continue
        kept.append(candidate)
    return kept


def _is_registered(conn: sqlite3.Connection | None, repo_root: Path) -> bool:
    if conn is None:
        return False
    canonical = str(repo_root.resolve())
    row = conn.execute(
        "select id from projects where canonical_path = ?",
        (canonical,),
    ).fetchone()
    return row is not None


def scan_fleet(
    root: Path,
    *,
    max_depth: int = 3,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    repos: list[dict[str, Any]] = []
    for repo_root in _discover_git_roots(root, max_depth=max_depth):
        inspection = inspect_project_shape(repo_root)
        repos.append(
            {
                "repo_id": inspection.repo_id,
                "repo_root": str(repo_root),
                "detected_profile": inspection.detected_profile,
                "recommended_preset": inspection.recommended_preset,
                "registered": _is_registered(conn, repo_root),
                "verify_commands": list(inspection.verify_commands),
            }
        )
    return {"root": str(root.resolve()), "repos": repos}


def apply_fleet_rollout(
    paths: RuntimePaths,
    conn: sqlite3.Connection,
    root: Path,
    *,
    preset: str = "observe",
    select: list[str] | None = None,
    enable_autonomy: bool = False,
) -> dict[str, Any]:
    scan = scan_fleet(root, conn=conn)
    selected = {_normalize_select_slug(item) for item in (select or [])}
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for entry in scan["repos"]:
        aliases = _repo_select_aliases(Path(entry["repo_root"]))
        normalized_aliases = {_normalize_select_slug(item) for item in aliases}
        slug = entry["repo_id"].replace("_", "-")
        if selected and not (selected & normalized_aliases):
            skipped.append({"repo_id": slug, "reason": "not_selected"})
            continue
        result = apply_onboarding_plan(
            paths,
            conn,
            Path(entry["repo_root"]),
            preset=preset,
            enable_autonomy=enable_autonomy,
        )
        applied.append({"repo_id": slug, "project_id": result["project_id"]})

    run_id = record_onboarding_run(
        conn,
        mode="apply",
        status="applied" if applied else "skipped",
        profile_name="unknown",
        preset_name=preset,
        repo_path=str(root.resolve()),
        applied_json={"applied": applied, "skipped": skipped},
    )
    fleet_id = str(uuid.uuid4())
    conn.execute(
        """
        insert into fleet_rollout_runs(
            id, root_path, mode, status, discovered_json, applied_json,
            skipped_json, created_at, finished_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fleet_id,
            str(root.resolve()),
            "apply",
            "applied" if applied else "partial",
            json.dumps(scan["repos"]),
            json.dumps(applied),
            json.dumps(skipped),
            _iso_now(),
            _iso_now(),
        ),
    )
    return {
        "run_id": run_id,
        "fleet_run_id": fleet_id,
        "applied": applied,
        "skipped": skipped,
    }