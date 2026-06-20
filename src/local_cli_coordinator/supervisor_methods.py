"""Multi-client Supervisor method registry.

Handles project-scoped requests: status, chat, pause/resume/stop,
event subscribe/replay.
"""

from __future__ import annotations

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
    """Registry of Supervisor request handlers."""

    def __init__(self) -> None:
        self._event_broker = EventBroker()
        self._paused: set[str] = set()
        self._handlers: dict[str, Callable] = {
            "project.status": self._handle_project_status,
            "chat.send": self._handle_chat_send,
            "project.pause": self._handle_project_pause,
            "project.resume": self._handle_project_resume,
            "project.stop": self._handle_project_stop,
            "events.subscribe": self._handle_events_subscribe,
            "events.replay": self._handle_events_replay,
        }

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
        # Placeholder: real implementation would route to Commander
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
        sub_id = str(uuid.uuid4())[:8]
        return self._ok(request, {"subscription_id": sub_id})

    def _handle_events_replay(
        self, conn: sqlite3.Connection, request: RequestEnvelope
    ) -> ResponseEnvelope:
        after = request.params.get("after", 0)
        events = self._event_broker.replay(
            conn, request.project_id, after=after
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
