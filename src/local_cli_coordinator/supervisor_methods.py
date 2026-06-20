"""Multi-client Supervisor method registry.

Handles project-scoped requests: status, chat, pause/resume/stop,
event subscribe/replay. Delegates pause state to the supervisor's
shared set. Event subscribers receive live events via the shared broker.
"""

from __future__ import annotations

import queue
import uuid
from typing import Any, Callable
import sqlite3

from .db import project_task_counts, project_list_tasks
from .supervisor_events import EventBroker
from .supervisor_protocol import (
    PROTOCOL_VERSION,
    RequestEnvelope,
    ResponseEnvelope,
)


class SupervisorMethods:
    """Registry of Supervisor request handlers.

    The EventBroker must be shared with the supervisor loop so that
    events published during ticks are visible to subscribers.
    Pause state is delegated to a shared set owned by the supervisor.
    """

    def __init__(self, broker: EventBroker | None = None) -> None:
        self._broker = broker or EventBroker()
        self._paused: set[str] = set()  # fallback if not set by supervisor
        self._live_queues: dict[str, queue.Queue] = {}  # sub_id → event queue
        self._handlers: dict[str, Callable] = {
            "project.status": self._handle_project_status,
            "chat.send": self._handle_chat_send,
            "project.pause": self._handle_project_pause,
            "project.resume": self._handle_project_resume,
            "project.stop": self._handle_project_stop,
            "events.subscribe": self._handle_events_subscribe,
            "events.replay": self._handle_events_replay,
        }

    def set_paused_ref(self, paused: set[str]) -> None:
        """Set reference to supervisor's paused set (unified state)."""
        self._paused = paused

    @property
    def broker(self) -> EventBroker:
        return self._broker

    def handle(
        self,
        conn: sqlite3.Connection,
        request: RequestEnvelope,
    ) -> ResponseEnvelope:
        """Dispatch a request to the appropriate handler."""
        handler = self._handlers.get(request.method)
        if handler is None:
            return ResponseEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request.request_id,
                ok=False,
                result=None,
                error=f"unsupported method {request.method!r}",
            )
        return handler(conn, request)

    def _handle_project_status(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        project_id = request.project_id
        counts = project_task_counts(conn, project_id=project_id)
        if not counts and not project_list_tasks(conn, project_id=project_id):
            return self._error(request, f"project {project_id!r} not found")
        return self._ok(request, {
            "counts": counts,
            "paused": project_id in self._paused,
        })

    def _handle_chat_send(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        return self._ok(request, {"received": True})

    def _handle_project_pause(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        self._paused.add(request.project_id)
        return self._ok(request, {"paused": True})

    def _handle_project_resume(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        self._paused.discard(request.project_id)
        return self._ok(request, {"paused": False})

    def _handle_project_stop(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        self._paused.discard(request.project_id)
        return self._ok(request, {"stopped": True})

    def _handle_events_subscribe(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        """Subscribe to live events for a project.

        Creates a queue and registers a broker callback that pushes
        events to it. Returns the subscription ID and replayed events.
        """
        project_id = request.project_id
        after = request.params.get("after", 0)

        sub_id = str(uuid.uuid4())[:8]
        event_queue: queue.Queue = queue.Queue()
        self._live_queues[sub_id] = event_queue

        # Register a real subscriber that pushes to the queue
        def _on_event(envelope):
            event_queue.put(envelope)

        self._broker.subscribe(project_id, _on_event)

        # Replay existing events
        events = self._broker.replay(conn, project_id, after=after)

        return self._ok(request, {
            "subscription_id": sub_id,
            "project_id": project_id,
            "replayed": [
                {"cursor": e.cursor, "type": e.event_type, "payload": e.payload}
                for e in events
            ],
        })

    def poll_live_events(self, sub_id: str, timeout: float = 0.0) -> list[dict]:
        """Poll for live events from a subscription."""
        q = self._live_queues.get(sub_id)
        if q is None:
            return []
        events = []
        try:
            while True:
                env = q.get_nowait()
                events.append({
                    "cursor": env.cursor,
                    "type": env.event_type,
                    "payload": env.payload,
                })
        except queue.Empty:
            pass
        return events

    def _handle_events_replay(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        after = request.params.get("after", 0)
        limit = request.params.get("limit", 1000)
        events = self._broker.replay(
            conn, request.project_id, after=after, limit=limit
        )
        return self._ok(request, {
            "events": [
                {"cursor": e.cursor, "type": e.event_type, "payload": e.payload}
                for e in events
            ]
        })

    @staticmethod
    def _ok(request: RequestEnvelope, result: dict[str, Any]) -> ResponseEnvelope:
        return ResponseEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id=request.request_id,
            ok=True,
            result=result,
            error=None,
        )

    @staticmethod
    def _error(request: RequestEnvelope, error: str) -> ResponseEnvelope:
        return ResponseEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id=request.request_id,
            ok=False,
            result=None,
            error=error,
        )
