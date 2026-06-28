"""Permission mode parsing and reporting for Coordinator agents."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .runtime_paths import RuntimePaths


VALID_PERMISSION_MODES = frozenset({"read-only", "workspace-write", "danger"})

_ROLE_DEFAULT_MODES = {
    "commander": "read-only",
    "spec_reviewer": "read-only",
    "quality_reviewer": "read-only",
    "planner": "read-only",
    "worker": "workspace-write",
}


@dataclass(frozen=True)
class AgentPermissions:
    mode: str
    allowed_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class PermissionsPolicyConfig:
    default_mode: str = "workspace-write"
    danger_requires_confirmation: bool = True


def _validate_mode(mode: str, *, context: str) -> str:
    if mode not in VALID_PERMISSION_MODES:
        raise ValueError(f"{context} has unsupported permission mode {mode!r}")
    return mode


def _validate_tool_name(name: str, *, context: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{context} tool name must be a non-empty string")
    if "\n" in name or "\r" in name:
        raise ValueError(f"{context} tool name must not contain newlines")
    return name.strip()


def _normalize_tool_list(raw: object, *, context: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{context} must be a list")
    return tuple(_validate_tool_name(str(item), context=context) for item in raw)


def default_mode_for_role(role: str) -> str:
    return _ROLE_DEFAULT_MODES.get(role, "workspace-write")


def parse_permissions_policy(policy_doc: dict) -> PermissionsPolicyConfig:
    raw = policy_doc.get("permissions", {})
    if not isinstance(raw, dict):
        raise ValueError("policy.permissions must be a table")
    default_mode = str(raw.get("default_mode", "workspace-write"))
    _validate_mode(default_mode, context="policy.permissions.default_mode")
    return PermissionsPolicyConfig(
        default_mode=default_mode,
        danger_requires_confirmation=bool(raw.get("danger_requires_confirmation", True)),
    )


def parse_agent_permissions(
    raw: dict,
    *,
    role: str,
    policy: PermissionsPolicyConfig | None = None,
) -> AgentPermissions:
    permissions_raw = raw.get("permissions", {})
    if permissions_raw is None:
        permissions_raw = {}
    if not isinstance(permissions_raw, dict):
        raise ValueError("agent permissions must be a table")

    if "mode" in permissions_raw:
        mode = str(permissions_raw["mode"])
    else:
        mode = default_mode_for_role(role)
    _validate_mode(mode, context=f"agent {raw.get('id', role)!r}")

    allowed_tools = _normalize_tool_list(
        permissions_raw.get("allowed_tools"),
        context="allowed_tools",
    )
    denied_tools = _normalize_tool_list(
        permissions_raw.get("denied_tools"),
        context="denied_tools",
    )
    return AgentPermissions(
        mode=mode,
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
    )


def permissions_to_dict(permissions: AgentPermissions) -> dict[str, object]:
    return {
        "mode": permissions.mode,
        "allowed_tools": list(permissions.allowed_tools),
        "denied_tools": list(permissions.denied_tools),
    }


def resolve_agent_permissions(
    paths: RuntimePaths,
    *,
    agent_id: str,
    role: str,
) -> AgentPermissions:
    agents_path = paths.config_dir / "agents.toml"
    policy_path = paths.config_dir / "policy.toml"
    agents_raw = tomllib.loads(agents_path.read_text()).get("agents", {})
    if not isinstance(agents_raw, dict):
        raise ValueError("agents must be a table")
    raw = agents_raw.get(agent_id)
    if not isinstance(raw, dict):
        raw = {"role": role}
    else:
        raw = dict(raw)
    raw.setdefault("role", role)

    policy: PermissionsPolicyConfig | None = None
    if policy_path.is_file():
        policy_doc = tomllib.loads(policy_path.read_text())
        if "permissions" in policy_doc:
            policy = parse_permissions_policy(policy_doc)

    return parse_agent_permissions(raw, role=role, policy=policy)