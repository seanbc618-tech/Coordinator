"""Fake Supervisor for TUI integration testing.

Listens on a Unix socket, accepts Supervisor protocol v1 messages,
and responds with deterministic events for PTY verification.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path


PROTOCOL_VERSION = 1


def _make_response(request_id: str, ok: bool = True, result: dict | None = None, error: str | None = None) -> str:
    return json.dumps({
        "type": "response",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": ok,
        "result": result if ok else None,
        "error": error if not ok else None,
    }) + "\n"


def _make_event(cursor: int, event_type: str, payload: dict, project_id: str = "proj-a") -> str:
    return json.dumps({
        "type": "event",
        "protocol_version": PROTOCOL_VERSION,
        "project_id": project_id,
        "cursor": cursor,
        "event_type": event_type,
        "payload": payload,
    }) + "\n"


class FakeSupervisor:
    """A minimal Supervisor that responds to TUI requests."""

    def __init__(self, socket_path: str, project_id: str = "proj-a") -> None:
        self.socket_path = socket_path
        self.project_id = project_id
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._cursor = 0
        self._request_count = 0

    def start(self) -> None:
        """Start listening in a background thread."""
        path = Path(self.socket_path)
        if path.exists():
            path.unlink()

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.socket_path)
        self._server.listen(5)
        self._server.settimeout(0.1)
        self._running = True

        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the server."""
        self._running = False
        if self._server:
            self._server.close()
        if self._thread:
            self._thread.join(timeout=2)
        path = Path(self.socket_path)
        if path.exists():
            path.unlink()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server.accept()
                threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, conn: socket.socket) -> None:
        buf = b""
        try:
            while self._running:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._process_message(conn, line.decode())
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            conn.close()

    def _process_message(self, conn: socket.socket, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if msg.get("type") != "request":
            return

        self._request_count += 1
        request_id = msg.get("request_id", "")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "system.ping":
            self._respond(conn, request_id, {"pong": True, "projects": {self.project_id: {"ready": 1}}})

        elif method == "project.snapshot":
            self._respond(conn, request_id, {
                "project_id": self.project_id,
                "cursor": self._cursor,
                "status": "active",
                "tasks": {"ready": 1, "running": 0, "done": 3},
            })

        elif method == "events.subscribe":
            after_cursor = params.get("after_cursor", 0)
            self._respond(conn, request_id)
            # Send some events after subscribe
            self._send_event(conn, "tick_scheduled", {"project_id": self.project_id, "reason": "ready"})
            self._send_event(conn, "task.created", {"task_id": "task-001", "title": "Implement auth", "agent": "worker"})
            self._send_event(conn, "task.stage", {"task_id": "task-001", "stage": "running"})
            self._send_event(conn, "task.command", {"task_id": "task-001", "command": "npm test"})
            self._send_event(conn, "task.output", {"task_id": "task-001", "output": "pass 1/3\npass 2/3\npass 3/3\n"})
            self._send_event(conn, "task.verification", {"task_id": "task-001", "result": "passed"})
            self._send_event(conn, "task.done", {"task_id": "task-001", "result": "completed"})
            self._send_event(conn, "chat.message", {"role": "coordinator", "text": "Task completed successfully!"})

        elif method == "chat.send":
            text = params.get("text", "")
            self._respond(conn, request_id, {"received": True})
            # Echo back as coordinator
            self._send_event(conn, "chat.message", {"role": "coordinator", "text": f"Received: {text}"})

        elif method == "project.status":
            self._respond(conn, request_id, {
                "project_id": self.project_id,
                "status": "active",
                "tasks": {"ready": 1, "running": 0, "done": 3},
            })

        elif method == "project.tasks":
            self._respond(conn, request_id, {
                "tasks": [
                    {"id": "task-001", "title": "Implement auth", "state": "done"},
                    {"id": "task-002", "title": "Add tests", "state": "ready"},
                ],
            })

        elif method in ("project.pause", "project.resume", "project.stop"):
            self._respond(conn, request_id, {"ok": True})

        elif method == "system.shutdown":
            self._respond(conn, request_id, {"shutting_down": True})

        else:
            self._respond(conn, request_id, {"method": method})

    def _respond(self, conn: socket.socket, request_id: str, result: dict | None = None) -> None:
        conn.sendall(_make_response(request_id, result=result).encode())

    def _send_event(self, conn: socket.socket, event_type: str, payload: dict) -> None:
        self._cursor += 1
        conn.sendall(_make_event(self._cursor, event_type, payload, self.project_id).encode())


def main() -> None:
    """CLI entry point for standalone testing."""
    socket_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/coord-fake.sock"
    project_id = sys.argv[2] if len(sys.argv) > 2 else "proj-a"

    server = FakeSupervisor(socket_path, project_id)
    server.start()
    print(f"FakeSupervisor listening on {socket_path} (project={project_id})")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()


if __name__ == "__main__":
    main()
