"""Execution-stage restrictions for Commander tasks and worker pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .commander_protocol import CommanderTaskProposal
from .config import RepoConfig

TOOLS = frozenset({"read", "search", "test", "edit", "commit", "push", "merge"})
ALIASES = {"grep": "search", "write": "edit"}


class ExecutionPolicyError(Exception):
    """Raised when a pipeline stage is forbidden by execution policy."""


@dataclass(frozen=True)
class ExecutionPolicy:
    allowed: frozenset[str]
    source: str = "default"

    @staticmethod
    def compute_effective(
        server: ExecutionPolicy,
        client: ExecutionPolicy | None = None,
        *,
        exclude: frozenset[str] | None = None,
    ) -> ExecutionPolicy:
        allowed = set(server.allowed)
        if client is not None:
            allowed &= set(client.allowed)
        if exclude:
            allowed -= set(exclude)
        return ExecutionPolicy(frozenset(allowed), source="effective")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "allowed": sorted(self.allowed),
            "source": self.source,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_json_dict())

    @classmethod
    def from_json(cls, raw: str | dict[str, object]) -> ExecutionPolicy:
        if isinstance(raw, str):
            data = json.loads(raw) if raw.strip() else {}
        else:
            data = raw
        allowed_raw = data.get("allowed", [])
        if not isinstance(allowed_raw, list):
            allowed_raw = []
        allowed = frozenset(
            str(item) for item in allowed_raw if str(item) in TOOLS
        )
        source = str(data.get("source") or "default")
        return cls(allowed=allowed, source=source)


def canonicalize_tool_name(name: str) -> str:
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("empty tool name")
    normalized = ALIASES.get(normalized, normalized)
    if normalized not in TOOLS:
        raise ValueError(f"unknown tool: {name}")
    return normalized


def parse_tool_csv(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    if not raw.strip():
        raise ValueError("empty tool list")
    names: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty tool list")
        names.append(canonicalize_tool_name(part))
    return names


def derive_server_policy(repo: RepoConfig) -> ExecutionPolicy:
    allowed = {"read", "search", "edit", "commit"}
    if repo.verify_commands:
        allowed.add("test")
    if repo.allow_push and repo.merge_policy != "no_push":
        allowed.add("push")
    if repo.merge_policy == "auto_merge_default_branch":
        allowed.add("merge")
    return ExecutionPolicy(frozenset(allowed), source="repo")


def resolve_effective_policy(
    repo: RepoConfig,
    *,
    tools: list[str] | None = None,
    exclude_tools: list[str] | None = None,
    no_tools: bool = False,
) -> ExecutionPolicy:
    server = derive_server_policy(repo)
    if no_tools:
        return ExecutionPolicy(frozenset(), source="cli")
    client: ExecutionPolicy | None = None
    if tools is not None:
        client = ExecutionPolicy(frozenset(tools), source="cli")
    exclude = frozenset(exclude_tools or [])
    return ExecutionPolicy.compute_effective(server, client, exclude=exclude)


def _policy_data(policy: dict[str, object] | str) -> dict[str, object]:
    if isinstance(policy, str):
        return json.loads(policy) if policy.strip() else {}
    return policy


def policy_is_restrictive(policy: dict[str, object] | str) -> bool:
    """Return True when an explicit ``allowed`` list was persisted."""
    return "allowed" in _policy_data(policy)


def _allowed_from_policy(policy: dict[str, object] | str) -> frozenset[str]:
    return ExecutionPolicy.from_json(policy).allowed


def check_policy_stage(
    policy: dict[str, object] | str,
    stage: str,
    *,
    has_changes: bool = False,
) -> None:
    if not policy_is_restrictive(policy):
        return
    allowed = _allowed_from_policy(policy)
    if stage == "edit" and has_changes and "edit" not in allowed:
        raise ExecutionPolicyError("execution policy forbids edit")
    if stage not in allowed:
        raise ExecutionPolicyError(f"execution policy forbids {stage}")


def proposal_policy_rejection_reasons(
    proposal: CommanderTaskProposal,
    policy_json: str,
) -> list[str]:
    if not policy_is_restrictive(policy_json):
        return []
    policy = ExecutionPolicy.from_json(policy_json)
    if not policy.allowed:
        return ["execution policy forbids all task proposals"]
    reasons: list[str] = []
    if proposal.expected_files > 0 and "edit" not in policy.allowed:
        reasons.append(
            "execution policy requires expected_files=0 without edit stage"
        )
    if proposal.verification_commands and "test" not in policy.allowed:
        reasons.append(
            "execution policy forbids verification commands without test stage"
        )
    return reasons


def format_policy_prompt_section(policy: ExecutionPolicy) -> str:
    if not policy.allowed:
        return (
            "## Execution restrictions\n"
            "Effective policy: conversation only; do not propose tasks."
        )
    stages = ", ".join(sorted(policy.allowed))
    return (
        "## Execution restrictions\n"
        f"Effective allowed stages: {stages}\n"
        "Proposals must not require forbidden stages."
    )