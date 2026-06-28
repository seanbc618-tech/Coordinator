"""Explain effective Coordinator config values and their sources."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from .config_runtime import load_config_for_paths
from .runtime_paths import RuntimePaths

_SENSITIVE_MARKERS = ("token", "secret", "password", "api_key", "key")


def _should_redact_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def _redact_value(key: str, value: object) -> tuple[object, bool]:
    if not _should_redact_key(key):
        return value, False
    if value is None:
        return None, False
    return "[REDACTED]", True


def _entry(
    *,
    key: str,
    effective_value: object,
    source_kind: str,
    source: str,
    explanation: str,
) -> dict[str, Any]:
    value, redacted = _redact_value(key, effective_value)
    return {
        "key": key,
        "effective_value": value,
        "source_kind": source_kind,
        "source": source,
        "redacted": redacted,
        "explanation": explanation,
    }


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text())


def _policy_entries(paths: RuntimePaths, policy_doc: dict[str, Any]) -> list[dict[str, Any]]:
    policy_path = paths.config_dir / "policy.toml"
    task_policy = policy_doc.get("task_policy", {})
    if not isinstance(task_policy, dict):
        task_policy = {}

    defaults = {
        "policy.max_tasks_per_day": (
            24,
            "Daily task admission budget.",
        ),
        "policy.max_attempts": (
            3,
            "Maximum worker attempts per task.",
        ),
        "policy.max_files_touched": (
            3,
            "Maximum files a task may touch.",
        ),
    }
    entries: list[dict[str, Any]] = []
    field_map = {
        "policy.max_tasks_per_day": "max_tasks_per_day",
        "policy.max_attempts": "max_attempts",
        "policy.max_files_touched": "max_files_touched",
    }
    for key, (default_value, explanation) in defaults.items():
        field = field_map[key]
        if field in task_policy:
            entries.append(
                _entry(
                    key=key,
                    effective_value=task_policy[field],
                    source_kind="config_file",
                    source=str(policy_path),
                    explanation=explanation,
                )
            )
        else:
            entries.append(
                _entry(
                    key=key,
                    effective_value=default_value,
                    source_kind="default",
                    source="built-in default",
                    explanation=explanation,
                )
            )
    return entries


def _agent_entries(paths: RuntimePaths, agents_doc: dict[str, Any]) -> list[dict[str, Any]]:
    agents_path = paths.config_dir / "agents.toml"
    agents = agents_doc.get("agents", {})
    if not isinstance(agents, dict):
        return []

    entries: list[dict[str, Any]] = []
    reserved = {
        "command",
        "capabilities",
        "max_concurrency",
        "role",
        "fallback_agents",
        "permissions",
    }
    for agent_id, raw in sorted(agents.items()):
        if not isinstance(raw, dict):
            continue
        for field, value in sorted(raw.items()):
            if field in reserved:
                continue
            key = f"agents.{agent_id}.{field}"
            entries.append(
                _entry(
                    key=key,
                    effective_value=value,
                    source_kind="config_file",
                    source=str(agents_path),
                    explanation=f"Agent-specific setting for {agent_id}.",
                )
            )
    return entries


def explain_config(paths: RuntimePaths, *, key: str | None = None) -> list[dict[str, Any]]:
    """Return effective config values with source and redaction metadata."""
    config = load_config_for_paths(paths)
    policy_doc = _read_toml(paths.config_dir / "policy.toml")
    agents_doc = _read_toml(paths.config_dir / "agents.toml")

    entries: list[dict[str, Any]] = []
    entries.extend(_policy_entries(paths, policy_doc))
    entries.extend(_agent_entries(paths, agents_doc))

    entries.append(
        _entry(
            key="policy.require_single_repo",
            effective_value=config.policy.require_single_repo,
            source_kind="config_file",
            source=str(paths.config_dir / "policy.toml"),
            explanation="Whether tasks must target a single repo.",
        )
    )

    if key is None:
        return entries
    filtered = [entry for entry in entries if entry["key"] == key]
    if filtered:
        return filtered

    if re.fullmatch(r"agents\.[^.]+\.[^.]+", key):
        parts = key.split(".")
        agent_id = parts[1]
        field = parts[2]
        agents = agents_doc.get("agents", {})
        raw = agents.get(agent_id, {}) if isinstance(agents, dict) else {}
        if isinstance(raw, dict) and field in raw:
            return [
                _entry(
                    key=key,
                    effective_value=raw[field],
                    source_kind="config_file",
                    source=str(paths.config_dir / "agents.toml"),
                    explanation=f"Agent-specific setting for {agent_id}.",
                )
            ]
    return []