from dataclasses import dataclass
from pathlib import Path
import locale
import os
import signal
import subprocess


TIMEOUT_EXIT_CODE = 124
_DRAIN_TIMEOUT_SECONDS = 0.2


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _as_text(output: bytes | None) -> str:
    if output is None:
        return ""
    text = output.decode(locale.getpreferredencoding(False), errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _merge_output(first: bytes, second: bytes) -> bytes:
    if not first:
        return second
    if not second or first == second:
        return first
    if second.startswith(first):
        return second
    if first.startswith(second):
        return first
    return first + second


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


def _close_pipes(process: subprocess.Popen[bytes]) -> None:
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def _reap_leader(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=_DRAIN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=_DRAIN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> ProcessResult:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix" and timeout_seconds is not None,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        _kill_process_tree(process)
        try:
            drained_stdout, drained_stderr = process.communicate(
                timeout=_DRAIN_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired as drain_exc:
            stdout = _merge_output(stdout, drain_exc.stdout or b"")
            stderr = _merge_output(stderr, drain_exc.stderr or b"")
            _close_pipes(process)
            _reap_leader(process)
        else:
            stdout = _merge_output(stdout, drained_stdout)
            stderr = _merge_output(stderr, drained_stderr)
        return ProcessResult(
            returncode=TIMEOUT_EXIT_CODE,
            stdout=_as_text(stdout),
            stderr=_as_text(stderr),
            timed_out=True,
        )
    return ProcessResult(
        returncode=process.returncode,
        stdout=_as_text(stdout),
        stderr=_as_text(stderr),
    )
