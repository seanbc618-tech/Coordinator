"""Onboarding profile presets and persistence helpers."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .project_inspector import ProjectInspection

DEFAULT_PRESET = "observe"

DETECTED_PROFILES = frozenset({"python", "node", "mixed", "docs", "unknown"})
PRESETS = frozenset({"observe", "assist", "managed", "overnight", "delivery"})
ONBOARDING_MODES = frozenset({"inspect", "dry_run", "apply", "rollback"})
ONBOARDING_STATUSES = frozenset({"planned", "applied", "rolled_back", "skipped", "failed"})
SNAPSHOT_SCOPES = frozenset({"global", "project"})
FLEET_MODES = frozenset({"scan", "dry_run", "apply"})
FLEET_STATUSES = frozenset({"planned", "applied", "partial", "failed"})

_PRESET_DELTAS: dict[str, dict[str, bool]] = {
    "observe": {
        "autonomy_enabled": False,
        "allow_push": False,
        "allow_task_execution": False,
        "allow_chat": False,
        "allow_autonomous_loop": False,
        "require_human_review_before_push": True,
        "auto_merge": False,
        "allow_push_without_confirmation": False,
    },
    "assist": {
        "autonomy_enabled": False,
        "allow_push": False,
        "allow_task_execution": False,
        "allow_chat": True,
        "allow_autonomous_loop": False,
        "require_human_review_before_push": True,
        "auto_merge": False,
        "allow_push_without_confirmation": False,
    },
    "managed": {
        "autonomy_enabled": False,
        "allow_push": False,
        "allow_task_execution": True,
        "allow_chat": True,
        "allow_autonomous_loop": False,
        "require_human_review_before_push": True,
        "auto_merge": False,
        "allow_push_without_confirmation": False,
    },
    "overnight": {
        "autonomy_enabled": False,
        "allow_push": False,
        "allow_task_execution": True,
        "allow_chat": True,
        "allow_autonomous_loop": True,
        "require_human_review_before_push": True,
        "auto_merge": False,
        "allow_push_without_confirmation": False,
    },
    "delivery": {
        "autonomy_enabled": False,
        "allow_push": False,
        "allow_task_execution": True,
        "allow_chat": True,
        "allow_autonomous_loop": False,
        "require_human_review_before_push": True,
        "auto_merge": False,
        "allow_push_without_confirmation": False,
    },
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_enum(value: str, allowed: frozenset[str], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"invalid {label}: {value}")
    return value


def validate_detected_profile(name: str) -> str:
    return _validate_enum(name, DETECTED_PROFILES, "detected_profile")


def validate_preset(name: str) -> str:
    return _validate_enum(name, PRESETS, "preset")


def preset_policy_delta(preset: str) -> dict[str, bool]:
    validate_preset(preset)
    return dict(_PRESET_DELTAS[preset])


def preset_enables_autonomy(preset: str, *, enable_autonomy: bool = False) -> bool:
    validate_preset(preset)
    if preset not in {"overnight", "delivery"}:
        return False
    return bool(enable_autonomy)


def resolve_autonomy_enabled(
    preset: str,
    *,
    enable_autonomy: bool = False,
) -> bool:
    return preset_enables_autonomy(preset, enable_autonomy=enable_autonomy)


def record_profile_run(
    conn: sqlite3.Connection,
    *,
    repo_path: str,
    inspection: ProjectInspection,
    project_id: str | None = None,
) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        """
        insert into project_profile_runs(
            id, project_id, repo_path, detected_profile, recommended_preset,
            confidence, findings_json, verify_commands_json, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            project_id,
            repo_path,
            validate_detected_profile(inspection.detected_profile),
            validate_preset(inspection.recommended_preset),
            float(inspection.confidence),
            json.dumps(inspection.findings),
            json.dumps(inspection.verify_commands),
            _iso_now(),
        ),
    )
    return run_id


def record_onboarding_run(
    conn: sqlite3.Connection,
    *,
    mode: str,
    status: str,
    profile_name: str,
    preset_name: str,
    repo_path: str,
    plan_json: dict[str, Any] | None = None,
    applied_json: dict[str, Any] | None = None,
    snapshot_id: str | None = None,
    project_id: str | None = None,
    error: str = "",
    finished_at: str | None = None,
) -> str:
    run_id = str(uuid.uuid4())
    _validate_enum(mode, ONBOARDING_MODES, "onboarding mode")
    _validate_enum(status, ONBOARDING_STATUSES, "onboarding status")
    validate_detected_profile(profile_name)
    validate_preset(preset_name)
    conn.execute(
        """
        insert into onboarding_runs(
            id, mode, status, profile_name, preset_name, project_id, repo_path,
            plan_json, applied_json, snapshot_id, error, created_at, finished_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            mode,
            status,
            profile_name,
            preset_name,
            project_id,
            repo_path,
            json.dumps(plan_json or {}),
            json.dumps(applied_json or {}),
            snapshot_id,
            error,
            _iso_now(),
            finished_at,
        ),
    )
    return run_id