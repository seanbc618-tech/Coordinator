"""Observe user decisions and repeated patterns for preference learning."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from .preference_rules import PreferenceObservation, record_observation
from .preference_suggestions import refresh_suggestions_from_observations


def observe_decision(
    conn: sqlite3.Connection,
    *,
    observation_type: str,
    subject: str,
    evidence: Mapping[str, Any] | None = None,
    project_id: str | None = None,
    suggest: bool = True,
    commit: bool = False,
) -> PreferenceObservation:
    observation = record_observation(
        conn,
        observation_type=observation_type,
        subject=subject,
        evidence=evidence,
        project_id=project_id,
        commit=False,
    )
    if suggest:
        refresh_suggestions_from_observations(
            conn,
            project_id=project_id,
            commit=False,
        )
    if commit:
        conn.commit()
    return observation


def observe_task_approval(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    title: str = "",
    commit: bool = False,
) -> PreferenceObservation:
    return observe_decision(
        conn,
        observation_type="approval",
        subject=task_id,
        evidence={"task_id": task_id, "title": title, "action": "approve"},
        project_id=project_id,
        commit=commit,
    )


def observe_task_rejection(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    reason: str = "",
    commit: bool = False,
) -> PreferenceObservation:
    return observe_decision(
        conn,
        observation_type="rejection",
        subject=task_id,
        evidence={"task_id": task_id, "reason": reason, "action": "reject"},
        project_id=project_id,
        commit=commit,
    )


def observe_task_retry(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    commit: bool = False,
) -> PreferenceObservation:
    return observe_decision(
        conn,
        observation_type="retry",
        subject=task_id,
        evidence={"task_id": task_id, "action": "retry"},
        project_id=project_id,
        commit=commit,
    )


def observe_command_pattern(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    command: str,
    args: str = "",
    commit: bool = False,
) -> PreferenceObservation:
    return observe_decision(
        conn,
        observation_type="command",
        subject=command,
        evidence={"command": command, "args": args},
        project_id=project_id,
        commit=commit,
    )


def observe_route_override(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    selected_agent_id: str,
    reason: str = "",
    commit: bool = False,
) -> PreferenceObservation:
    return observe_decision(
        conn,
        observation_type="route_override",
        subject=selected_agent_id,
        evidence={
            "task_id": task_id,
            "selected_agent_id": selected_agent_id,
            "reason": reason,
        },
        project_id=project_id,
        commit=commit,
    )


def observe_task_edit(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    field: str,
    old_value: str,
    new_value: str,
    commit: bool = False,
) -> PreferenceObservation:
    return observe_decision(
        conn,
        observation_type="edit",
        subject=task_id,
        evidence={
            "task_id": task_id,
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
        },
        project_id=project_id,
        commit=commit,
    )