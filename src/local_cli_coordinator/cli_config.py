"""Read-only configuration inspection for global Coordinator."""

from __future__ import annotations

import shutil

from .config_runtime import load_config_for_paths
from .runtime_paths import resolve_runtime_paths


def run_config_command() -> int:
    paths = resolve_runtime_paths()
    paths.create()

    print("Coordinator config")
    print()
    print("Paths")
    print(f"  config: {paths.config_dir}")
    print(f"  data:   {paths.data_dir}")
    print(f"  state:  {paths.state_dir}")
    print(f"  socket: {paths.socket}")
    print()

    try:
        config = load_config_for_paths(paths)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"Runtime: degraded ({exc})")
        print()
        agents_path = paths.config_dir / "agents.toml"
        if agents_path.exists():
            print("Agents")
            print(f"  agents.toml: present ({agents_path})")
        return 0

    print("Agents")
    if not config.agents:
        print("  (none configured)")
    for agent_id, agent in sorted(config.agents.items()):
        command = shutil.which(agent.command.split()[0]) if agent.command else None
        status = "ok" if command else f"missing binary for {agent.command!r}"
        print(
            f"  {agent_id}: role={agent.role} "
            f"capabilities={','.join(agent.capabilities) or '-'} [{status}]"
        )
    commander = next(
        (item for item in config.agents.values() if item.role == "commander"),
        None,
    )
    if commander is None:
        print("  [warn] no commander agent configured")
    print()

    print("Repos")
    if not config.repos:
        print("  [warn] allowlist is empty")
    for repo_id, repo in sorted(config.repos.items()):
        print(f"  {repo_id}: {repo.path}")
    print()

    print("Policy")
    print(f"  max_files_touched: {config.policy.max_files_touched}")
    print(f"  max_attempts: {config.policy.max_attempts}")
    print(f"  max_tasks_per_day: {config.policy.max_tasks_per_day}")
    print()

    print("Runtime")
    print("  status: ok")
    return 0