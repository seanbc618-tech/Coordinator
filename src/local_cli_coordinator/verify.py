from dataclasses import dataclass
from pathlib import Path
import shlex

from .process import run_command
from .reporting import NULL_REPORTER, ExecutionContext, Reporter


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
    *,
    reporter: Reporter = NULL_REPORTER,
    task_id: str = "",
) -> VerificationResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "verifier.log"
    results: list[CommandResult] = []

    def _append_log(text: str) -> None:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)

    if not commands:
        log_path.write_text("no verification commands configured\n")
        _append_log(f"timed_out: False\n")
        _append_log(f"timeout_seconds: {timeout_seconds}\n")
        return VerificationResult(passed=False, results=results, log_path=log_path)

    # Clear log for streaming writes
    log_path.write_text("")

    for command in commands:
        _append_log(f"$ {command}\n")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            _append_log(f"error: {exc}\n")
            _append_log(f"timed_out: False\n")
            _append_log(f"timeout_seconds: {timeout_seconds}\n")
            results.append(CommandResult(command=command, exit_code=127))
            break
        if not argv:
            _append_log("empty verification command\n")
            _append_log(f"timed_out: False\n")
            _append_log(f"timeout_seconds: {timeout_seconds}\n")
            results.append(CommandResult(command=command, exit_code=127))
            break
        try:
            context = ExecutionContext(
                stage="verify",
                task_id=task_id,
                log_path=log_path,
            )
            result = run_command(
                argv,
                cwd=worktree_path,
                timeout_seconds=timeout_seconds,
                reporter=reporter,
                context=context,
                stdout_sink=_append_log,
                stderr_sink=_append_log,
            )
        except OSError as exc:
            _append_log(f"error: {exc}\n")
            _append_log(f"timed_out: False\n")
            _append_log(f"timeout_seconds: {timeout_seconds}\n")
            results.append(CommandResult(command=command, exit_code=127))
            break
        else:
            _append_log(f"timed_out: {result.timed_out}\n")
            _append_log(f"timeout_seconds: {timeout_seconds}\n")
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
