"""Fake Supervisor for TUI integration testing.

Listens on a Unix socket, accepts Supervisor protocol v1 messages,
and responds with deterministic events for PTY verification.

Enhanced with:
- Thread-safe request log for asserting RPC calls.
- Foreign-project event injection for defense-in-depth testing.
- Work simulation that continues after client disconnect.
- Synchronized observations via threading.Event.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from local_cli_coordinator.supervisor_identity import build_ping_result


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
    """A minimal Supervisor that responds to TUI requests.

    Thread-safe request log allows tests to assert which RPCs were sent
    without relying on arbitrary sleeps or reading private state during
    mutation.
    """

    def __init__(self, socket_path: str, project_id: str = "proj-a") -> None:
        self.socket_path = socket_path
        self.project_id = project_id
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._cursor = 0
        self._request_count = 0
        self._lock = threading.Lock()
        # Thread-safe request log: list of (method, params) tuples.
        self._request_log: list[tuple[str, dict]] = []
        self._request_event = threading.Event()
        # Phase 5.4: track execution_policy and context_files per chat.send.
        self._execution_policies: list[dict] = []
        self._context_files_received: list[list[dict]] = []
        # Active client connections for disconnect simulation.
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()
        # Work simulation state.
        self._work_counter = 0
        self._work_active = False
        self._work_event = threading.Event()
        # Cursor replay state for reconnect tests: (cursor, event_type, payload).
        self._event_history: list[tuple[int, str, dict]] = []

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
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        path = Path(self.socket_path)
        if path.exists():
            path.unlink()

    def drain_requests(self) -> list[tuple[str, dict]]:
        """Return and clear the request log. Thread-safe."""
        with self._lock:
            result = list(self._request_log)
            self._request_log.clear()
            return result

    def reset_session(self) -> None:
        """Clear replay history so PTY tests start from a clean event stream."""
        with self._lock:
            self._event_history.clear()
            self._cursor = 0

    def count_requests(self, method: str) -> int:
        """Count logged requests for a method without clearing the log."""
        with self._lock:
            return sum(1 for m, _ in self._request_log if m == method)

    def wait_for_request_method(self, method: str, timeout: float = 5.0) -> bool:
        """Block until at least one request for method arrives."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if any(m == method for m, _ in self._request_log):
                    return True
            time.sleep(0.05)
        return False

    def wait_for_request(self, timeout: float = 5.0) -> bool:
        """Block until at least one request arrives. Thread-safe."""
        return self._request_event.wait(timeout=timeout)

    def clear_request_event(self) -> None:
        """Reset the request event for the next wait."""
        self._request_event.clear()

    def get_work_counter(self) -> int:
        """Return the current work simulation counter. Thread-safe."""
        with self._lock:
            return self._work_counter

    def drain_execution_policies(self) -> list[dict]:
        """Return and clear captured execution_policy values. Thread-safe."""
        with self._lock:
            result = list(self._execution_policies)
            self._execution_policies.clear()
            return result

    def drain_context_files(self) -> list[list[dict]]:
        """Return and clear captured context_files lists. Thread-safe."""
        with self._lock:
            result = list(self._context_files_received)
            self._context_files_received.clear()
            return result

    def is_work_active(self) -> bool:
        """Return whether work simulation is running. Thread-safe."""
        with self._lock:
            return self._work_active

    def disconnect_clients(self) -> None:
        """Force-close all connected clients to simulate connection drop."""
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                    c.close()
                except OSError:
                    pass
            self._clients.clear()

    def send_foreign_event(self, event_type: str, payload: dict, project_id: str) -> None:
        """Send an event with a foreign project_id to all connected clients."""
        with self._clients_lock:
            clients = list(self._clients)
        for conn in clients:
            try:
                self._send_event(conn, event_type, payload, project_id)
            except OSError:
                pass

    def start_work(self) -> None:
        """Start simulating ongoing work that advances independently."""
        with self._lock:
            self._work_active = True
            self._work_counter = 0
        threading.Thread(target=self._work_loop, daemon=True).start()

    def stop_work(self) -> None:
        """Stop work simulation."""
        with self._lock:
            self._work_active = False

    def wait_work_counter(self, target: int, timeout: float = 10.0) -> bool:
        """Block until work_counter >= target."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._work_counter >= target:
                    return True
            time.sleep(0.05)
        return False

    def _work_loop(self) -> None:
        """Background loop that simulates ongoing work."""
        while True:
            with self._lock:
                if not self._work_active:
                    break
                self._work_counter += 1
                counter = self._work_counter
            self._work_event.set()
            time.sleep(0.1)

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server.accept()
                with self._clients_lock:
                    self._clients.append(conn)
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
            with self._clients_lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            conn.close()

    def _process_message(self, conn: socket.socket, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if msg.get("type") != "request":
            return

        with self._lock:
            self._request_count += 1
            self._request_log.append((msg.get("method", ""), msg.get("params", {})))
        self._request_event.set()

        request_id = msg.get("request_id", "")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "system.ping":
            with self._clients_lock:
                active_workers = len(self._clients)
            self._respond(
                conn,
                request_id,
                build_ping_result(
                    pid=os.getpid(),
                    started_at=self._started_at,
                    active_workers=active_workers,
                    extra={"projects": {self.project_id: {"ready": 1}}},
                ),
            )

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
            if not self._event_history:
                self._emit_initial_events(conn)
            else:
                # Reconnect: replay full history with original cursors so tests
                # can assert client-side dedup.
                self._replay_all_events(conn)

        elif method == "events.subscribe.replay":
            after_cursor = params.get("after_cursor", 0)
            self._respond(conn, request_id)
            self._replay_events(conn, after_cursor)

        elif method == "chat.send":
            text = params.get("text", "")
            goal_id = params.get("goal_id", 1)
            # Phase 5.4: capture execution_policy and context_files.
            exec_policy = params.get("execution_policy")
            ctx_files = params.get("context_files", [])
            with self._lock:
                if exec_policy is not None:
                    self._execution_policies.append(exec_policy)
                self._context_files_received.append(ctx_files)
            if "你好" in text or "任务" in text:
                coordinator_reply = "你好！有什么可以帮你的吗？"
                intent = "conversation"
                admitted = 0
                accepted_task_ids: list[str] = []
            else:
                coordinator_reply = (
                    "Understood — I'll queue the requested checks and "
                    "report back when they finish."
                )
                intent = "task_request"
                admitted = 1
                accepted_task_ids = ["task-cmd-001"]
            progress_summary = "Queued baseline acceptance checks"
            self._respond(conn, request_id, {
                "received": True,
                "goal_id": goal_id,
                "commander_run_id": 42,
                "admitted": admitted,
                "rejected": 0,
                "user_reply": coordinator_reply,
                "intent": intent,
                "accepted_task_ids": accepted_task_ids,
            })
            self._send_event(conn, "chat.message", {
                "role": "user",
                "text": text,
                "goal_id": goal_id,
            })
            self._send_event(conn, "chat.message", {
                "role": "coordinator",
                "text": coordinator_reply,
                "goal_id": goal_id,
            })
            if admitted:
                self._send_event(conn, "task.created", {
                    "task_id": "task-cmd-001",
                    "goal_id": goal_id,
                    "title": "Run baseline acceptance checks",
                    "state": "ready",
                    "goal": "Run verification commands without changing code",
                    "acceptance_criteria": "Record pytest and ruff results",
                    "verification_commands": ["uv run pytest -q", "uv run ruff check src/ tests/"],
                })
            self._send_event(conn, "commander.completed", {
                "goal_id": goal_id,
                "run_id": 42,
                "intent": intent,
                "user_reply": coordinator_reply,
                "progress_summary": progress_summary,
                "admitted": admitted,
                "rejected": 0,
                "accepted_task_ids": accepted_task_ids,
                "rejection_reasons": [],
                "succeeded": True,
            })

        elif method == "project.status":
            self._respond(conn, request_id, {
                "project_id": self.project_id,
                "status": "active",
                "tasks": {"ready": 1, "running": 0, "done": 3},
            })

        elif method == "project.tasks":
            self._respond(conn, request_id, {
                "tasks": [
                    {
                        "id": "task-001",
                        "title": "Implement auth",
                        "state": "done",
                        "goal": "Ship auth",
                        "latest_note": "completed",
                    },
                    {
                        "id": "task-002",
                        "title": "Add tests",
                        "state": "ready",
                        "goal": "Add coverage",
                        "latest_note": None,
                    },
                ],
            })

        elif method == "project.task":
            task_id = params.get("args", "task-baseline-001")
            self._respond(conn, request_id, {
                "task": {
                    "id": task_id,
                    "title": "Run baseline acceptance checks",
                    "state": "failed",
                    "goal": "Run verification commands without changing code",
                    "verification_commands": ["uv run pytest -q", "uv run ruff check src/ tests/"],
                    "worktree_path": "/tmp/worktree",
                },
                "latest_event": {
                    "old_state": "running",
                    "new_state": "failed",
                    "note": "no changed files",
                },
                "latest_attempt": {
                    "agent_id": "claude_worker",
                    "exit_code": 0,
                    "result_class": "interactive_blocked",
                    "log_path": "/tmp/agent.log",
                },
                "artifacts": [{"kind": "agent_log", "path": "/tmp/agent.log"}],
            })

        elif method in (
            "project.goals",
            "project.goal.resume",
            "project.goal.fork",
        ):
            self._handle_goal_session_rpc(
                conn,
                request_id,
                method,
                params,
                msg.get("project_id"),
            )

        elif method in ("project.pause", "project.resume", "project.stop"):
            self._respond(conn, request_id, {"ok": True})

        elif method == "system.shutdown":
            self._respond(conn, request_id, {"shutting_down": True})

        else:
            self._respond(conn, request_id, {"method": method})

    def _respond(
        self,
        conn: socket.socket,
        request_id: str,
        result: dict | None = None,
        *,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        try:
            conn.sendall(
                _make_response(request_id, ok=ok, result=result, error=error).encode()
            )
        except OSError:
            pass

    def _handle_goal_session_rpc(
        self,
        conn: socket.socket,
        request_id: str,
        method: str,
        params: dict,
        project_id: str | None,
    ) -> None:
        home = os.environ.get("COORDINATOR_HOME")
        if not home:
            self._respond(
                conn,
                request_id,
                ok=False,
                error="COORDINATOR_HOME not set",
            )
            return

        from local_cli_coordinator.db import connect, init_db
        from local_cli_coordinator.goal_sessions import (
            GoalSessionError,
            fork_project_goal,
            format_goal_session_error,
            list_project_goal_candidates,
            resume_project_goal,
        )
        from local_cli_coordinator.runtime_paths import RuntimePaths

        home_path = Path(home)
        paths = RuntimePaths(
            home_path / "config",
            home_path / "data",
            home_path / "state",
        )
        db = connect(paths.database)
        try:
            init_db(db)
            effective_project_id = project_id
            if not effective_project_id:
                row = db.execute("select id from projects limit 1").fetchone()
                effective_project_id = row["id"] if row else self.project_id

            if method == "project.goals":
                candidates = list_project_goal_candidates(
                    db, effective_project_id
                )
                self._respond(conn, request_id, {"candidates": candidates})
                return

            if method == "project.goal.resume":
                try:
                    goal_id = int(params.get("goal_id"))
                except (TypeError, ValueError):
                    self._respond(
                        conn,
                        request_id,
                        ok=False,
                        error="goal_id is required",
                    )
                    return
                try:
                    goal = resume_project_goal(
                        db, effective_project_id, goal_id
                    )
                except GoalSessionError as exc:
                    self._respond(
                        conn,
                        request_id,
                        ok=False,
                        error=format_goal_session_error(exc),
                    )
                    return
                self._respond(
                    conn,
                    request_id,
                    {
                        "goal_id": goal["id"],
                        "status": goal["status"],
                        "title": goal["title"],
                    },
                )
                return

            if method == "project.goal.fork":
                instruction = params.get("instruction", "")
                if not isinstance(instruction, str):
                    instruction = ""
                try:
                    source_goal_id = int(params.get("goal_id"))
                except (TypeError, ValueError):
                    self._respond(
                        conn,
                        request_id,
                        ok=False,
                        error="goal_id is required",
                    )
                    return
                try:
                    new_goal_id = fork_project_goal(
                        db,
                        effective_project_id,
                        source_goal_id,
                        instruction,
                    )
                except GoalSessionError as exc:
                    self._respond(
                        conn,
                        request_id,
                        ok=False,
                        error=format_goal_session_error(exc),
                    )
                    return
                self._respond(
                    conn,
                    request_id,
                    {"goal_id": new_goal_id, "status": "draft"},
                )
        finally:
            db.close()

    def _emit_initial_events(self, conn: socket.socket) -> None:
        """Send the deterministic initial event sequence on first subscribe."""
        self._send_event(conn, "tick_scheduled", {"project_id": self.project_id, "reason": "ready"})
        self._send_event(conn, "task.created", {"task_id": "task-001", "title": "Implement auth", "agent": "worker"})
        self._send_event(conn, "task.stage", {"task_id": "task-001", "stage": "running"})
        self._send_event(conn, "task.command", {"task_id": "task-001", "command": "npm test"})
        self._send_event(conn, "task.output", {"task_id": "task-001", "output": "pass 1/3\npass 2/3\npass 3/3\n"})
        self._send_event(conn, "task.verification", {"task_id": "task-001", "result": "passed"})
        self._send_event(conn, "task.done", {"task_id": "task-001", "result": "completed"})
        self._send_event(conn, "chat.message", {"role": "coordinator", "text": "Task completed successfully!"})

    def _replay_events(self, conn: socket.socket, after_cursor: int) -> None:
        """Replay stored events with cursor > after_cursor (fixed cursors)."""
        with self._lock:
            history = list(self._event_history)
        for cursor, event_type, payload in history:
            if cursor > after_cursor:
                try:
                    conn.sendall(
                        _make_event(cursor, event_type, payload, self.project_id).encode()
                    )
                except OSError:
                    pass

    def _replay_all_events(self, conn: socket.socket) -> None:
        """Replay every stored event with its original cursor."""
        with self._lock:
            history = list(self._event_history)
        for cursor, event_type, payload in history:
            try:
                conn.sendall(
                    _make_event(cursor, event_type, payload, self.project_id).encode()
                )
            except OSError:
                pass

    def _send_event(self, conn: socket.socket, event_type: str, payload: dict, project_id: str | None = None) -> None:
        with self._lock:
            self._cursor += 1
            cursor = self._cursor
            self._event_history.append((cursor, event_type, payload))
        try:
            conn.sendall(_make_event(cursor, event_type, payload, project_id or self.project_id).encode())
        except OSError:
            pass


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
