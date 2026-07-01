"""Durable preference observations and editable rules with lifecycle management."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

OBSERVATION_TYPES = frozenset({
    "approval",
    "rejection",
    "retry",
    "edit",
    "command",
    "route_override",
})
SCOPES = frozenset({"global", "project"})
RULE_TYPES = frozenset({
    "task_style",
    "agent_choice",
    "risk_preference",
    "review_preference",
    "schedule_preference",
})
RULE_STATUSES = frozenset({
    "suggested",
    "active",
    "rejected",
    "disabled",
    "deleted",
})

_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|token)\s*[=:]\s*)(\S+)"
)

_FORBIDDEN_RULE_KEYS = frozenset({
    "allow_push",
    "autonomy_enabled",
    "allow_task_execution",
    "allow_chat",
    "allow_autonomous_loop",
    "auto_merge",
    "allow_push_without_confirmation",
    "permissions",
    "merge_policy",
    "review_policy",
    "bypass_approval",
    "bypass_review",
    "bypass_policy",
    "enable_autonomy",
    "grant_capability",
    "add_repo",
})


@dataclass(frozen=True)
class PreferenceObservation:
    id: str
    project_id: str | None
    observation_type: str
    subject: str
    evidence: dict[str, Any]
    redaction_status: str
    created_at: str


@dataclass(frozen=True)
class PreferenceRule:
    id: str
    scope: str
    project_id: str | None
    rule_type: str
    status: str
    priority: int
    rule: dict[str, Any]
    evidence_ids: list[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PreferenceHint:
    rule_id: str
    rule_type: str
    message: str
    score_delta: float = 0.0
    agent_id: str | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_enum(value: str, allowed: frozenset[str], label: str) -> str:
    text = value.strip()
    if text not in allowed:
        raise ValueError(f"invalid {label}: {value!r}")
    return text


def redact_evidence_value(value: Any) -> tuple[Any, bool]:
    """Redact secret-looking strings in evidence payloads."""
    redacted = False
    if isinstance(value, str):
        new_text = _SECRET_RE.sub(r"\1[REDACTED]", value)
        return new_text, new_text != value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            cleaned, item_redacted = redact_evidence_value(item)
            result[str(key)] = cleaned
            redacted = redacted or item_redacted
        return result, redacted
    if isinstance(value, list):
        result_list: list[Any] = []
        for item in value:
            cleaned, item_redacted = redact_evidence_value(item)
            result_list.append(cleaned)
            redacted = redacted or item_redacted
        return result_list, redacted
    return value, False


def redact_evidence(evidence: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    cleaned, had_secrets = redact_evidence_value(dict(evidence))
    status = "redacted" if had_secrets else "clean"
    assert isinstance(cleaned, dict)
    return cleaned, status


def validate_rule_payload(rule: Mapping[str, Any]) -> None:
    """Reject rules that attempt permission escalation or policy bypass."""
    for key in rule:
        lowered = key.strip().lower()
        if lowered in _FORBIDDEN_RULE_KEYS:
            raise ValueError(
                f"preference rule cannot set permission or policy key: {key!r}"
            )
        if lowered.startswith("allow_") and lowered not in {
            "allow_vague_tasks",
            "allow_broad_tasks",
        }:
            raise ValueError(
                f"preference rule cannot set permission key: {key!r}"
            )


def _row_observation(row: sqlite3.Row) -> PreferenceObservation:
    return PreferenceObservation(
        id=str(row["id"]),
        project_id=str(row["project_id"]) if row["project_id"] else None,
        observation_type=str(row["observation_type"]),
        subject=str(row["subject"]),
        evidence=json.loads(row["evidence_json"]),
        redaction_status=str(row["redaction_status"]),
        created_at=str(row["created_at"]),
    )


def _row_rule(row: sqlite3.Row) -> PreferenceRule:
    return PreferenceRule(
        id=str(row["id"]),
        scope=str(row["scope"]),
        project_id=str(row["project_id"]) if row["project_id"] else None,
        rule_type=str(row["rule_type"]),
        status=str(row["status"]),
        priority=int(row["priority"]),
        rule=json.loads(row["rule_json"]),
        evidence_ids=json.loads(row["evidence_ids_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _rule_payload(rule: PreferenceRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "scope": rule.scope,
        "project_id": rule.project_id,
        "rule_type": rule.rule_type,
        "status": rule.status,
        "priority": rule.priority,
        "rule": rule.rule,
        "evidence_ids": rule.evidence_ids,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def record_observation(
    conn: sqlite3.Connection,
    *,
    observation_type: str,
    subject: str,
    evidence: Mapping[str, Any] | None = None,
    project_id: str | None = None,
    commit: bool = False,
) -> PreferenceObservation:
    _validate_enum(observation_type, OBSERVATION_TYPES, "observation_type")
    cleaned, redaction_status = redact_evidence(evidence or {})
    obs_id = f"prefobs-{uuid.uuid4().hex[:12]}"
    created_at = _iso_now()
    conn.execute(
        """
        insert into preference_observations(
            id, project_id, observation_type, subject, evidence_json,
            redaction_status, created_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            obs_id,
            project_id,
            observation_type,
            subject,
            json.dumps(cleaned),
            redaction_status,
            created_at,
        ),
    )
    if commit:
        conn.commit()
    return PreferenceObservation(
        id=obs_id,
        project_id=project_id,
        observation_type=observation_type,
        subject=subject,
        evidence=cleaned,
        redaction_status=redaction_status,
        created_at=created_at,
    )


