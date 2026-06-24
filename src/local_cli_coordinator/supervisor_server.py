from __future__ import annotations

import errno
import json
import os
import queue
import socket
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from local_cli_coordinator.locks import LockInfo, _read_lock, acquire_lock_at, release_lock_at
from local_cli_coordinator.supervisor_identity import build_ping_result
from local_cli_coordinator.supervisor_protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    RequestEnvelope,
    ResponseEnvelope,
    EventEnvelope,
    decode_envelope,
    encode_envelope,
)


class RuntimePathsLike(Protocol):
    @property
    def socket(self) -> Path:
        ...

    @property
    def lock(self) -> Path:
        ...


Handler = Callable[[RequestEnvelope], ResponseEnvelope]


class SupervisorServerError(RuntimeError):
    """Raised when the Supervisor server cannot start or acquire state."""


class SupervisorTransportError(OSError):
    """Raised when a Supervisor socket request fails."""


class SupervisorServer:
    def __init__(
        self,
        paths: RuntimePathsLike,
        *,
        handler: Handler | None = None,
        methods: Any | None = None,
    ) -> None:
        self._paths = paths
        self._handler = handler
        self._methods = methods  # for poll_live_events / unsubscribe
        self._shutdown = threading.Event()
        self._server_socket: socket.socket | None = None
        self._client_threads: list[threading.Thread] = []
        self._threads_lock = threading.Lock()
        self._started_at: str | None = None
        self._server_pid: int | None = None

    def request_shutdown(self) -> None:
        self._shutdown.set()
        server_socket = self._server_socket
        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass

    def owns_runtime(self) -> bool:
        """Return True when this process still owns the Supervisor lock and socket."""
        if self._server_pid is None:
            return False
        lock = _read_lock(self._paths.lock)
        if lock is None or lock.pid != self._server_pid:
            return False
        return self._paths.socket.exists()

    def serve_forever(self) -> None:
        lock_result = acquire_lock_at(self._paths.lock)
        if isinstance(lock_result, str):
            raise SupervisorServerError(lock_result)

        self._server_pid = os.getpid()
        self._started_at = datetime.now(timezone.utc).isoformat()

        server_socket: socket.socket | None = None
        try:
            self._cleanup_stale_socket(self._paths.socket)
            server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server_socket = server_socket
            server_socket.bind(str(self._paths.socket))
            server_socket.listen(8)
            self._paths.socket.chmod(0o600)

            while not self._shutdown.is_set():
                if not self.owns_runtime():
                    self.request_shutdown()
                    break
                try:
                    client_socket, _address = server_socket.accept()
                except OSError:
                    if self._shutdown.is_set():
                        break
                    raise

                thread = threading.Thread(
                    target=self._serve_client,
                    args=(client_socket,),
                    daemon=True,
                )
                with self._threads_lock:
                    self._client_threads.append(thread)
                thread.start()
        finally:
            if server_socket is not None:
                try:
                    server_socket.close()
                except OSError:
                    pass
            self._server_socket = None
            self._paths.socket.unlink(missing_ok=True)
            release_lock_at(self._paths.lock)

    def _serve_client(self, client_socket: socket.socket) -> None:
        """Serve a single client connection.

        After a successful events.subscribe response, polls the methods'
        live_queues for events and forwards them to the client.
        On disconnect, calls methods.unsubscribe to clean up.
        """
        active_sub_id: str | None = None

        try:
            with client_socket:
                buf = b""
                while not self._shutdown.is_set():
                    # Forward live events from methods' queue
                    if active_sub_id and self._methods:
                        events = self._methods.poll_live_events(active_sub_id)
                        for evt in events:
                            proto = EventEnvelope(
                                protocol_version=PROTOCOL_VERSION,
                                project_id=evt.get("project_id", ""),
                                cursor=evt.get("cursor", 0),
                                event_type=evt.get("type", ""),
                                payload=evt.get("payload", {}),
                            )
                            encoded = encode_envelope(proto) + "\n"
                            client_socket.sendall(encoded.encode("utf-8"))

                    # Wait for incoming data with timeout
                    client_socket.settimeout(0.2)
                    try:
                        chunk = client_socket.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break

                    if not chunk:
                        break

                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line:
                            response = self._handle_raw_request(line.decode("utf-8").strip())
                            resp_data = encode_envelope(response) + "\n"
                            client_socket.sendall(resp_data.encode("utf-8"))

                            # Track subscription for live event forwarding
                            if (response.ok and response.result
                                    and "subscription_id" in response.result):
                                active_sub_id = response.result["subscription_id"]
        except OSError:
            return
        finally:
            # Clean up subscription on disconnect
            if active_sub_id and self._methods:
                self._methods.unsubscribe(active_sub_id)

    def _handle_raw_request(self, raw: str) -> ResponseEnvelope:
        request_id = _extract_request_id(raw)
        try:
            envelope = decode_envelope(raw)
            if not isinstance(envelope, RequestEnvelope):
                raise ProtocolError("expected request envelope")
            request_id = envelope.request_id
            return self._dispatch(envelope)
        except ProtocolError as exc:
            return ResponseEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request_id,
                ok=False,
                result=None,
                error=str(exc),
            )

    def _build_ping_result(self, *, active_workers: int = 0, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        pid = self._server_pid if self._server_pid is not None else os.getpid()
        started_at = self._started_at
        if started_at is None:
            started_at = datetime.now(timezone.utc).isoformat()
        return build_ping_result(
            pid=pid,
            started_at=started_at,
            active_workers=active_workers,
            extra=extra,
        )

    def _dispatch(self, request: RequestEnvelope) -> ResponseEnvelope:
        if request.method == "system.ping":
            return ResponseEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request.request_id,
                ok=True,
                result=self._build_ping_result(),
                error=None,
            )

        if request.method == "system.shutdown":
            self.request_shutdown()
            return ResponseEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request.request_id,
                ok=True,
                result={"shutdown": True},
                error=None,
            )

        if self._handler is None:
            return ResponseEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request.request_id,
                ok=False,
                result=None,
                error=f"unsupported method {request.method!r}",
            )

        return self._handler(request)

    @staticmethod
    def _cleanup_stale_socket(socket_path: Path) -> None:
        if not socket_path.exists():
            return

        if not socket_path.is_socket():
            socket_path.unlink(missing_ok=True)
            return

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(str(socket_path))
        except OSError as exc:
            if exc.errno in {errno.ECONNREFUSED, errno.ENOENT, errno.ENOTSOCK}:
                socket_path.unlink(missing_ok=True)
                return
            if isinstance(exc, ConnectionRefusedError):
                socket_path.unlink(missing_ok=True)
                return
            raise SupervisorServerError("supervisor socket is already in use") from exc
        finally:
            probe.close()

        raise SupervisorServerError("supervisor socket is already in use")


def _extract_request_id(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "unknown"
    if not isinstance(payload, dict):
        return "unknown"
    request_id = payload.get("request_id")
    if isinstance(request_id, str) and request_id.strip():
        return request_id.strip()
    return "unknown"


def _recv_line(sock: socket.socket) -> bytes:
    """Read one newline-terminated line from a socket using recv."""
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            break
    line, _, _ = buf.partition(b"\n")
    return line


def send_request(
    socket_path: Path,
    request: RequestEnvelope,
    *,
    timeout: float = 2.0,
) -> ResponseEnvelope:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(socket_path))
            payload = encode_envelope(request) + "\n"
            client.sendall(payload.encode("utf-8"))
            line = _recv_line(client)
            if not line:
                raise SupervisorTransportError(
                    "supervisor closed connection without response"
                )
            envelope = decode_envelope(line.decode("utf-8").strip())
            if not isinstance(envelope, ResponseEnvelope):
                raise SupervisorTransportError("expected response envelope")
            return envelope
    except (TimeoutError, OSError) as exc:
        if isinstance(exc, SupervisorTransportError):
            raise
        raise SupervisorTransportError(str(exc)) from exc
