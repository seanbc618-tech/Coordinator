"""Read-only configuration inspection for global Coordinator."""

from __future__ import annotations

import shutil

from .admin_json import AdminError, emit_envelope, envelope
from .config_runtime import load_config_for_paths
from .runtime_paths import resolve_runtime_paths
from .supervisor_process import missing_config_admin_error, missing_config_file


def run_config_command(*, json_mode: bool = False) -> int:
    paths = resolve_runtime_paths()
    paths.create()

    missing = missing_config_file(paths)
    if missing is not None and json_mode:
        return emit_envelope(
            envelope(
                command="config",
                ok=False,
                errors=[missing_config_admin_error(missing)],
            )
        )

    try:
        config = load_config_for_paths(paths)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        if json_mode:
            return emit_envelope(
                envelope(
                    command="config",
                    ok=False,
                    errors=[
                        AdminError(
                            code="missing_config_file",
                            message=str(exc),
                            hint="Run `coordinator init` or create the required config files.",
                        )
                    ],
                )
            )
        print("Coordinator config")
        print()
        print("Paths")
        print(f"  config: {paths.config_dir}")
        print(f"  data:   {paths.data_dir}")
        print(f"  state:  {paths.state_dir}")
        print(f"  socket: {paths.socket}")
        print()
        print(f"Runtime: degraded ({exc})")
        print()
        agents_path = paths.config_dir / "agents.toml"
        if agents_path.exists():
            print("Agents")
            print(f"  agents.toml: present ({agents_path})")
        return 0

    agents_payload = []
    for agent_id, agent in sorted(config.agents.items()):
        command = shutil.which(agent.command.split()[0]) if agent.command else None
        status = "ok" if command else f"missing binary for {agent.command!r}"
        agents_payload.append(
            {
                "id": agent_id,
                "role": agent.role,
                "capabilities": list(agent.capabilities),
                "status": status,
            }
        )

    repos_payload = [
        {"id": repo_id, "path": str(repo.path)}
        for repo_id, repo in sorted(config.repos.items())
    ]
    policy_payload = {
        "max_files_touched": config.policy.max_files_touched,
        "max_attempts": config.policy.max_attempts,
        "max_tasks_per_day": config.policy.max_tasks_per_day,
    }

    if json_mode:
        return emit_envelope(
            envelope(
                command="config",
                ok=True,
                data={
                    "paths": {
                        "config": str(paths.config_dir),
                        "data": str(paths.data_dir),
                        "state": str(paths.state_dir),
                        "socket": str(paths.socket),
                    },
                    "agents": agents_payload,
                    "repos": repos_payload,
                    "policy": policy_payload,
                    "runtime_status": "ok",
                },
            )
        )

    print("Coordinator config")
    print()
    print("Paths")
    print(f"  config: {paths.config_dir}")
    print(f"  data:   {paths.data_dir}")
    print(f"  state:  {paths.state_dir}")
    print(f"  socket: {paths.socket}")
    print()

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