def list_observations(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    observation_type: str | None = None,
    limit: int = 100,
) -> list[PreferenceObservation]:
    clauses: list[str] = []
    params: list[Any] = []
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    if observation_type is not None:
        _validate_enum(observation_type, OBSERVATION_TYPES, "observation_type")
        clauses.append("observation_type = ?")
        params.append(observation_type)
    where = f"where {' and '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        select * from preference_observations
        {where}
        order by created_at desc
        limit ?
        """,
        (*params, limit),
    ).fetchall()
    return [_row_observation(row) for row in rows]


def create_rule(
    conn: sqlite3.Connection,
    *,
    scope: str,
    rule_type: str,
    rule: Mapping[str, Any],
    project_id: str | None = None,
    status: str = "suggested",
    priority: int = 0,
    evidence_ids: list[str] | None = None,
    commit: bool = False,
) -> PreferenceRule:
    _validate_enum(scope, SCOPES, "scope")
    _validate_enum(rule_type, RULE_TYPES, "rule_type")
    _validate_enum(status, RULE_STATUSES, "status")
    if scope == "project" and not project_id:
        raise ValueError("project-scoped rules require project_id")
    if scope == "global":
        project_id = None
    validate_rule_payload(rule)
    rule_id = f"prefrule-{uuid.uuid4().hex[:12]}"
    now = _iso_now()
    conn.execute(
        """
        insert into preference_rules(
            id, scope, project_id, rule_type, status, priority,
            rule_json, evidence_ids_json, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rule_id,
            scope,
            project_id,
            rule_type,
            status,
            priority,
            json.dumps(dict(rule)),
            json.dumps(list(evidence_ids or [])),
            now,
            now,
        ),
    )
    if commit:
        conn.commit()
    return PreferenceRule(
        id=rule_id,
        scope=scope,
        project_id=project_id,
        rule_type=rule_type,
        status=status,
        priority=priority,
        rule=dict(rule),
        evidence_ids=list(evidence_ids or []),
        created_at=now,
        updated_at=now,
    )


