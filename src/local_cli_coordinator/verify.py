from dataclasses import dataclass
from pathlib import Path
import shlex

from .process import run_command


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int
    timed_out: bool = False


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    results: list[CommandResult]
    log_path: Path
    timed_out: bool = False


def run_verification(
    commands: list[str],
    worktree_path: Path,
    run_dir: Path,
    timeout_seconds: float | None = None,
) -> VerificationResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "verifier.log"
    output: list[str] = []
    results: list[CommandResult] = []
    if not commands:
        output.append("no verification commands configured\n")
        output.append("timed_out: False\n")
        output.append(f"timeout_seconds: {timeout_seconds}\n")
        log_path.write_text("".join(output))
        return VerificationResult(passed=False, results=results, log_path=log_path)

    for command in commands:
        output.append(f"$ {command}\n")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            output.append(f"error: {exc}\n")
            output.append("timed_out: False\n")
            output.append(f"timeout_seconds: {timeout_seconds}\n")
            results.append(CommandResult(command=command, exit_code=127))
            break
        if not argv:
            output.append("empty verification command\n")
            output.append("timed_out: False\n")
            output.append(f"timeout_seconds: {timeout_seconds}\n")
            results.append(CommandResult(command=command, exit_code=127))
            break
        try:
            result = run_command(
                argv,
                cwd=worktree_path,
                timeout_seconds=timeout_seconds,
            )
        except OSError as exc:
            output.append(f"error: {exc}\n")
            output.append("timed_out: False\n")
            output.append(f"timeout_seconds: {timeout_seconds}\n")
            results.append(CommandResult(command=command, exit_code=127))
            break
        else:
            output.append(result.stdout)
            output.append(result.stderr)
            output.append(f"timed_out: {result.timed_out}\n")
            output.append(f"timeout_seconds: {timeout_seconds}\n")
            results.append(
                CommandResult(
                    command=command,
                    exit_code=result.returncode,
                    timed_out=result.timed_out,
                )
            )
            if result.returncode != 0 or result.timed_out:
                break
    timed_out = any(result.timed_out for result in results)
    log_path.write_text("".join(output))
    return VerificationResult(
        passed=(
            bool(results)
            and not timed_out
            and all(result.exit_code == 0 for result in results)
        ),
        results=results,
        log_path=log_path,
        timed_out=timed_out,
    )
