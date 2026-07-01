"""Agent capability profiles and persistence helpers."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import AgentConfig, CoordinatorConfig

RISK_TIERS = frozenset({"low", "normal", "high"})
REVIEW_STRENGTHS = frozenset({"unknown", "weak", "normal", "strong"})


@dataclass(frozen=True)
class AgentCapabilityProfile:
    agent_id: str
    role: str
    skills: tuple[str, ...]
    risk_tier: str
    review_strength: str
    max_task_minutes: int
    enabled: bool
    updated_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_enum(value: str, allowed: frozenset[str], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"invalid {label}: {value}")
    return value


def validate_risk_tier(value: str) -> str:
    return _validate_enum(value, RISK_TIERS, "risk_tier")


def validate_review_strength(value: str) -> str:
    return _validate_enum(value, REVIEW_STRENGTHS, "review_strength")


def profile_from_agent_config(
    agent: AgentConfig,
    *,
    risk_tier: str = "normal",
    review_strength: str = "unknown",
    max_task_minutes: int = 30,
    enabled: bool = True,
    skills: list[str] | None = None,
) -> AgentCapabilityProfile:
    return AgentCapabilityProfile(
        agent_id=agent.id,
        role=agent.role,
        skills=tuple(skills if skills is not None else list(agent.capabilities)),
        risk_tier=validate_risk_tier(risk_tier),
        review_strength=validate_review_strength(review_strength),
        max_task_minutes=int(max_task_minutes),
        enabled=bool(enabled),
        updated_at=_iso_now(),
    )


def _row_to_profile(row: sqlite3.Row) -> AgentCapabilityProfile:
    skills = json.loads(row["skills_json"])
    return AgentCapabilityProfile(
        agent_id=str(row["agent_id"]),
        role=str(row["role"]),
        skills=tuple(str(item) for item in skills),
        risk_tier=str(row["risk_tier"]),
        review_strength=str(row["review_strength"]),
        max_task_minutes=int(row["max_task_minutes"]),
        enabled=bool(row["enabled"]),
        updated_at=str(row["updated_at"]),
    )


def upsert_capability_profile(
    conn: sqlite3.Connection,
    profile: AgentCapabilityProfile,
    *,
    commit: bool = True,
) -> None:
    validate_risk_tier(profile.risk_tier)
    validate_review_strength(profile.review_strength)
    conn.execute(
        """
        insert into agent_capability_profiles(
            agent_id, role, skills_json, risk_tier, review_strength,
            max_task_minutes, enabled, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(agent_id) do update set
            role = excluded.role,
            skills_json = excluded.skills_json,
            risk_tier = excluded.risk_tier,
            review_strength = excluded.review_strength,
            max_task_minutes = excluded.max_task_minutes,
            enabled = excluded.enabled,
            updated_at = excluded.updated_at
        """,
        (
            profile.agent_id,
            profile.role,
            json.dumps(list(profile.skills)),
            profile.risk_tier,
            profile.review_strength,
            profile.max_task_minutes,
            1 if profile.enabled else 0,
            profile.updated_at,
        ),
    )
    if commit:
        conn.commit()


def get_capability_profile(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
) -> AgentCapabilityProfile | None:
    row = conn.execute(
        "select * from agent_capability_profiles where agent_id = ?",
        (agent_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_profile(row)


def list_capability_profiles(
    conn: sqlite3.Connection,
) -> list[AgentCapabilityProfile]:
    rows = conn.execute(
        "select * from agent_capability_profiles order by agent_id"
    ).fetchall()
    return [_row_to_profile(row) for row in rows]


def _profile_overrides_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if "risk_tier" in raw:
        overrides["risk_tier"] = validate_risk_tier(str(raw["risk_tier"]))
    if "review_strength" in raw:
        overrides["review_strength"] = validate_review_strength(
            str(raw["review_strength"])
        )
    if "max_task_minutes" in raw:
        overrides["max_task_minutes"] = int(raw["max_task_minutes"])
    if "enabled" in raw:
        overrides["enabled"] = bool(raw["enabled"])
    if "skills" in raw:
        skills = raw["skills"]
        if not isinstance(skills, list):
            raise ValueError("skills must be a list")
        overrides["skills"] = [str(item) for item in skills]
    return overrides


def load_capability_profiles(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    *,
    agents_raw: dict[str, dict[str, Any]] | None = None,
    sync: bool = True,
) -> dict[str, AgentCapabilityProfile]:
    """Merge configured agents with durable capability profile overrides."""
    profiles: dict[str, AgentCapabilityProfile] = {}
    for agent_id, agent in config.agents.items():
        overrides = (
            _profile_overrides_from_raw(agents_raw[agent_id])
            if agents_raw and agent_id in agents_raw
            else {}
        )
        stored = get_capability_profile(conn, agent_id=agent_id)
        if stored is not None:
            profiles[agent_id] = stored
            continue
        profiles[agent_id] = profile_from_agent_config(agent, **overrides)
        if sync:
            upsert_capability_profile(conn, profiles[agent_id], commit=False)
    if sync:
        conn.commit()
    return profiles


def sync_capability_profiles_from_config(
    conn: sqlite3.Connection,
    config: CoordinatorConfig,
    *,
    agents_raw: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Persist capability profiles for all configured agents."""
    count = 0
    for agent_id, agent in config.agents.items():
        overrides = (
            _profile_overrides_from_raw(agents_raw[agent_id])
            if agents_raw and agent_id in agents_raw
            else {}
        )
        stored = get_capability_profile(conn, agent_id=agent_id)
        if stored is not None:
            continue
        upsert_capability_profile(
            conn,
            profile_from_agent_config(agent, **overrides),
            commit=False,
        )
        count += 1
    conn.commit()
    return count


def agent_has_skills(profile: AgentCapabilityProfile, required: list[str]) -> bool:
    return set(required).issubset(set(profile.skills))