from dataclasses import dataclass
from pathlib import Path

from . import gitops
from .config import CoordinatorConfig


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    message: str


def _check(status: str, name: str, message: str) -> ReadinessCheck:
    return ReadinessCheck(name=name, status=status, message=message)


def _has_discovery_source(root: Path) -> bool:
    return (root / "tasks" / "inbox").exists() or (
        root / "tasks" / "generated"
    ).exists()


def _has_state_file(root: Path) -> bool:
    return (root / "coordinator.db").exists() or (root / "state" / "loop_state.md").exists()


def check_loop_readiness(root: Path, config: CoordinatorConfig | None) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []

    if _has_discovery_source(root):
        checks.append(
            _check("pass", "discovery source", "tasks/inbox or tasks/generated is present")
        )
    else:
        checks.append(
            _check("warn", "discovery source", "tasks/inbox or tasks/generated is not present")
        )

    if _has_state_file(root):
        checks.append(_check("pass", "state file", "coordinator.db or state/loop_state.md is present"))
    else:
        checks.append(
            _check("warn", "state file", "coordinator.db or state/loop_state.md is not present")
        )

    if config is None:
        checks.append(_check("warn", "evaluator", "configuration is not loadable; verifier policy unknown"))
        checks.append(
            _check("warn", "worktree isolation", "configuration is not loadable; repo isolation unknown")
        )
        checks.append(_check("warn", "budget cap", "configuration is not loadable; budget caps unknown"))
        checks.append(
            _check("warn", "human review point", "configuration is not loadable; review policy unknown")
        )
        return checks

    has_verifier = config.policy.require_verification_commands or any(
        repo.verify_commands for repo in config.repos.values()
    )
    if has_verifier:
        checks.append(
            _check(
                "warn",
                "evaluator",
                "verification commands are required or configured, but independent reviewer is not configured yet",
            )
        )
    else:
        checks.append(_check("warn", "evaluator", "verification commands are not required or configured"))

    if not config.repos:
        checks.append(_check("warn", "worktree isolation", "no repos are configured"))
    elif any(repo.branch_prefix for repo in config.repos.values()) and hasattr(
        gitops, "create_worktree"
    ):
        checks.append(
            _check(
                "pass",
                "worktree isolation",
                "repos have branch prefixes and git worktree support is available",
            )
        )
    else:
        checks.append(
            _check(
                "warn",
                "worktree isolation",
                "repo branch prefixes or git worktree support are missing",
            )
        )

    if config.policy.max_files_touched > 0 and config.policy.max_attempts > 0:
        checks.append(
            _check(
                "warn",
                "budget cap",
                "file and attempt policy caps are configured, but runtime caps are not configured yet",
            )
        )
    else:
        checks.append(_check("warn", "budget cap", "file and attempt caps are missing"))

    if not config.repos:
        checks.append(_check("warn", "human review point", "no repos are configured"))
    elif any(
        (not repo.allow_push) or repo.merge_policy != "auto_merge_default_branch"
        for repo in config.repos.values()
    ):
        checks.append(
            _check(
                "pass",
                "human review point",
                "at least one repo requires review before default-branch auto-merge",
            )
        )
    else:
        checks.append(_check("warn", "human review point", "all repos allow default-branch auto-merge"))

    return checks
