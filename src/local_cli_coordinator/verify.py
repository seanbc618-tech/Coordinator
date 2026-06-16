from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    results: list[CommandResult]
    log_path: Path


def run_verification(
    commands: list[str],
    worktree_path: Path,
    run_dir: Path,
) -> VerificationResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "verifier.log"
    output: list[str] = []
    results: list[CommandResult] = []
    for command in commands:
        result = subprocess.run(
            shlex.split(command),
            cwd=worktree_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        output.append(f"$ {command}\n")
        output.append(result.stdout)
        output.append(result.stderr)
        results.append(CommandResult(command=command, exit_code=result.returncode))
        if result.returncode != 0:
            break
    log_path.write_text("".join(output))
    return VerificationResult(
        passed=all(result.exit_code == 0 for result in results),
        results=results,
        log_path=log_path,
    )
