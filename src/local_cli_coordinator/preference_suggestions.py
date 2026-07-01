"""Generate evidence-backed suggested preference rules from observations."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any

from .preference_rules import (
    PreferenceRule,
    create_rule,
    list_observations,
    list_rules,
)


def _existing_suggestion_key(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    rule_type: str,
    rule: dict[str, Any],
) -> bool:
    for existing in list_rules(conn, project_id=project_id, status="suggested"):
        if existing.rule_type != rule_type:
            continue
        if existing.rule == rule:
            return True
    return False


def _upsert_suggested_rule(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    rule_type: str,
    rule: dict[str, Any],
    evidence_ids: list[str],
    priority: int = 0,
) -> PreferenceRule | None:
    if _existing_suggestion_key(
        conn,
        project_id=project_id,
        rule_type=rule_type,
        rule=rule,
    ):
        return None
    scope = "project" if project_id else "global"
    return create_rule(
        conn,
        scope=scope,
        project_id=project_id,
        rule_type=rule_type,
        rule=rule,
        status="suggested",
        priority=priority,
        evidence_ids=evidence_ids,
        commit=False,
    )


def _agent_choice_suggestions(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
) -> list[PreferenceRule]:
    observations = list_observations(
        conn,
        project_id=project_id,
        observation_type="route_override",
        limit=200,
    )
    if len(observations) < 2:
        return []
    counts = Counter(obs.subject for obs in observations)
    created: list[PreferenceRule] = []
    for agent_id, count in counts.items():
        if count < 2:
            continue
        evidence_ids = [obs.id for obs in observations if obs.subject == agent_id]
        rule = {
            "preferred_agent_id": agent_id,
            "score_bonus": 15.0,
            "reason": f"operator routed to {agent_id} {count} times",
        }
        suggested = _upsert_suggested_rule(
            conn,
            project_id=project_id,
            rule_type="agent_choice",
            rule=rule,
            evidence_ids=evidence_ids[:10],
            priority=count,
        )
        if suggested is not None:
            created.append(suggested)
    return created


def _task_style_suggestions(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
) -> list[PreferenceRule]:
    created: list[PreferenceRule] = []
    approvals = list_observations(
        conn,
        project_id=project_id,
        observation_type="approval",
        limit=200,
    )
    small_task_approvals = [
        obs
        for obs in approvals
        if any(
            signal in str(obs.evidence.get("title", "")).lower()
            for signal in ("read-only", "readonly", "small", "tiny", "docs")
        )
    ]
    if len(small_task_approvals) >= 2:
        rule = {
            "prefer_small_tasks": True,
            "reason": "operator repeatedly approved small or read-only tasks",
        }
        suggested = _upsert_suggested_rule(
            conn,
            project_id=project_id,
            rule_type="task_style",
            rule=rule,
            evidence_ids=[obs.id for obs in small_task_approvals[:10]],
            priority=len(small_task_approvals),
        )
        if suggested is not None:
            created.append(suggested)

    rejections = list_observations(
        conn,
        project_id=project_id,
        observation_type="rejection",
        limit=200,
    )
    vague_rejections = [
        obs
        for obs in rejections
        if any(
            signal in json.dumps(obs.evidence).lower()
            for signal in ("vague", "broad", "unclear", "maybe", "investigate")
        )
    ]
    if len(vague_rejections) >= 2:
        rule = {
            "reject_vague_tasks": True,
            "reason": "operator repeatedly rejected vague or broad tasks",
        }
        suggested = _upsert_suggested_rule(
            conn,
            project_id=project_id,
            rule_type="task_style",
            rule=rule,
            evidence_ids=[obs.id for obs in vague_rejections[:10]],
            priority=len(vague_rejections),
        )
        if suggested is not None:
            created.append(suggested)
    return created


def _command_suggestions(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
) -> list[PreferenceRule]:
    observations = list_observations(
        conn,
        project_id=project_id,
        observation_type="command",
        limit=200,
    )
    if len(observations) < 2:
        return []
    counts = Counter(obs.subject for obs in observations)
    created: list[PreferenceRule] = []
    for command, count in counts.items():
        if count < 2:
            continue
        evidence_ids = [obs.id for obs in observations if obs.subject == command]
        rule = {
            "preferred_command": command,
            "reason": f"operator ran {command} {count} times",
        }
        suggested = _upsert_suggested_rule(
            conn,
            project_id=project_id,
            rule_type="schedule_preference",
            rule=rule,
            evidence_ids=evidence_ids[:10],
            priority=count,
        )
        if suggested is not None:
            created.append(suggested)
    return created


def refresh_suggestions_from_observations(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    commit: bool = False,
) -> list[PreferenceRule]:
    created: list[PreferenceRule] = []
    created.extend(_agent_choice_suggestions(conn, project_id=project_id))
    created.extend(_task_style_suggestions(conn, project_id=project_id))
    created.extend(_command_suggestions(conn, project_id=project_id))
    if commit:
        conn.commit()
    return created