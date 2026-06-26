"""Runtime identity and capability contract for the global Supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .supervisor_protocol import PROTOCOL_VERSION

RUNTIME_COMPATIBILITY = "coordinator-global-supervisor/phase5.2"

SUPERVISOR_CAPABILITIES: tuple[str, ...] = (
    "chat.commander.v2",
    "project.goal",
    "project.task",
    "project.tasks",
    "transcript.line-budget.v1",
)

REQUIRED_CLIENT_CAPABILITIES = frozenset(SUPERVISOR_CAPABILITIES)

INCOMPATIBLE_SUPERVISOR_MESSAGE = (
    "Supervisor is incompatible with this Coordinator install.\n"
    "Run: coordinator supervisor restart"
)


@dataclass(frozen=True)
class SupervisorIdentity:
    pong: bool
    pid: int
    protocol_version: int
    runtime_compatibility: str
    capabilities: tuple[str, ...]
    started_at: str
    active_workers: int

    @classmethod
    def from_ping_result(cls, result: dict[str, Any]) -> SupervisorIdentity | None:
        required = (
            "pong",
            "pid",
            "protocol_version",
            "runtime_compatibility",
            "capabilities",
            "started_at",
            "active_workers",
        )
        if not all(key in result for key in required):
            return None
        try:
            return cls(
                pong=bool(result["pong"]),
                pid=int(result["pid"]),
                protocol_version=int(result["protocol_version"]),
                runtime_compatibility=str(result["runtime_compatibility"]),
                capabilities=tuple(str(item) for item in result["capabilities"]),
                started_at=str(result["started_at"]),
                active_workers=int(result["active_workers"]),
            )
        except (TypeError, ValueError):
            return None


def build_ping_result(
    *,
    pid: int,
    started_at: str,
    active_workers: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "pong": True,
        "pid": pid,
        "protocol_version": PROTOCOL_VERSION,
        "runtime_compatibility": RUNTIME_COMPATIBILITY,
        "capabilities": list(SUPERVISOR_CAPABILITIES),
        "started_at": started_at,
        "active_workers": active_workers,
    }
    if extra:
        result.update(extra)
    return result


def is_compatible_identity(identity: SupervisorIdentity) -> bool:
    if not identity.pong:
        return False
    if identity.runtime_compatibility != RUNTIME_COMPATIBILITY:
        return False
    return REQUIRED_CLIENT_CAPABILITIES.issubset(frozenset(identity.capabilities))