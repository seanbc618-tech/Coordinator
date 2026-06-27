from dataclasses import dataclass, field
from pathlib import Path
import tomllib

SUPPORTED_AGENT_ROLES = frozenset({
    "worker",
    "spec_reviewer",
    "quality_reviewer",
    "planner",
    "commander",
})

SUPPORTED_REVIEW_POLICIES = frozenset({
    "auto",
    "branch_only",
    "always_human",
    "risky_human",
    "tests_only",
    "full_review",
})


@dataclass(frozen=True)
class AgentConfig:
    id: str
    command: str
    capabilities: list[str]
    max_concurrency: int
    role: str = "worker"
    fallback_agents: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoConfig:
    id: str
    path: Path
    default_branch: str
    remote: str
    branch_prefix: str
    allow_push: bool
    merge_policy: str
    verify_commands: list[str]
    memory_path: Path | None = None
    review_policy: str = "full_review"
    autonomy_enabled: bool = False


@dataclass(frozen=True)
class DaemonPolicyConfig:
    loop_interval_seconds: int = 300
    idle_sleep_seconds: int = 60
    run_discovery_before_tasks: bool = True


@dataclass(frozen=True)
class AutonomyConfig:
    enabled: bool = False
    max_iterations_per_tick: int = 1
    max_evaluations_per_iteration: int = 3
    max_admissions_per_iteration: int = 1
    max_generated_backlog_per_iteration: int = 3
    commander_generation_timeout_seconds: int = 45
    wait_when_running: bool = True
    require_evaluation_before_followup: bool = True
    pause_after_consecutive_failures: int = 3


@dataclass(frozen=True)
class PolicyConfig:
    require_single_repo: bool
    require_acceptance_criteria: bool
    require_verification_commands: bool
    require_handoff_summary: bool
    max_files_touched: int
    max_expected_minutes: int
    max_attempts: int
    split_if_touches_multiple_subsystems: bool
    split_if_research_and_code_are_mixed: bool
    max_task_runtime_seconds: int = 1800
    max_daemon_runtime_seconds: int = 3600
    max_tasks_per_run: int = 1
    max_tasks_per_day: int = 24
    max_consecutive_failures: int = 3


@dataclass(frozen=True)
class DiscoverySourceConfig:
    id: str
    type: str
    repos: dict[str, bool]
    command: str | None = None


@dataclass(frozen=True)
class ConnectorConfig:
    id: str
    command: str
    input_contract: str
    output_contract: str


@dataclass(frozen=True)
class CoordinatorConfig:
    agents: dict[str, AgentConfig]
    repos: dict[str, RepoConfig]
    policy: PolicyConfig
    discovery_sources: dict[str, DiscoverySourceConfig] = field(default_factory=dict)
    connectors: dict[str, ConnectorConfig] = field(default_factory=dict)
    daemon_policy: DaemonPolicyConfig = field(default_factory=DaemonPolicyConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)


def iter_agents_by_role(
    config: "CoordinatorConfig",
    role: str,
    capabilities: list[str] | None = None,
):
    """Yield agents matching *role* and optional *capabilities* in config order."""
    if not config.agents:
        return
    required = set(capabilities) if capabilities else set()
    for agent in config.agents.values():
        if agent.role == role and required.issubset(set(agent.capabilities)):
            yield agent


def select_agent_by_role(
    config: "CoordinatorConfig",
    role: str,
    capabilities: list[str] | None = None,
) -> "AgentConfig | None":
    """Return the first agent matching *role* and optional *capabilities*."""
    return next(iter_agents_by_role(config, role, capabilities), None)


def select_fallback_agent(
    config: "CoordinatorConfig",
    primary_id: str,
    capabilities: list[str],
    unavailable_ids: set[str] | None = None,
) -> "AgentConfig | None":
    """Return the first eligible fallback agent for *primary_id*.

    Selection criteria:
    - Listed in primary's fallback_agents
    - Different from primary
    - Worker role (not reviewer/planner/commander)
    - Has all required capabilities
    - Not in unavailable_ids
    Returns None if no eligible fallback exists.
    """
    primary = config.agents.get(primary_id)
    if primary is None:
        return None
    unavailable = unavailable_ids or set()
    required = set(capabilities)
    for fallback_id in primary.fallback_agents:
        if fallback_id == primary_id:
            continue
        if fallback_id in unavailable:
            continue
        candidate = config.agents.get(fallback_id)
        if candidate is None:
            continue
        if candidate.role != "worker":
            continue
        if not required.issubset(set(candidate.capabilities)):
            continue
        return candidate
    return None


