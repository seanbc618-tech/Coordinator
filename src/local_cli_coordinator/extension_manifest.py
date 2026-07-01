"""Declarative local extension manifest schema validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11
    tomllib = None  # type: ignore[assignment]

ALLOWED_TOP_LEVEL_KEYS = frozenset({
    "name",
    "version",
    "description",
    "slash_commands",
    "agent_adapters",
})

SLASH_COMMAND_KEYS = frozenset({"name", "description"})
AGENT_ADAPTER_KEYS = frozenset({"id", "display_name", "capabilities"})

_FORBIDDEN_POLICY_KEYS = frozenset({
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
    "policy",
    "execute",
    "eval",
    "import",
    "module",
    "script",
    "command",
    "python",
    "bytecode",
    "loader",
    "entrypoint",
    "rpc_method",
    "method",
})

_CODE_EXECUTION_KEYS = frozenset({
    "script",
    "module",
    "command",
    "exec",
    "eval",
    "import",
    "python",
    "bytecode",
    "entrypoint",
    "loader",
    "execute",
})


class ExtensionManifestError(ValueError):
    """Raised when an extension manifest is invalid or unsafe."""


def _walk_keys(value: Any, *, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            found.append((child_path, key_text))
            found.extend(_walk_keys(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            found.extend(_walk_keys(child, path=child_path))
    return found


def _check_forbidden_keys(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for path, key in _walk_keys(payload):
        lowered = key.lower()
        if lowered in _FORBIDDEN_POLICY_KEYS:
            errors.append(f"forbidden key at {path}: {key}")
        if lowered in _CODE_EXECUTION_KEYS:
            errors.append(f"code execution key at {path}: {key}")
    return errors


def _validate_slash_commands(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return ["slash_commands must be a list"]
    errors: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            errors.append(f"slash_commands[{index}] must be an object")
            continue
        extra = set(item.keys()) - SLASH_COMMAND_KEYS
        if extra:
            errors.append(
                f"slash_commands[{index}] has unsupported keys: {sorted(extra)}"
            )
        if "name" not in item or not str(item["name"]).startswith("/"):
            errors.append(f"slash_commands[{index}].name must start with /")
    return errors


def _validate_agent_adapters(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return ["agent_adapters must be a list"]
    errors: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            errors.append(f"agent_adapters[{index}] must be an object")
            continue
        extra = set(item.keys()) - AGENT_ADAPTER_KEYS
        if extra:
            errors.append(
                f"agent_adapters[{index}] has unsupported keys: {sorted(extra)}"
            )
        if "id" not in item:
            errors.append(f"agent_adapters[{index}] requires id")
        capabilities = item.get("capabilities")
        if capabilities is not None and not isinstance(capabilities, list):
            errors.append(f"agent_adapters[{index}].capabilities must be a list")
    return errors


def parse_manifest_text(text: str, *, suffix: str) -> dict[str, Any]:
    lowered = suffix.lower()
    if lowered == ".json":
        payload = json.loads(text)
    elif lowered in {".toml", ".tml"}:
        if tomllib is None:
            raise ExtensionManifestError("TOML manifests require Python 3.11+")
        payload = tomllib.loads(text)
    else:
        raise ExtensionManifestError(f"unsupported manifest suffix: {suffix}")
    if not isinstance(payload, dict):
        raise ExtensionManifestError("manifest root must be an object")
    return payload


def validate_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    extra_top = set(payload.keys()) - ALLOWED_TOP_LEVEL_KEYS
    if extra_top:
        errors.append(f"unsupported top-level keys: {sorted(extra_top)}")
    for required in ("name", "version"):
        if required not in payload or not str(payload[required]).strip():
            errors.append(f"missing required field: {required}")

    errors.extend(_check_forbidden_keys(payload))
    errors.extend(_validate_slash_commands(payload.get("slash_commands")))
    errors.extend(_validate_agent_adapters(payload.get("agent_adapters")))

    if errors:
        raise ExtensionManifestError("; ".join(errors))

    capabilities: list[str] = []
    for item in payload.get("agent_adapters") or []:
        if isinstance(item, Mapping):
            for cap in item.get("capabilities") or []:
                capabilities.append(str(cap))

    return {
        "name": str(payload["name"]),
        "version": str(payload["version"]),
        "description": str(payload.get("description", "")),
        "slash_commands": list(payload.get("slash_commands") or []),
        "agent_adapters": list(payload.get("agent_adapters") or []),
        "capabilities": sorted(set(capabilities)),
    }


def load_manifest_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    payload = parse_manifest_text(text, suffix=path.suffix)
    return validate_manifest(payload)