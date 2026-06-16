from dataclasses import dataclass
from pathlib import Path
import os
import shlex
import subprocess

from .config import AgentConfig


@dataclass(frozen=True)
class AgentRunResult:
    agent_id: str
    command: str
    exit_code: int
    log_path: Path


def _render_token(token: str, prompt_path: Path, worktree_path: Path) -> str:
    return (
        token.replace("{prompt_path}", str(prompt_path))
        .replace("{worktree_path}", str(worktree_path))
    )


def _write_log(
    log_path: Path,
    command: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    error: BaseException | None = None,
) -> None:
    lines = [
        f"command: {command}",
        f"exit_code: {exit_code}",
        "stdout:",
        stdout,
        "stderr:",
        stderr,
    ]
    if error is not None:
        lines.extend(["error:", f"{type(error).__name__}: {error}"])
    log_path.write_text("\n".join(lines))


def run_agent(
    agent: AgentConfig,
    prompt_path: Path,
    worktree_path: Path,
    run_dir: Path,
) -> AgentRunResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "agent.log"
    exit_code = 127
    command = agent.command
    env = os.environ.copy()
    env["COORDINATOR_AGENT_ID"] = agent.id
    env["COORDINATOR_PROMPT_PATH"] = str(prompt_path)
    env["COORDINATOR_WORKTREE_PATH"] = str(worktree_path)

    try:
        argv = [
            _render_token(token, prompt_path, worktree_path)
            for token in shlex.split(agent.command)
        ]
        command = shlex.join(argv)
        result = subprocess.run(
            argv,
            cwd=worktree_path,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        exit_code = result.returncode
        _write_log(log_path, command, result.stdout, result.stderr, exit_code)
    except (OSError, ValueError) as exc:
        _write_log(log_path, command, "", "", exit_code, exc)

    return AgentRunResult(
        agent_id=agent.id,
        command=command,
        exit_code=exit_code,
        log_path=log_path,
    )
