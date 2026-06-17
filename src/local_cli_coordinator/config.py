from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class AgentConfig:
    id: str
    command: str
    capabilities: list[str]
    max_concurrency: int


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


@dataclass(frozen=True)
class CoordinatorConfig:
    agents: dict[str, AgentConfig]
    repos: dict[str, RepoConfig]
    policy: PolicyConfig


def _read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_config(root: Path) -> CoordinatorConfig:
    config_dir = root / "config"
    agents_raw = _read_toml(config_dir / "agents.toml").get("agents", {})
    repos_raw = _read_toml(config_dir / "repos.toml").get("repos", {})
    policy_raw = _read_toml(config_dir / "policy.toml")["task_policy"]

    agents = {
        agent_id: AgentConfig(
            id=agent_id,
            command=str(raw["command"]),
            capabilities=list(raw.get("capabilities", [])),
            max_concurrency=int(raw.get("max_concurrency", 1)),
        )
        for agent_id, raw in agents_raw.items()
    }

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
    )

    return CoordinatorConfig(agents=agents, repos=repos, policy=policy)


def try_load_config(root: Path) -> tuple[CoordinatorConfig | None, str | None]:
    try:
        return load_config(root), None
    except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        return None, str(exc)
