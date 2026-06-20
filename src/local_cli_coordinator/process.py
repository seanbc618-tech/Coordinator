from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import fcntl
import locale
import os
import selectors
import shlex
import signal
import subprocess
import time

from local_cli_coordinator.reporting import (
    NULL_REPORTER,
    ExecutionContext,
    ExecutionEvent,
    Reporter,
)


TIMEOUT_EXIT_CODE = 124
_DRAIN_TIMEOUT_SECONDS = 0.2
_DEFAULT_POLL_SECONDS = 0.05


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


class _LineBuffer:
    def __init__(self) -> None:
        self._pending = ""
        self.complete = ""

    def feed(self, text: str) -> list[str]:
        if not text:
            return []
        self._pending += text
        lines: list[str] = []
        while True:
            newline_index = self._pending.find("\n")
            if newline_index < 0:
                break
            line = self._pending[: newline_index + 1]
            self._pending = self._pending[newline_index + 1 :]
            self.complete += line
            lines.append(line)
        return lines

    def flush(self) -> str | None:
        if not self._pending:
            return None
        remainder = self._pending
        self._pending = ""
        self.complete += remainder
        return remainder


def _render_command(argv: list[str]) -> str:
    return shlex.join(argv)


def _event_context(
    context: ExecutionContext | None,
    *,
    stage: str = "",
    actor: str = "",
    task_id: str = "",
    log_path: Path | None = None,
) -> tuple[str, str, str, Path | None]:
    resolved_stage = context.stage if context is not None else stage
    resolved_actor = context.actor if context is not None else actor
    resolved_task_id = context.task_id if context is not None else task_id
    resolved_log_path = context.log_path if context is not None else log_path
    return resolved_stage, resolved_actor, resolved_task_id, resolved_log_path


def _emit(
    reporter: Reporter,
    event: ExecutionEvent,
    *,
    stdout_sink: Callable[[str], None] | None = None,
    stderr_sink: Callable[[str], None] | None = None,
) -> None:
    reporter.emit(event)
    if event.kind == "stdout" and stdout_sink is not None:
        stdout_sink(event.text)
    elif event.kind == "stderr" and stderr_sink is not None:
        stderr_sink(event.text)


def _emit_stream_lines(
    reporter: Reporter,
    *,
    kind: str,
    stage: str,
    actor: str,
    task_id: str,
    log_path: Path | None,
    lines: list[str],
    stdout_sink: Callable[[str], None] | None,
    stderr_sink: Callable[[str], None] | None,
) -> None:
    sink = stdout_sink if kind == "stdout" else stderr_sink
    for line in lines:
        event = ExecutionEvent(
            kind=kind,
            stage=stage,
            actor=actor,
            task_id=task_id,
            text=line,
            log_path=log_path,
        )
        reporter.emit(event)
        if sink is not None:
            sink(line)


def _register_streams(
    selector: selectors.BaseSelector,
    process: subprocess.Popen[bytes],
) -> dict[int, str]:
    streams: dict[int, str] = {}
    if process.stdout is not None:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        streams[process.stdout.fileno()] = "stdout"
    if process.stderr is not None:
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        streams[process.stderr.fileno()] = "stderr"
    return streams


def _unregister_streams(selector: selectors.BaseSelector, process: subprocess.Popen[bytes]) -> None:
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            try:
                selector.unregister(pipe)
            except (KeyError, ValueError):
                pass