def get_rule(conn: sqlite3.Connection, *, rule_id: str) -> PreferenceRule | None:
    row = conn.execute(
        "select * from preference_rules where id = ?",
        (rule_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_rule(row)


def list_rules(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    status: str | None = None,
    include_global: bool = True,
    include_deleted: bool = False,
) -> list[PreferenceRule]:
    clauses: list[str] = []
    params: list[Any] = []
    if not include_deleted:
        clauses.append("status != 'deleted'")
    if status is not None:
        _validate_enum(status, RULE_STATUSES, "status")
        clauses.append("status = ?")
        params.append(status)
    if project_id is not None:
        if include_global:
            clauses.append("(scope = 'global' or (scope = 'project' and project_id = ?))")
            params.append(project_id)
        else:
            clauses.append("scope = 'project' and project_id = ?")
            params.append(project_id)
    where = f"where {' and '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        select * from preference_rules
        {where}
        order by priority desc, updated_at desc
        """,
        params,
    ).fetchall()
    return [_row_rule(row) for row in rows]


def _update_rule_status(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    status: str,
    commit: bool = False,
) -> PreferenceRule:
    _validate_enum(status, RULE_STATUSES, "status")
    existing = get_rule(conn, rule_id=rule_id)
    if existing is None:
        raise ValueError(f"preference rule {rule_id!r} not found")
    now = _iso_now()
    conn.execute(
        """
        update preference_rules
        set status = ?, updated_at = ?
        where id = ?
        """,
        (status, now, rule_id),
    )
    if commit:
        conn.commit()
    return PreferenceRule(
        id=existing.id,
        scope=existing.scope,
        project_id=existing.project_id,
        rule_type=existing.rule_type,
        status=status,
        priority=existing.priority,
        rule=existing.rule,
        evidence_ids=existing.evidence_ids,
        created_at=existing.created_at,
        updated_at=now,
    )


def approve_rule(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    commit: bool = False,
) -> PreferenceRule:
    existing = get_rule(conn, rule_id=rule_id)
    if existing is None:
        raise ValueError(f"preference rule {rule_id!r} not found")
    if existing.status not in {"suggested", "disabled"}:
        raise ValueError(
            f"preference rule {rule_id!r} cannot be approved from status {existing.status!r}"
        )
    validate_rule_payload(existing.rule)
    return _update_rule_status(conn, rule_id=rule_id, status="active", commit=commit)


def reject_rule(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    commit: bool = False,
) -> PreferenceRule:
    existing = get_rule(conn, rule_id=rule_id)
    if existing is None:
        raise ValueError(f"preference rule {rule_id!r} not found")
    if existing.status not in {"suggested", "active"}:
        raise ValueError(
            f"preference rule {rule_id!r} cannot be rejected from status {existing.status!r}"
        )
    return _update_rule_status(conn, rule_id=rule_id, status="rejected", commit=commit)


def disable_rule(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    commit: bool = False,
) -> PreferenceRule:
    existing = get_rule(conn, rule_id=rule_id)
    if existing is None:
        raise ValueError(f"preference rule {rule_id!r} not found")
    if existing.status != "active":
        raise ValueError(
            f"preference rule {rule_id!r} cannot be disabled from status {existing.status!r}"
        )
    return _update_rule_status(conn, rule_id=rule_id, status="disabled", commit=commit)


def delete_rule(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    commit: bool = False,
) -> PreferenceRule:
    existing = get_rule(conn, rule_id=rule_id)
    if existing is None:
        raise ValueError(f"preference rule {rule_id!r} not found")
    if existing.status == "deleted":
        return existing
    return _update_rule_status(conn, rule_id=rule_id, status="deleted", commit=commit)


def export_rules(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    include_global: bool = True,
) -> dict[str, Any]:
    rules = list_rules(
        conn,
        project_id=project_id,
        include_global=include_global,
        include_deleted=False,
    )
    observations = list_observations(conn, project_id=project_id, limit=500)
    return {
        "project_id": project_id,
        "rules": [_rule_payload(rule) for rule in rules],
        "observations": [
            {
                "id": obs.id,
                "project_id": obs.project_id,
                "observation_type": obs.observation_type,
                "subject": obs.subject,
                "evidence": obs.evidence,
                "redaction_status": obs.redaction_status,
                "created_at": obs.created_at,
            }
            for obs in observations
        ],
        "exported_at": _iso_now(),
    }


def list_active_rules(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
) -> list[PreferenceRule]:
    return list_rules(
        conn,
        project_id=project_id,
        status="active",
        include_global=True,
    )


def routing_preference_hints(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> list[PreferenceHint]:
    hints: list[PreferenceHint] = []
    for rule in list_active_rules(conn, project_id=project_id):
        if rule.rule_type != "agent_choice":
            continue
        preferred = rule.rule.get("preferred_agent_id")
        if not isinstance(preferred, str) or not preferred.strip():
            continue
        hints.append(
            PreferenceHint(
                rule_id=rule.id,
                rule_type=rule.rule_type,
                message=f"prefer agent {preferred} (rule {rule.id})",
                score_delta=float(rule.rule.get("score_bonus", 15.0)),
                agent_id=preferred,
            )
        )
    return hints


def planning_preference_hints(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    text: str,
) -> list[PreferenceHint]:
    hints: list[PreferenceHint] = []
    normalized = text.lower()
    for rule in list_active_rules(conn, project_id=project_id):
        if rule.rule_type == "task_style":
            if rule.rule.get("prefer_small_tasks") and any(
                signal in normalized
                for signal in ("refactor", "rewrite", "migrate", "everything")
            ):
                hints.append(
                    PreferenceHint(
                        rule_id=rule.id,
                        rule_type=rule.rule_type,
                        message=f"prefer smaller tasks first (rule {rule.id})",
                    )
                )
            if rule.rule.get("reject_vague_tasks") and any(
                signal in normalized
                for signal in ("maybe", "somehow", "investigate", "explore", "tbd")
            ):
                hints.append(
                    PreferenceHint(
                        rule_id=rule.id,
                        rule_type=rule.rule_type,
                        message=f"reject vague tasks (rule {rule.id})",
                    )
                )
        if rule.rule_type == "risk_preference":
            if rule.rule.get("avoid_costly_agents_for_docs") and any(
                signal in normalized for signal in ("docs", "readme", "documentation")
            ):
                hints.append(
                    PreferenceHint(
                        rule_id=rule.id,
                        rule_type=rule.rule_type,
                        message=f"avoid costly agents for docs-only work (rule {rule.id})",
                    )
                )
    return hints