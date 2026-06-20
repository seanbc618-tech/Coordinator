from __future__ import annotations

import errno
import json
import queue
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from local_cli_coordinator.locks import LockInfo, acquire_lock_at, release_lock_at
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
        broker: Any | None = None,
    ) -> None:
        self._paths = paths
        self._handler = handler
        self._broker = broker
        self._shutdown = threading.Event()
        self._server_socket: socket.socket | None = None
        self._client_threads: list[threading.Thread] = []
        self._threads_lock = threading.Lock()
        # Event queues for subscribed clients: client_id → queue
        self._event_queues: dict[int, queue.Queue] = {}
        self._event_queues_lock = threading.Lock()

    def request_shutdown(self) -> None:
        self._shutdown.set()
        server_socket = self._server_socket
        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass

    def push_event(self, event: EventEnvelope) -> None:
        """Push an event to all subscribed client queues."""
        encoded = encode_envelope(event) + "\n"
        with self._event_queues_lock:
            for q in self._event_queues.values():
                try:
                    q.put_nowait(encoded)
                except queue.Full:
                    pass

    def serve_forever(self) -> None:
        lock_result = acquire_lock_at(self._paths.lock)
        if isinstance(lock_result, str):
            raise SupervisorServerError(lock_result)

        server_socket: socket.socket | None = None
        try:
            self._cleanup_stale_socket(self._paths.socket)
            server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server_socket = server_socket
            server_socket.bind(str(self._paths.socket))
            server_socket.listen(8)
            self._paths.socket.chmod(0o600)

            while not self._shutdown.is_set():
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
        client_id = id(client_socket)
        event_q: queue.Queue = queue.Queue(maxsize=256)

        with self._event_queues_lock:
            self._event_queues[client_id] = event_q

        try:
            with client_socket:
                buf = b""
                while not self._shutdown.is_set():
                    # Check for outgoing events (non-blocking)
                    try:
                        while True:
                            event_data = event_q.get_nowait()
                            client_socket.sendall(event_data.encode("utf-8"))
                    except queue.Empty:
                        pass

                    # Wait for incoming data with timeout (allows checking events)
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

                            # If this was a subscribe response, register the
                            # client to receive live events via the handler
                            self._maybe_register_subscription(response, event_q)
        except OSError:
            return
        finally:
            with self._event_queues_lock:
                self._event_queues.pop(client_id, None)

    def _maybe_register_subscription(
        self, response: ResponseEnvelope, event_q: queue.Queue
    ) -> None:
        """If the response indicates a successful subscribe, register a
        broker callback that forwards events to this client's queue."""
        if not response.ok or response.result is None:
            return
        if "subscription_id" not in response.result:
            return
        if self._broker is None:
            return

        # Extract project_id from the response context (stored in result)
        project_id = response.result.get("project_id")
        if not project_id:
            return

        # Register a broker callback that writes events to this client's queue
        def _forward_to_client(envelope):
            try:
                # Convert broker EventEnvelope to protocol EventEnvelope
                proto_event = EventEnvelope(
                    protocol_version=PROTOCOL_VERSION,
                    project_id=envelope.project_id,
                    cursor=envelope.cursor,
                    event_type=envelope.event_type,
                    payload=envelope.payload,
                )
                encoded = encode_envelope(proto_event) + "\n"
                event_q.put_nowait(encoded)
            except queue.Full:
                pass

        self._broker.subscribe(project_id, _forward_to_client)

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

    def _dispatch(self, request: RequestEnvelope) -> ResponseEnvelope:
        if request.method == "system.ping":
            return ResponseEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request.request_id,
                ok=True,
                result={"pong": True},
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