def _set_nonblocking(pipe: subprocess.PIPE) -> None:
    if os.name != "posix":
        return
    try:
        flags = fcntl.fcntl(pipe, fcntl.F_GETFL)
        fcntl.fcntl(pipe, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    except OSError:
        pass


def _read_available(pipe: subprocess.PIPE, *, max_bytes: int = 65536) -> bytes:
    try:
        return os.read(pipe.fileno(), max_bytes)
    except BlockingIOError:
        return b""
    except OSError:
        return b""


def _drain_available_output(
    process: subprocess.Popen[bytes],
    *,
    stdout_buffer: _LineBuffer,
    stderr_buffer: _LineBuffer,
    reporter: Reporter,
    stage: str,
    actor: str,
    task_id: str,
    log_path: Path | None,
    stdout_sink: Callable[[str], None] | None,
    stderr_sink: Callable[[str], None] | None,
    selector: selectors.BaseSelector | None = None,
    stdout_open: bool = True,
    stderr_open: bool = True,
) -> tuple[bool, bool, bool]:
    emitted_output = False
    pipes: list[tuple[str, subprocess.PIPE, _LineBuffer, bool]] = []
    if process.stdout is not None and stdout_open:
        pipes.append(("stdout", process.stdout, stdout_buffer, stdout_open))
    if process.stderr is not None and stderr_open:
        pipes.append(("stderr", process.stderr, stderr_buffer, stderr_open))

    if selector is not None and pipes:
        events = selector.select(timeout=0)
        ready_kinds = {key.data for key, _ in events}
        pipes = [entry for entry in pipes if entry[0] in ready_kinds]

    stdout_eof = not stdout_open
    stderr_eof = not stderr_open
    for kind, pipe, buffer, _ in pipes:
        chunk = _read_available(pipe)
        if not chunk:
            if kind == "stdout":
                stdout_eof = True
            else:
                stderr_eof = True
            continue
        text = _as_text(chunk)
        lines = buffer.feed(text)
        if lines:
            emitted_output = True
            _emit_stream_lines(
                reporter,
                kind=kind,
                stage=stage,
                actor=actor,
                task_id=task_id,
                log_path=log_path,
                lines=lines,
                stdout_sink=stdout_sink,
                stderr_sink=stderr_sink,
            )
    return emitted_output, stdout_eof, stderr_eof


def _bounded_drain(
    process: subprocess.Popen[bytes],
    *,
    stdout_buffer: _LineBuffer,
    stderr_buffer: _LineBuffer,
    reporter: Reporter,
    stage: str,
    actor: str,
    task_id: str,
    log_path: Path | None,
    stdout_sink: Callable[[str], None] | None,
    stderr_sink: Callable[[str], None] | None,
    selector: selectors.BaseSelector,
    duration_seconds: float,
    monotonic_fn: Callable[[], float],
) -> None:
    deadline = monotonic_fn() + duration_seconds
    stdout_open = True
    stderr_open = True
    while monotonic_fn() < deadline and (stdout_open or stderr_open):
        emitted_output, stdout_eof, stderr_eof = _drain_available_output(
            process,
            stdout_buffer=stdout_buffer,
            stderr_buffer=stderr_buffer,
            reporter=reporter,
            stage=stage,
            actor=actor,
            task_id=task_id,
            log_path=log_path,
            stdout_sink=stdout_sink,
            stderr_sink=stderr_sink,
            selector=selector,
            stdout_open=stdout_open,
            stderr_open=stderr_open,
        )
        stdout_open = not stdout_eof
        stderr_open = not stderr_eof
        if not emitted_output:
            remaining = max(0.0, deadline - monotonic_fn())
            if remaining <= 0:
                break
            selector.select(timeout=min(_DEFAULT_POLL_SECONDS, remaining))


def _flush_stream_buffers(
    *,
    stdout_buffer: _LineBuffer,
    stderr_buffer: _LineBuffer,
    reporter: Reporter,
    stage: str,
    actor: str,
    task_id: str,
    log_path: Path | None,
    stdout_sink: Callable[[str], None] | None,
    stderr_sink: Callable[[str], None] | None,
) -> None:
    for kind, buffer in (("stdout", stdout_buffer), ("stderr", stderr_buffer)):
        remainder = buffer.flush()
        if remainder is None:
            continue
        sink = stdout_sink if kind == "stdout" else stderr_sink
        event = ExecutionEvent(
            kind=kind,
            stage=stage,
            actor=actor,
            task_id=task_id,
            text=remainder,
            log_path=log_path,
        )
        reporter.emit(event)
        if sink is not None:
            sink(remainder)


def _poll_timeout_seconds(
    *,
    deadline: float | None,
    heartbeat_deadline: float,
    now: float,
) -> float:
    candidates = [_DEFAULT_POLL_SECONDS, max(0.0, heartbeat_deadline - now)]
    if deadline is not None:
        candidates.append(max(0.0, deadline - now))
    return min(candidates)


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
    reporter: Reporter = NULL_REPORTER,
    context: ExecutionContext | None = None,
    stdout_sink: Callable[[str], None] | None = None,
    stderr_sink: Callable[[str], None] | None = None,
    heartbeat_seconds: float = 15.0,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> ProcessResult:
    stage, actor, task_id, log_path = _event_context(context)
    command = _render_command(argv)
    started_at = monotonic_fn()
    deadline = None if timeout_seconds is None else started_at + timeout_seconds
    heartbeat_deadline = started_at + heartbeat_seconds

    reporter.emit(
        ExecutionEvent(
            kind="started",
            stage=stage,
            actor=actor,
            task_id=task_id,
            command=command,
            cwd=cwd,
            log_path=log_path,
        )
    )

    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    if process.stdout is not None:
        _set_nonblocking(process.stdout)
    if process.stderr is not None:
        _set_nonblocking(process.stderr)

    stdout_buffer = _LineBuffer()
    stderr_buffer = _LineBuffer()
    timed_out = False
    returncode = 1
    selector = selectors.DefaultSelector()
    stdout_open = process.stdout is not None
    stderr_open = process.stderr is not None

    try:
        _register_streams(selector, process)

        while True:
            now = monotonic_fn()
            if deadline is not None and now >= deadline and not timed_out:
                timed_out = True
                reporter.emit(
                    ExecutionEvent(
                        kind="timeout",
                        stage=stage,
                        actor=actor,
                        task_id=task_id,
                        timed_out=True,
                        elapsed_seconds=now - started_at,
                        log_path=log_path,
                    )
                )
                _kill_process_tree(process)

            emitted_output, stdout_eof, stderr_eof = _drain_available_output(
                process,
                stdout_buffer=stdout_buffer,
                stderr_buffer=stderr_buffer,
                reporter=reporter,
                stage=stage,
                actor=actor,
                task_id=task_id,
                log_path=log_path,
                stdout_sink=stdout_sink,
                stderr_sink=stderr_sink,
                selector=selector,
                stdout_open=stdout_open,
                stderr_open=stderr_open,
            )
            if stdout_eof:
                stdout_open = False
            if stderr_eof:
                stderr_open = False
            if emitted_output:
                heartbeat_deadline = monotonic_fn() + heartbeat_seconds

            poll_result = process.poll()
            if timed_out:
                _bounded_drain(
                    process,
                    stdout_buffer=stdout_buffer,
                    stderr_buffer=stderr_buffer,
                    reporter=reporter,
                    stage=stage,
                    actor=actor,
                    task_id=task_id,
                    log_path=log_path,
                    stdout_sink=stdout_sink,
                    stderr_sink=stderr_sink,
                    selector=selector,
                    duration_seconds=_DRAIN_TIMEOUT_SECONDS,
                    monotonic_fn=monotonic_fn,
                )
                returncode = TIMEOUT_EXIT_CODE
                _close_pipes(process)
                _reap_leader(process)
                break

            if poll_result is not None and not stdout_open and not stderr_open:
                returncode = poll_result
                break

            now = monotonic_fn()
            if now >= heartbeat_deadline:
                reporter.emit(
                    ExecutionEvent(
                        kind="heartbeat",
                        stage=stage,
                        actor=actor,
                        task_id=task_id,
                        elapsed_seconds=now - started_at,
                        log_path=log_path,
                    )
                )
                heartbeat_deadline = now + heartbeat_seconds

            poll_timeout = _poll_timeout_seconds(
                deadline=deadline,
                heartbeat_deadline=heartbeat_deadline,
                now=now,
            )
            if poll_timeout > 0 and (stdout_open or stderr_open or poll_result is None):
                selector.select(timeout=poll_timeout)
            elif poll_result is not None:
                stdout_open = False
                stderr_open = False

        if not timed_out:
            try:
                returncode = process.wait(timeout=_DRAIN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                _close_pipes(process)
                _reap_leader(process)
                returncode = process.returncode if process.returncode is not None else returncode
        _close_pipes(process)
    except KeyboardInterrupt:
        reporter.emit(
            ExecutionEvent(
                kind="interrupted",
                stage=stage,
                actor=actor,
                task_id=task_id,
                elapsed_seconds=monotonic_fn() - started_at,
                log_path=log_path,
            )
        )
        _kill_process_tree(process)
        _close_pipes(process)
        _reap_leader(process)
        raise
    except OSError as exc:
        reporter.emit(
            ExecutionEvent(
                kind="error",
                stage=stage,
                actor=actor,
                task_id=task_id,
                text=str(exc),
                log_path=log_path,
            )
        )
        _kill_process_tree(process)
        _close_pipes(process)
        _reap_leader(process)
        raise
    finally:
        _flush_stream_buffers(
            stdout_buffer=stdout_buffer,
            stderr_buffer=stderr_buffer,
            reporter=reporter,
            stage=stage,
            actor=actor,
            task_id=task_id,
            log_path=log_path,
            stdout_sink=stdout_sink,
            stderr_sink=stderr_sink,
        )
        _unregister_streams(selector, process)
        try:
            selector.close()
        except OSError:
            pass

    completed_at = monotonic_fn()
    reporter.emit(
        ExecutionEvent(
            kind="completed",
            stage=stage,
            actor=actor,
            task_id=task_id,
            exit_code=returncode,
            timed_out=timed_out,
            elapsed_seconds=completed_at - started_at,
            log_path=log_path,
        )
    )
    return ProcessResult(
        returncode=returncode,
        stdout=stdout_buffer.complete,
        stderr=stderr_buffer.complete,
        timed_out=timed_out,
    )