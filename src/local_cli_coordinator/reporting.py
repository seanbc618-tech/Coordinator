from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ExecutionEvent:
    """Immutable event emitted by pipeline stages."""

    kind: str
    stage: str
    actor: str = ""
    task_id: str = ""
    command: str = ""
    cwd: Path | None = None
    text: str = ""
    elapsed_seconds: float = 0.0
    exit_code: int | None = None
    timed_out: bool = False
    log_path: Path | None = None


@dataclass(frozen=True)
class ExecutionContext:
    """Metadata passed to process runners for event enrichment."""

    stage: str
    actor: str = ""
    task_id: str = ""
    log_path: Path | None = None


class Reporter(Protocol):
    """Protocol accepted by pipeline stages."""

    def emit(self, event: ExecutionEvent) -> None: ...


class NullReporter:
    """No-op reporter for library callers and --quiet mode."""

    def emit(self, event: ExecutionEvent) -> None:
        pass


NULL_REPORTER: Reporter = NullReporter()


class ConsoleReporter:
    """Renders ExecutionEvent to a terminal stream with immediate flush."""

    def __init__(
        self,
        *,
        stream: object = None,
        timestamp_fn: object = None,
    ) -> None:
        import sys
        import time

        self._stream = stream if stream is not None else sys.stderr
        self._timestamp = timestamp_fn if timestamp_fn is not None else self._default_timestamp
        self._partial: dict[str, str] = {}

    @staticmethod
    def _default_timestamp() -> str:
        import time

        return time.strftime("%H:%M:%S")

    def emit(self, event: ExecutionEvent) -> None:
        try:
            self._render(event)
        except (OSError, ValueError):
            pass

    def _render(self, event: ExecutionEvent) -> None:
        kind = event.kind
        ts = self._timestamp()
        label = self._label(event)

        if kind == "cycle_started":
            self._write(f"[{ts}] cycle      started\n")
            self._flush()
        elif kind == "task_started":
            self._write(f"[{ts}] task       {event.task_id}")
            if event.actor:
                self._write(f" - agent={event.actor}")
            self._write("\n")
            self._flush()
        elif kind == "started":
            self._write(f"[{ts}] {label} started\n")
            if event.task_id:
                self._write(f"{'':12} task={event.task_id}\n")
            if event.actor:
                self._write(f"{'':12} actor={event.actor}\n")
            if event.cwd:
                self._write(f"{'':12} cwd={event.cwd}\n")
            if event.command:
                self._write(f"{'':12} $ {event.command}\n")
            self._flush()
        elif kind == "stdout":
            self._buffer_stream(event, "stdout")
        elif kind == "stderr":
            self._buffer_stream(event, "stderr")
        elif kind == "heartbeat":
            self._write(f"[{ts}] {label} running - {event.elapsed_seconds:.1f}s")
            if event.task_id:
                self._write(f" - task={event.task_id}")
            if event.actor:
                self._write(f" - actor={event.actor}")
            self._write("\n")
            self._flush()
        elif kind == "timeout":
            self._flush_partial(event)
            self._write(f"[{ts}] {label} TIMED OUT - {event.elapsed_seconds:.1f}s\n")
            self._flush()
        elif kind == "interrupted":
            self._flush_partial(event)
            self._write(f"[{ts}] {label} INTERRUPTED - {event.elapsed_seconds:.1f}s\n")
            self._flush()
        elif kind == "error":
            self._flush_partial(event)
            self._write(f"[{ts}] {label} ERROR: {event.text}\n")
            self._flush()
        elif kind == "completed":
            self._flush_partial(event)
            status = "timed out" if event.timed_out else "completed"
            parts = [f"[{ts}] {label} {status}"]
            if event.exit_code is not None:
                parts.append(f"exit={event.exit_code}")
            if event.elapsed_seconds:
                parts.append(f"{event.elapsed_seconds:.1f}s")
            if event.log_path:
                parts.append(f"log={event.log_path}")
            self._write(" - ".join(parts))
            self._write("\n")
            self._flush()
        else:
            # Generic fallback for unknown event kinds
            self._write(f"[{ts}] {label} {kind}")
            if event.text:
                self._write(f": {event.text}")
            if event.task_id:
                self._write(f" - task={event.task_id}")
            self._write("\n")
            self._flush()

    def _label(self, event: ExecutionEvent) -> str:
        return f"{event.stage:<10}"

    def _buffer_stream(self, event: ExecutionEvent, stream_name: str) -> None:
        actor = event.actor or event.stage
        key = f"{actor}:{stream_name}"
        pending = self._partial.get(key, "") + event.text
        wrote = False
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            self._write(f"[{key}] {line}\n")
            wrote = True
        self._partial[key] = pending
        if wrote:
            self._flush()

    def _flush_partial(self, _event: ExecutionEvent) -> None:
        for key in list(self._partial):
            remainder = self._partial.pop(key)
            if remainder:
                self._write(f"[{key}] {remainder}\n")
        self._flush()

    def _write(self, text: str) -> None:
        self._stream.write(text)  # type: ignore[union-attr]

    def _flush(self) -> None:
        self._stream.flush()  # type: ignore[union-attr]
