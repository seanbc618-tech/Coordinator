"""Safe project bootstrap for global Coordinator config."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from typing import Any

from .admin_json import AdminError, emit_envelope, envelope
from .config_runtime import REQUIRED_CONFIG_FILES, ensure_config_dir
from .runtime_paths import RuntimePaths, resolve_runtime_paths


class InitProjectError(RuntimeError):
    """Raised when project initialization cannot proceed safely."""


_DEFAULT_AGENT_COMMANDS = {
    "worker": "true",
    "commander": "true",
}


def discover_repo_root(path: Path) -> Path:
    """Return the git root for path or raise InitProjectError."""
    start = Path(path).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InitProjectError(f"git repository lookup failed: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.strip()
        message = stderr or f"not a git repository: {start}"
        if "not a git repository" not in message.lower():
            message = f"not a git repository: {start}"
        raise InitProjectError(message)
    return Path(result.stdout.strip()).resolve()


def derive_repo_id(repo_root: Path) -> str:
    """Return a stable lowercase id from the directory name."""
    normalized = repo_root.name.lower().replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        raise InitProjectError(f"cannot derive repo id from {repo_root.name!r}")
    return normalized


def _git_default_branch(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "main"
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "main"


def _minimal_agents_toml() -> str:
    return textwrap.dedent(
        """
        [agents.worker]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"

        [agents.commander]
        command = "true"
        capabilities = ["code", "tests", "docs", "research"]
        max_concurrency = 1
        role = "commander"
        """
    ).strip() + "\n"


def _minimal_policy_toml(*, autonomy_enabled: bool) -> str:
    return textwrap.dedent(
        f"""
        [task_policy]
        require_single_repo = false
        require_acceptance_criteria = false
        require_verification_commands = false
        require_handoff_summary = false
        max_files_touched = 20
        max_expected_minutes = 60
        max_attempts = 3
        split_if_touches_multiple_subsystems = false
        split_if_research_and_code_are_mixed = false

        [daemon_policy]
        loop_interval_seconds = 300
        idle_sleep_seconds = 60
        run_discovery_before_tasks = true

        [autonomy]
        enabled = {str(autonomy_enabled).lower()}
        max_iterations_per_tick = 1
        max_evaluations_per_iteration = 3
        max_admissions_per_iteration = 1
        max_generated_backlog_per_iteration = 3
        commander_generation_timeout_seconds = 45
        wait_when_running = true
        require_evaluation_before_followup = true
        pause_after_consecutive_failures = 3
        """
    ).strip() + "\n"


def _format_toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _format_verify_commands(commands: list[str]) -> str:
    if not commands:
        return "verify_commands = []"
    lines = ["verify_commands = ["]
    for command in commands:
        lines.append(f"    {_format_toml_string(command)},")
    lines.append("]")
    return "\n".join(lines)


def _repo_section_text(
    *,
    repo_id: str,
    repo_root: Path,
    default_branch: str,
    verify_commands: list[str],
    autonomy_enabled: bool,
) -> str:
    verify_block = _format_verify_commands(verify_commands)
    return textwrap.dedent(
        f"""
        [repos.{repo_id}]
        path = {_format_toml_string(str(repo_root))}
        default_branch = {_format_toml_string(default_branch)}
        remote = "origin"
        branch_prefix = "coord/"
        allow_push = false
        merge_policy = "no_push"
        autonomy_enabled = {str(autonomy_enabled).lower()}
        {verify_block}
        review_policy = "full_review"
        """
    ).strip()


def _agents_has_custom_commands(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        agents = tomllib.loads(path.read_text()).get("agents", {})
    except (OSError, tomllib.TOMLDecodeError):
        return True
    if not isinstance(agents, dict):
        return True
    for raw in agents.values():
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role", "worker"))
        command = str(raw.get("command", "")).strip()
        default = _DEFAULT_AGENT_COMMANDS.get(role)
        if default is not None and command and command != default:
            return True
    return False


def _read_repo_sections(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text())
    repos = data.get("repos", {})
    if not isinstance(repos, dict):
        return {}
    return {str(repo_id): dict(raw) for repo_id, raw in repos.items() if isinstance(raw, dict)}


def _render_repos_toml(repos: dict[str, dict[str, Any]]) -> str:
    blocks: list[str] = []
    for repo_id in sorted(repos):
        raw = repos[repo_id]
        lines = [f"[repos.{repo_id}]"]
        for key in (
            "path",
            "default_branch",
            "remote",
            "branch_prefix",
            "allow_push",
            "merge_policy",
            "autonomy_enabled",
            "review_policy",
        ):
            if key not in raw:
                continue
            value = raw[key]
            if isinstance(value, bool):
                lines.append(f"{key} = {str(value).lower()}")
            else:
                lines.append(f"{key} = {_format_toml_string(str(value))}")
        verify_commands = raw.get("verify_commands", [])
        if isinstance(verify_commands, list):
            if verify_commands:
                lines.append("verify_commands = [")
                for command in verify_commands:
                    lines.append(f"    {_format_toml_string(str(command))},")
                lines.append("]")
            else:
                lines.append("verify_commands = []")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def build_init_plan(
    paths: RuntimePaths,
    *,
    repo_root: Path,
    repo_id: str,
    verify_commands: list[str],
    autonomy_enabled: bool,
) -> dict[str, Any]:
    """Return the files and TOML sections that would be created or updated."""
    default_branch = _git_default_branch(repo_root)
    repo_section = _repo_section_text(
        repo_id=repo_id,
        repo_root=repo_root,
        default_branch=default_branch,
        verify_commands=verify_commands,
        autonomy_enabled=autonomy_enabled,
    )
    files: dict[str, Any] = {}
    agents_path = paths.config_dir / "agents.toml"
    policy_path = paths.config_dir / "policy.toml"
    repos_path = paths.config_dir / "repos.toml"

    if not agents_path.exists():
        files["agents.toml"] = {
            "action": "create",
            "content": _minimal_agents_toml(),
        }
    elif _agents_has_custom_commands(agents_path):
        files["agents.toml"] = {"action": "preserve", "reason": "custom agent commands"}
    else:
        files["agents.toml"] = {"action": "preserve", "reason": "already present"}

    if not policy_path.exists():
        files["policy.toml"] = {
            "action": "create",
            "content": _minimal_policy_toml(autonomy_enabled=autonomy_enabled),
        }
    else:
        files["policy.toml"] = {"action": "preserve", "reason": "already present"}

    existing_repos = _read_repo_sections(repos_path)
    repo_entry = {
        "path": str(repo_root),
        "default_branch": default_branch,
        "remote": "origin",
        "branch_prefix": "coord/",
        "allow_push": False,
        "merge_policy": "no_push",
        "autonomy_enabled": autonomy_enabled,
        "verify_commands": list(verify_commands),
        "review_policy": "full_review",
    }
    action = "create" if not repos_path.exists() else "update"
    files["repos.toml"] = {
        "action": action,
        "repo_id": repo_id,
        "section": repo_section,
        "entry": repo_entry,
        "content": (
            repo_section + "\n"
            if not existing_repos
            else _render_repos_toml({**existing_repos, repo_id: repo_entry})
        ),
    }

    return {
        "repo_root": str(repo_root),
        "repo_id": repo_id,
        "autonomy_enabled": autonomy_enabled,
        "verify_commands": list(verify_commands),
        "files": files,
        "config_dir": str(paths.config_dir),
    }


def apply_init_plan(paths: RuntimePaths, plan: dict[str, Any]) -> list[str]:
    """Apply a built init plan. Returns the list of files written."""
    ensure_config_dir(paths)
    written: list[str] = []
    files = plan["files"]

    agents_spec = files["agents.toml"]
    if agents_spec["action"] == "create":
        agents_path = paths.config_dir / "agents.toml"
        agents_path.write_text(agents_spec["content"])
        written.append("agents.toml")

    policy_spec = files["policy.toml"]
    if policy_spec["action"] == "create":
        policy_path = paths.config_dir / "policy.toml"
        policy_path.write_text(policy_spec["content"])
        written.append("policy.toml")

    repos_spec = files["repos.toml"]
    repos_path = paths.config_dir / "repos.toml"
    new_content = repos_spec["content"]
    if not repos_path.exists() or repos_path.read_text() != new_content:
        repos_path.write_text(new_content)
        written.append("repos.toml")
    return written


def run_init_command(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    paths.create()
    json_mode = getattr(args, "json", False)
    dry_run = getattr(args, "dry_run", False)
    yes = getattr(args, "yes", False)

    start = Path(args.path) if getattr(args, "path", None) else Path.cwd()
    autonomy_enabled = getattr(args, "autonomy", "off") == "on"
    verify_commands = [
        command
        for command in (getattr(args, "verify", None) or [])
        if str(command).strip()
    ]

    try:
        repo_root = discover_repo_root(start)
    except InitProjectError as exc:
        if json_mode:
            return emit_envelope(
                envelope(
                    command="init",
                    ok=False,
                    errors=[AdminError(code="invalid_project", message=str(exc))],
                )
            )
        print(f"error: {exc}", file=sys.stderr)
        return 1

    repo_id = getattr(args, "repo_id", None) or derive_repo_id(repo_root)
    plan = build_init_plan(
        paths,
        repo_root=repo_root,
        repo_id=repo_id,
        verify_commands=verify_commands,
        autonomy_enabled=autonomy_enabled,
    )

    if dry_run:
        if json_mode:
            return emit_envelope(
                envelope(command="init", ok=True, data=plan, warnings=[])
            )
        print("Coordinator init (dry-run)")
        print(f"repo_root: {plan['repo_root']}")
        print(f"repo_id: {plan['repo_id']}")
        for name, spec in plan["files"].items():
            print(f"  {name}: {spec['action']}")
        return 0

    if not yes:
        message = "Refusing to write without --yes confirmation."
        hint = "Run `coordinator init --yes` to apply the bootstrap plan."
        if json_mode:
            return emit_envelope(
                envelope(
                    command="init",
                    ok=False,
                    data=plan,
                    errors=[AdminError(code="confirmation_required", message=message, hint=hint)],
                )
            )
        print(f"error: {message}", file=sys.stderr)
        print(hint, file=sys.stderr)
        return 1

    try:
        written = apply_init_plan(paths, plan)
    except InitProjectError as exc:
        if json_mode:
            return emit_envelope(
                envelope(
                    command="init",
                    ok=False,
                    errors=[AdminError(code="invalid_project", message=str(exc))],
                )
            )
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if json_mode:
        return emit_envelope(
            envelope(
                command="init",
                ok=True,
                data={
                    "repo_root": plan["repo_root"],
                    "repo_id": plan["repo_id"],
                    "written_files": written,
                    "autonomy_enabled": plan["autonomy_enabled"],
                },
            )
        )

    print("Coordinator init")
    print(f"repo_root: {plan['repo_root']}")
    print(f"repo_id: {plan['repo_id']}")
    if written:
        print("written:")
        for name in written:
            print(f"  {name}")
    else:
        print("no changes (already initialized)")
    return 0