SUPPORTED_DISCOVERY_SOURCE_TYPES = frozenset({
    "inbox",
    "git_recent_commits",
    "command",
    "ci_command",
    "issue_command",
})


def _validate_review_policy(repo_id: str, review_policy: str) -> str:
    if review_policy not in SUPPORTED_REVIEW_POLICIES:
        raise ValueError(
            f"repo {repo_id!r} has unsupported review_policy {review_policy!r}"
        )
    return review_policy


def _read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _load_connectors(config_dir: Path) -> dict[str, ConnectorConfig]:
    path = config_dir / "connectors.toml"
    if not path.exists():
        return {}

    connectors_raw = _read_toml(path).get("connectors", {})
    if not isinstance(connectors_raw, dict):
        raise ValueError("connectors must be a table")

    connectors: dict[str, ConnectorConfig] = {}
    for connector_id, raw in connectors_raw.items():
        if not isinstance(raw, dict):
            raise ValueError(f"connector {connector_id!r} must be a table")
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"connector {connector_id!r} command must be a string")
        input_contract = str(raw.get("input", "json"))
        output_contract = str(raw.get("output", "json"))
        connectors[connector_id] = ConnectorConfig(
            id=connector_id,
            command=command,
            input_contract=input_contract,
            output_contract=output_contract,
        )
    return connectors


def _load_discovery_sources(config_dir: Path) -> dict[str, DiscoverySourceConfig]:
    path = config_dir / "discovery.toml"
    if not path.exists():
        return {}

    sources_raw = _read_toml(path).get("sources", {})
    if not isinstance(sources_raw, dict):
        raise ValueError("discovery sources must be a table")

    sources: dict[str, DiscoverySourceConfig] = {}
    for source_id, raw in sources_raw.items():
        if not isinstance(raw, dict):
            raise ValueError(f"discovery source {source_id!r} must be a table")

        source_type = raw.get("type")
        if (
            not isinstance(source_type, str)
            or source_type not in SUPPORTED_DISCOVERY_SOURCE_TYPES
        ):
            raise ValueError(
                f"discovery source {source_id!r} has invalid type {source_type!r}"
            )

        repos_raw = raw.get("repos", {})
        if not isinstance(repos_raw, dict):
            raise ValueError(
                f"discovery source {source_id!r} repos must be a table"
            )
        for repo_id, enabled in repos_raw.items():
            if not isinstance(enabled, bool):
                raise ValueError(
                    f"discovery source {source_id!r} repo {repo_id!r} "
                    f"must be boolean, got {enabled!r}"
                )

        command = raw.get("command")
        if command is not None and not isinstance(command, str):
            raise ValueError(
                f"discovery source {source_id!r} command must be a string"
            )

        sources[source_id] = DiscoverySourceConfig(
            id=source_id,
            type=source_type,
            repos=dict(repos_raw),
            command=command,
        )
    return sources


