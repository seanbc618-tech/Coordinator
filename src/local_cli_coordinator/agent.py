from dataclasses import dataclass
from pathlib import Path
import os
import shlex

from .config import AgentConfig
from .process import run_command
from .reporting import NULL_REPORTER, ExecutionContext, Reporter


@dataclass(frozen=True)
class AgentRunResult:
    agent_id: str
    command: str
    exit_code: int
    log_path: Path
    timed_out: bool = False


def _render_token(token: str, prompt_path: Path, worktree_path: Path) -> str:
    prompt_path = prompt_path.resolve()
    worktree_path = worktree_path.resolve()
    return (
        token.replace("{prompt_path}", str(prompt_path))
        .replace("{worktree_path}", str(worktree_path))
    )


def _write_log_header(
    log_path: Path,
    command: str,
    timeout_seconds: float | None,
) -> None:
    log_path.write_text(
        f"command: {command}\n"
        f"timeout_seconds: {timeout_seconds}\n"
        "stdout:\n",
    )


def _write_log_footer(
    log_path: Path,
    exit_code: int,
    timed_out: bool,
    timeout_seconds: float | None,
    error: BaseException | None = None,
) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"exit_code: {exit_code}\n")
        handle.write(f"timed_out: {timed_out}\n")
        handle.write(f"timeout_seconds: {timeout_seconds}\n")
        if error is not None:
            handle.write(f"error: {type(error).__name__}: {error}\n")


def _log_sink(log_path: Path) -> object:
    """Return a callable that appends text to the log file."""
    def _append(text: str) -> None:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
    return _append


def run_agent(
    agent: AgentConfig,
    prompt_path: Path,
    worktree_path: Path,
    run_dir: Path,
    timeout_seconds: float | None = None,
    *,
    reporter: Reporter = NULL_REPORTER,
    task_id: str = "",
) -> AgentRunResult:
    prompt_path = prompt_path.resolve()
    worktree_path = worktree_path.resolve()
    run_dir = run_dir.resolve()
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
        _write_log_header(log_path, command, timeout_seconds)
        sink = _log_sink(log_path)
        context = ExecutionContext(
            stage="worker",
            actor=agent.id,
            task_id=task_id,
            log_path=log_path,
        )
        result = run_command(
            argv,
            cwd=worktree_path,
            env=env,
            timeout_seconds=timeout_seconds,
            reporter=reporter,
            context=context,
            stdout_sink=sink,
            stderr_sink=sink,
        )
        exit_code = result.returncode
        timed_out = result.timed_out
        _write_log_footer(log_path, exit_code, timed_out, timeout_seconds)
    except (OSError, ValueError) as exc:
        _write_log_footer(log_path, exit_code, timed_out, timeout_seconds, exc)

    return AgentRunResult(
        agent_id=agent.id,
        command=command,
        exit_code=exit_code,
        log_path=log_path,
        timed_out=timed_out,
    )
