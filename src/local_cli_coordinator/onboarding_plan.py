"""Dry-run and apply planning for project onboarding."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config_snapshots import create_config_snapshot, rollback_config_snapshot
from .init_project import (
    _read_repo_sections,
    apply_init_plan,
    build_init_plan,
    derive_repo_id,
)
from .onboarding_profiles import (
    record_onboarding_run,
    record_profile_run,
    resolve_autonomy_enabled,
    validate_preset,
)
from .project_inspector import inspect_project_shape
from .projects import inspect_project, register_project
from .runtime_paths import RuntimePaths


def build_onboarding_plan(
    paths: RuntimePaths,
    conn: sqlite3.Connection,
    repo_path: Path,
    *,
    preset: str = "observe",
    dry_run: bool = True,
    enable_autonomy: bool = False,
) -> dict[str, Any]:
    validate_preset(preset)
    inspection = inspect_project_shape(repo_path)
    autonomy_enabled = resolve_autonomy_enabled(preset, enable_autonomy=enable_autonomy)
    record_profile_run(
        conn,
        repo_path=str(inspection.repo_root),
        inspection=inspection,
    )

    init_plan = build_init_plan(
        paths,
        repo_root=inspection.repo_root,
        repo_id=inspection.repo_id,
        verify_commands=list(inspection.verify_commands),
        autonomy_enabled=autonomy_enabled,
    )
    repos_spec = init_plan["files"]["repos.toml"]
    current_repo_entry = _current_repo_entry(paths, inspection.repo_id)
    proposed_repo_entry = repos_spec.get("entry", {})
    config_diff = _build_config_diff(paths, init_plan)
    warnings = _build_warnings(preset, autonomy_enabled)

    plan = {
        "preset": preset,
        "autonomy_enabled": autonomy_enabled,
        "detected_profile": inspection.detected_profile,
        "recommended_preset": inspection.recommended_preset,
        "repo_entry": proposed_repo_entry,
        "current_repo_entry": current_repo_entry,
        "proposed_repo_entry": proposed_repo_entry,
        "config_diff": config_diff,
        "verify_commands": list(inspection.verify_commands),
        "warnings": warnings,
        "repo_id": inspection.repo_id,
        "repo_root": str(inspection.repo_root),
    }

    run_id = record_onboarding_run(
        conn,
        mode="dry_run" if dry_run else "apply",
        status="planned",
        profile_name=inspection.detected_profile,
        preset_name=preset,
        repo_path=str(inspection.repo_root),
        plan_json=plan,
    )
    plan["run_id"] = run_id
    return plan


def apply_onboarding_plan(
    paths: RuntimePaths,
    conn: sqlite3.Connection,
    repo_path: Path,
    *,
    preset: str = "observe",
    enable_autonomy: bool = False,
    allow_delivery_policy_change: bool = False,
) -> dict[str, Any]:
    validate_preset(preset)
    plan = build_onboarding_plan(
        paths,
        conn,
        repo_path,
        preset=preset,
        dry_run=False,
        enable_autonomy=enable_autonomy,
    )
    snapshot_id = create_config_snapshot(
        paths,
        conn,
        scope="global",
        project_id=None,
        reason="onboarding apply",
    )
    init_plan = build_init_plan(
        paths,
        repo_root=Path(plan["repo_root"]),
        repo_id=plan["repo_id"],
        verify_commands=list(plan["verify_commands"]),
        autonomy_enabled=plan["autonomy_enabled"],
    )
    try:
        apply_init_plan(paths, init_plan)
        draft = inspect_project(Path(repo_path))
        project_id = register_project(conn, draft, confirmed=True)
    except Exception as exc:
        rollback_config_snapshot(paths, conn, snapshot_id)
        record_onboarding_run(
            conn,
            mode="apply",
            status="failed",
            profile_name=plan["detected_profile"],
            preset_name=preset,
            repo_path=plan["repo_root"],
            snapshot_id=snapshot_id,
            error=str(exc),
        )
        raise

    from .onboarding_brain import store_onboarding_profile_memory

    store_onboarding_profile_memory(conn, project_id=project_id, plan=plan)

    run_id = record_onboarding_run(
        conn,
        mode="apply",
        status="applied",
        profile_name=plan["detected_profile"],
        preset_name=preset,
        repo_path=plan["repo_root"],
        project_id=project_id,
        snapshot_id=snapshot_id,
        plan_json=plan,
        applied_json={
            "repo_id": plan["repo_id"],
            "autonomy_enabled": plan["autonomy_enabled"],
            "allow_delivery_policy_change": allow_delivery_policy_change,
        },
    )
    return {
        "snapshot_id": snapshot_id,
        "run_id": run_id,
        "project_id": project_id,
        "autonomy_enabled": plan["autonomy_enabled"],
        "repo_id": plan["repo_id"],
        "preset": preset,
    }


def _current_repo_entry(paths: RuntimePaths, repo_id: str) -> dict[str, Any] | None:
    repos = _read_repo_sections(paths.config_dir / "repos.toml")
    entry = repos.get(repo_id)
    return dict(entry) if entry is not None else None


def _build_config_diff(paths: RuntimePaths, init_plan: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for name, spec in init_plan["files"].items():
        path = paths.config_dir / name
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        proposed = spec.get("content", current)
        if spec.get("action") == "preserve":
            diff[name] = {"action": "preserve", "reason": spec.get("reason", "unchanged")}
        elif current != proposed:
            diff[name] = {"action": spec.get("action", "update"), "changed": True}
        else:
            diff[name] = {"action": "unchanged", "changed": False}
    return diff


def _build_warnings(preset: str, autonomy_enabled: bool) -> list[str]:
    warnings: list[str] = []
    if preset == "observe":
        warnings.append("Default observe preset keeps autonomy disabled.")
    if preset in {"overnight", "delivery"} and not autonomy_enabled:
        warnings.append(
            f"{preset} preset requires --enable-autonomy to turn on autonomous loops."
        )
    return warnings