def load_config_from_dir(config_dir: Path) -> CoordinatorConfig:
    """Load coordinator config from a flat directory of TOML files."""
    agents_raw = _read_toml(config_dir / "agents.toml").get("agents", {})
    repos_raw = _read_toml(config_dir / "repos.toml").get("repos", {})
    policy_doc = _read_toml(config_dir / "policy.toml")
    policy_raw = policy_doc["task_policy"]
    daemon_raw = policy_doc.get("daemon_policy", {})
    discovery_sources = _load_discovery_sources(config_dir)
    connectors = _load_connectors(config_dir)

    agents = {}
    for agent_id, raw in agents_raw.items():
        role = str(raw.get("role", "worker"))
        if role not in SUPPORTED_AGENT_ROLES:
            raise ValueError(
                f"agent {agent_id!r} has unsupported role {role!r}"
            )
        agents[agent_id] = AgentConfig(
            id=agent_id,
            command=str(raw["command"]),
            capabilities=list(raw.get("capabilities", [])),
            max_concurrency=int(raw.get("max_concurrency", 1)),
            role=role,
            fallback_agents=tuple(raw.get("fallback_agents", [])),
        )

    repos = {
        repo_id: RepoConfig(
            id=repo_id,
            path=Path(raw["path"]),
            default_branch=str(raw["default_branch"]),
            remote=str(raw.get("remote", "origin")),
            branch_prefix=str(raw.get("branch_prefix", "coord/")),
            allow_push=bool(raw.get("allow_push", False)),
            merge_policy=str(raw.get("merge_policy", "no_push")),
            verify_commands=list(raw.get("verify_commands", [])),
            memory_path=(
                Path(raw["memory_path"])
                if raw.get("memory_path") is not None
                else None
            ),
            review_policy=_validate_review_policy(
                repo_id,
                str(raw.get("review_policy", "full_review")),
            ),
            autonomy_enabled=bool(raw.get("autonomy_enabled", False)),
        )
        for repo_id, raw in repos_raw.items()
    }

    policy = PolicyConfig(
        require_single_repo=bool(policy_raw["require_single_repo"]),
        require_acceptance_criteria=bool(policy_raw["require_acceptance_criteria"]),
        require_verification_commands=bool(policy_raw["require_verification_commands"]),
        require_handoff_summary=bool(policy_raw["require_handoff_summary"]),
        max_files_touched=int(policy_raw["max_files_touched"]),
        max_expected_minutes=int(policy_raw["max_expected_minutes"]),
        max_attempts=int(policy_raw["max_attempts"]),
        split_if_touches_multiple_subsystems=bool(policy_raw["split_if_touches_multiple_subsystems"]),
        split_if_research_and_code_are_mixed=bool(policy_raw["split_if_research_and_code_are_mixed"]),
        max_task_runtime_seconds=int(policy_raw.get("max_task_runtime_seconds", 1800)),
        max_daemon_runtime_seconds=int(policy_raw.get("max_daemon_runtime_seconds", 3600)),
        max_tasks_per_run=int(policy_raw.get("max_tasks_per_run", 1)),
        max_tasks_per_day=int(policy_raw.get("max_tasks_per_day", 24)),
        max_consecutive_failures=int(policy_raw.get("max_consecutive_failures", 3)),
    )

    daemon_policy = DaemonPolicyConfig(
        loop_interval_seconds=int(daemon_raw.get("loop_interval_seconds", 300)),
        idle_sleep_seconds=int(daemon_raw.get("idle_sleep_seconds", 60)),
        run_discovery_before_tasks=bool(daemon_raw.get("run_discovery_before_tasks", True)),
    )

    autonomy_raw = policy_doc.get("autonomy", {})
    autonomy = AutonomyConfig(
        enabled=bool(autonomy_raw.get("enabled", False)),
        max_iterations_per_tick=int(autonomy_raw.get("max_iterations_per_tick", 1)),
        max_evaluations_per_iteration=int(
            autonomy_raw.get("max_evaluations_per_iteration", 3)
        ),
        max_admissions_per_iteration=int(
            autonomy_raw.get("max_admissions_per_iteration", 1)
        ),
        max_generated_backlog_per_iteration=int(
            autonomy_raw.get("max_generated_backlog_per_iteration", 3)
        ),
        commander_generation_timeout_seconds=int(
            autonomy_raw.get("commander_generation_timeout_seconds", 45)
        ),
        wait_when_running=bool(autonomy_raw.get("wait_when_running", True)),
        require_evaluation_before_followup=bool(
            autonomy_raw.get("require_evaluation_before_followup", True)
        ),
        pause_after_consecutive_failures=int(
            autonomy_raw.get("pause_after_consecutive_failures", 3)
        ),
    )

    return CoordinatorConfig(
        agents=agents,
        repos=repos,
        policy=policy,
        discovery_sources=discovery_sources,
        connectors=connectors,
        daemon_policy=daemon_policy,
        autonomy=autonomy,
    )


def load_config(root: Path) -> CoordinatorConfig:
    """Load config from legacy repo layout: ``{root}/config/*.toml``."""
    return load_config_from_dir(root / "config")


def try_load_config(root: Path) -> tuple[CoordinatorConfig | None, str | None]:
    try:
        return load_config(root), None
    except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        return None, str(exc)
