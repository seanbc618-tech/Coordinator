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


def run_agent(
    agent: AgentConfig,
    prompt_path: Path,
    worktree_path: Path,
    run_dir: Path,
) -> AgentRunResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "agent.log"
    command = agent.command.format(prompt_path=str(prompt_path), worktree_path=str(worktree_path))
    env = os.environ.copy()
    env["COORDINATOR_AGENT_ID"] = agent.id
    env["COORDINATOR_PROMPT_PATH"] = str(prompt_path)
    env["COORDINATOR_WORKTREE_PATH"] = str(worktree_path)
    result = subprocess.run(
        shlex.split(command),
        cwd=worktree_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    log_path.write_text(result.stdout + result.stderr)
    return AgentRunResult(
        agent_id=agent.id,
        command=command,
        exit_code=result.returncode,
        log_path=log_path,
    )
