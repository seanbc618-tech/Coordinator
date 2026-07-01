"""Store onboarding profile findings in project brain without secrets."""

from __future__ import annotations

import sqlite3
from typing import Any

from .project_brain import upsert_brain_memory


def store_onboarding_profile_memory(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    plan: dict[str, Any],
) -> None:
    profile = str(plan.get("detected_profile", "unknown"))
    preset = str(plan.get("preset", "observe"))
    verify_commands = plan.get("verify_commands") or []
    upsert_brain_memory(
        conn,
        project_id=project_id,
        source_type="onboarding",
        source_id=str(plan.get("run_id", "onboarding")),
        memory_type="onboarding",
        title=f"Onboarding profile: {profile}",
        summary=(
            f"Detected {profile} repository with preset {preset}. "
            f"Suggested verify commands: {len(verify_commands)}."
        ),
        data={
            "detected_profile": profile,
            "preset": preset,
            "verify_commands": list(verify_commands),
            "autonomy_enabled": bool(plan.get("autonomy_enabled")),
        },
        status="active",
    )