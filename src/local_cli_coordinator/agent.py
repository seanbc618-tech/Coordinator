from dataclasses import dataclass
from pathlib import Path
import os
import shlex

from .config import AgentConfig
from .process import run_command


@dataclass(frozen=True)
class AgentRunResult:
    agent_id: str
    command: str
    exit_code: int
    log_path: Path
    timed_out: bool = False


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
    timed_out: bool,
    timeout_seconds: float | None,
    error: BaseException | None = None,
) -> None:
    lines = [
        f"command: {command}",
        f"exit_code: {exit_code}",
        f"timed_out: {timed_out}",
        f"timeout_seconds: {timeout_seconds}",
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
    timeout_seconds: float | None = None,
) -> AgentRunResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "agent.log"
    exit_code = 127
    timed_out = False
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
        if not argv:
            raise ValueError("empty agent command")
        command = shlex.join(argv)
        result = run_command(
            argv,
            cwd=worktree_path,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        exit_code = result.returncode
        timed_out = result.timed_out
        _write_log(
            log_path,
            command,
            result.stdout,
            result.stderr,
            exit_code,
            timed_out,
            timeout_seconds,
        )
    except (OSError, ValueError) as exc:
        _write_log(
            log_path,
            command,
            "",
            "",
            exit_code,
            timed_out,
            timeout_seconds,
            exc,
        )

    return AgentRunResult(
        agent_id=agent.id,
        command=command,
        exit_code=exit_code,
        log_path=log_path,
        timed_out=timed_out,
    )
