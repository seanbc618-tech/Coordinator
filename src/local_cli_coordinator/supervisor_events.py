"""Supervisor event stream for multi-project replay.

Persists events per project with monotonic cursors. Supports replay
from a cursor position and subscriber notifications.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable
import sqlite3


@dataclass(frozen=True)
class EventEnvelope:
    """Immutable event from the supervisor stream."""

    project_id: str
    cursor: int
    event_type: str
    payload: dict[str, Any]


_Subscriber = Callable[[EventEnvelope], None]


@dataclass
class _Subscription:
    project_id: str
    callback: _Subscriber


class EventBroker:
    """Publish/subscribe broker for project-scoped supervisor events."""

    def __init__(self) -> None:
        self._subscribers: list[_Subscription] = []
        self._next_token = 0

    def publish(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        """Publish an event and return its cursor."""
        # Get next cursor for this project
        row = conn.execute(
            "select coalesce(max(cursor), 0) as max_cursor "
            "from supervisor_events where project_id = ?",
            (project_id,),
        ).fetchone()
        cursor = row["max_cursor"] + 1

        conn.execute(
            "insert into supervisor_events(project_id, cursor, event_type, payload) "
            "values (?, ?, ?, ?)",
            (project_id, cursor, event_type, json.dumps(payload)),
        )
        conn.commit()

        envelope = EventEnvelope(
            project_id=project_id,
            cursor=cursor,
            event_type=event_type,
            payload=payload,
        )

        # Notify subscribers
        for sub in self._subscribers:
            if sub.project_id == project_id:
                try:
                    sub.callback(envelope)
                except Exception:
                    pass  # Don't let subscriber errors break publishing

        return cursor

    def replay(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        *,
        after: int = 0,
        limit: int = 1000,
    ) -> list[EventEnvelope]:
        """Replay events for a project after the given cursor."""
        rows = conn.execute(
            "select * from supervisor_events "
            "where project_id = ? and cursor > ? "
            "order by cursor asc limit ?",
            (project_id, after, limit),
        ).fetchall()

        return [
            EventEnvelope(
                project_id=row["project_id"],
                cursor=row["cursor"],
                event_type=row["event_type"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    def subscribe(self, project_id: str, callback: _Subscriber) -> int:
        """Subscribe to events for a project. Returns a token for unsubscribe."""
        token = self._next_token
        self._next_token += 1
        self._subscribers.append(_Subscription(project_id, callback))
        return token

    def unsubscribe(self, token: int) -> None:
        """Remove a subscription by token."""
        # Simple linear scan; fine for small subscriber counts
        if 0 <= token < len(self._subscribers):
            self._subscribers.pop(token)
