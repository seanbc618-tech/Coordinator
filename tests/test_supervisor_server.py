import socket
import stat
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

from local_cli_coordinator.supervisor_identity import RUNTIME_COMPATIBILITY
from local_cli_coordinator.supervisor_protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    RequestEnvelope,
    ResponseEnvelope,
)
from local_cli_coordinator.supervisor_server import (
    SupervisorServer,
    SupervisorServerError,
    SupervisorTransportError,
    send_request,
)


@dataclass(frozen=True)
class _TestPaths:
    state_dir: Path

    @property
    def socket(self) -> Path:
        return self.state_dir / "coordinator.sock"

    @property
    def lock(self) -> Path:
        return self.state_dir / "supervisor.lock"


def _ping_request(request_id: str = "ping-1") -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id,
        project_id=None,
        method="system.ping",
        params={},
    )


class SupervisorServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = _TestPaths(Path(self._tmpdir.name) / "state")
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _start_server(self, handler=None) -> SupervisorServer:
        server = SupervisorServer(self.paths, handler=handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        deadline = time.time() + 2.0
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                send_request(
                    self.paths.socket,
                    _ping_request("startup"),
                    timeout=0.2,
                )
                return server
            except SupervisorTransportError as exc:
                last_error = exc
                time.sleep(0.01)
        self.fail(f"server did not become ready: {last_error}")

    def _wait_until_stopped(self) -> None:
        deadline = time.time() + 2.0
        while time.time() < deadline and (
            self.paths.socket.exists() or self.paths.lock.exists()
        ):
            time.sleep(0.01)
        self.assertFalse(self.paths.socket.exists())
        self.assertFalse(self.paths.lock.exists())

    def test_start_ping_and_clean_shutdown(self) -> None:
        server = self._start_server()
        response = send_request(self.paths.socket, _ping_request())
        self.assertTrue(response.ok)
        self.assertIsNotNone(response.result)
        result = response.result or {}
        self.assertTrue(result.get("pong"))
        self.assertEqual(result.get("runtime_compatibility"), RUNTIME_COMPATIBILITY)
        self.assertIn("pid", result)
        self.assertIn("capabilities", result)
        self.assertIn("started_at", result)
        self.assertIn("active_workers", result)

        server.request_shutdown()
        self._wait_until_stopped()

    def test_socket_is_created_with_private_mode(self) -> None:
        self._start_server()
        mode = stat.S_IMODE(self.paths.socket.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_second_server_start_is_rejected(self) -> None:
        self._start_server()
        second = SupervisorServer(self.paths, handler=None)
        with self.assertRaises(SupervisorServerError):
            second.serve_forever()

    def test_stale_socket_is_removed_when_unowned(self) -> None:
        self.paths.socket.write_text("", encoding="utf-8")
        server = self._start_server()
        response = send_request(self.paths.socket, _ping_request("stale"))
        self.assertTrue(response.ok)
        server.request_shutdown()
        self._wait_until_stopped()

    def test_malformed_request_does_not_stop_server(self) -> None:
        self._start_server()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(self.paths.socket))
            client.sendall(b"not-json\n")
            client.settimeout(2.0)
            line = client.makefile("rb").readline()
        self.assertTrue(line)
        response = send_request(self.paths.socket, _ping_request("ping-2"))
        self.assertTrue(response.ok)

    def test_protocol_mismatch_returns_error_response(self) -> None:
        self._start_server()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(self.paths.socket))
            payload = (
                '{"type":"request","protocol_version":99,'
                '"request_id":"bad","project_id":null,'
                '"method":"system.ping","params":{}}\n'
            )
            client.sendall(payload.encode("utf-8"))
            client.settimeout(2.0)
            line = client.makefile("rb").readline().decode("utf-8").strip()
        response = ResponseEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id="bad",
            ok=False,
            result=None,
            error="unsupported protocol_version 99; expected 1",
        )
        from local_cli_coordinator.supervisor_protocol import decode_envelope

        decoded = decode_envelope(line)
        self.assertIsInstance(decoded, ResponseEnvelope)
        self.assertFalse(decoded.ok)
        self.assertEqual(decoded.request_id, response.request_id)

    def test_custom_handler_is_invoked(self) -> None:
        def handler(request: RequestEnvelope) -> ResponseEnvelope:
            return ResponseEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request.request_id,
                ok=True,
                result={"handled": request.method},
                error=None,
            )

        self._start_server(handler=handler)
        request = RequestEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id="req-custom",
            project_id="project-a",
            method="project.status",
            params={},
        )
        response = send_request(self.paths.socket, request)
        self.assertTrue(response.ok)
        self.assertEqual(response.result, {"handled": "project.status"})

    def test_send_request_timeout_raises_transport_error(self) -> None:
        missing = self.paths.state_dir / "missing.sock"
        with self.assertRaises(SupervisorTransportError):
            send_request(missing, _ping_request(), timeout=0.2)

    def test_system_shutdown_stops_server(self) -> None:
        server = self._start_server()
        request = RequestEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id="shutdown-1",
            project_id=None,
            method="system.shutdown",
            params={},
        )
        response = send_request(self.paths.socket, request)
        self.assertTrue(response.ok)
        self.assertEqual(response.result, {"shutdown": True})

        self._wait_until_stopped()


if __name__ == "__main__":
    unittest.main()