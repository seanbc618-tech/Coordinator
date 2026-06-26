"""Reporter that forwards worker stdout/stderr to supervisor events."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from .reporting import ExecutionEvent, NULL_REPORTER, Reporter

if TYPE_CHECKING:
    from .supervisor_events import EventBroker


class EventStreamReporter:
    """Wrap a reporter and publish ``task.log.append`` for live tails."""

    def __init__(
        self,
        inner: Reporter,
        *,
        broker: EventBroker,
        conn: sqlite3.Connection,
        project_id: str,
    ) -> None:
        self._inner = inner
        self._broker = broker
        self._conn = conn
        self._project_id = project_id

    def emit(self, event: ExecutionEvent) -> None:
        self._inner.emit(event)
        if event.kind not in {"stdout", "stderr"}:
            return
        if not event.task_id or not event.text:
            return
        self._broker.publish(
            self._conn,
            self._project_id,
            "task.log.append",
            {"task_id": event.task_id, "output": event.text},
        )


def wrap_reporter(
    reporter: Reporter,
    *,
    broker: EventBroker | None,
    conn: sqlite3.Connection | None,
    project_id: str | None,
) -> Reporter:
    if broker is None or conn is None or not project_id:
        return reporter
    return EventStreamReporter(
        reporter if reporter is not None else NULL_REPORTER,
        broker=broker,
        conn=conn,
        project_id=project_id,
    )