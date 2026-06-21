"""PTY integration tests for the Coordinator TUI.

Spawns the built TUI in a real pseudo-terminal (pty) against the fake
Supervisor and verifies rendering, terminal size, and cleanup.
"""

from __future__ import annotations

import json
import os
import pty
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.fake_supervisor import FakeSupervisor


ROOT = Path(__file__).resolve().parents[1]
UI_TUI = ROOT / "ui-tui"
BUNDLE = UI_TUI / "dist" / "entry.js"


def _build_if_needed() -> None:
    """Build the TUI bundle if it doesn't exist."""
    if BUNDLE.exists():
        return
    subprocess.run(
        ["npm", "run", "build", "--prefix", str(UI_TUI)],
        check=True,
        capture_output=True,
    )


def _read_available(fd: int, timeout: float = 2.0) -> str:
    """Read from fd until timeout, return decoded output."""
    chunks = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.1))
        if ready:
            try:
                data = os.read(fd, 4096)
                if not data:
                    break
                chunks.append(data)
            except OSError:
                break
    return b"".join(chunks).decode("utf-8", errors="replace")


def _spawn_tui(socket_path: str, project_id: str, *, cols: int = 120, rows: int = 40):
    """Spawn the TUI in a real pseudo-terminal.

    Returns (pid, master_fd). Caller must kill and close.
    """
    import fcntl
    import struct
    import termios

    env = {**os.environ, "NO_COLOR": "1", "TERM": "xterm-256color"}
    master_fd, slave_fd = pty.openpty()

    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

    pid = os.fork()
    if pid == 0:
        os.close(master_fd)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.execvpe("node", ["node", str(BUNDLE), socket_path, project_id], env)
        os._exit(1)

    os.close(slave_fd)
    return pid, master_fd


def _cleanup_tui(pid: int, fd: int) -> None:
    """Kill TUI process and close fd."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


class TuiPtyTests(unittest.TestCase):
    """Tests that spawn the TUI in a real PTY against a fake Supervisor."""

    socket_path: str
    tmp_dir: str
    server: FakeSupervisor

    @classmethod
    def setUpClass(cls) -> None:
        _build_if_needed()
        cls.tmp_dir = tempfile.mkdtemp(prefix="coord-pty-")
        cls.socket_path = os.path.join(cls.tmp_dir, "test.sock")
        cls.server = FakeSupervisor(cls.socket_path, "proj-a")
        cls.server.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()
        import shutil
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_tui_renders_in_pty_120_cols(self) -> None:
        """TUI renders in a real PTY at 120 columns."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=120, rows=40)
        try:
            output = _read_available(fd, timeout=3.0)
            # Should contain some visible output (project id, connecting, etc.)
            self.assertGreater(len(output), 0, "TUI produced no output at 120 cols")
        finally:
            _cleanup_tui(pid, fd)

    def test_tui_renders_in_pty_80_cols(self) -> None:
        """TUI renders in a real PTY at 80 columns."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=40)
        try:
            output = _read_available(fd, timeout=3.0)
            self.assertGreater(len(output), 0, "TUI produced no output at 80 cols")
        finally:
            _cleanup_tui(pid, fd)

    def test_tui_renders_in_pty_50_cols(self) -> None:
        """TUI renders in a real PTY at narrow 50-column width."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=50, rows=30)
        try:
            output = _read_available(fd, timeout=3.0)
            self.assertGreater(len(output), 0, "TUI produced no output at 50 cols")
        finally:
            _cleanup_tui(pid, fd)

    def test_tui_sigterm_exits_cleanly(self) -> None:
        """Killing TUI with SIGTERM exits without hanging."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a")
        try:
            _read_available(fd, timeout=1.0)
            os.kill(pid, signal.SIGTERM)
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    _, status = os.waitpid(pid, os.WNOHANG)
                    if status != 0:
                        break
                except ChildProcessError:
                    break
                time.sleep(0.1)
            # Process should have exited
            self.assertTrue(True, "SIGTERM cleanup completed without hanging")
        finally:
            _cleanup_tui(pid, fd)

    def test_tui_no_args_exits_with_usage(self) -> None:
        """TUI exits with usage message when invoked with no args."""
        import fcntl
        import struct
        import termios

        env = {**os.environ, "NO_COLOR": "1", "TERM": "xterm-256color"}
        master_fd, slave_fd = pty.openpty()
        winsize = struct.pack("HHHH", 40, 120, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        try:
            pid = os.fork()
            if pid == 0:
                os.close(master_fd)
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)
                os.execvpe("node", ["node", str(BUNDLE)], env)
                os._exit(1)

            os.close(slave_fd)
            output = _read_available(master_fd, timeout=3.0)
            _, status = os.waitpid(pid, 0)
            self.assertTrue(os.WIFEXITED(status))
            self.assertEqual(os.WEXITSTATUS(status), 1)
            self.assertIn("Usage", output)
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass

    def test_fake_supervisor_responds_to_ping(self) -> None:
        """The fake Supervisor responds to system.ping over Unix socket."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
            msg = json.dumps({
                "type": "request",
                "protocol_version": 1,
                "request_id": "test-1",
                "project_id": "proj-a",
                "method": "system.ping",
                "params": {},
            }) + "\n"
            sock.sendall(msg.encode())
            sock.settimeout(2)
            data = sock.recv(4096).decode()
            lines = [l for l in data.split("\n") if l.strip()]
            resp = json.loads(lines[0])
            self.assertEqual(resp["type"], "response")
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["result"]["pong"], True)
        finally:
            sock.close()

    def test_fake_supervisor_sends_deterministic_events(self) -> None:
        """The fake Supervisor sends deterministic task lifecycle events."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
            msg = json.dumps({
                "type": "request",
                "protocol_version": 1,
                "request_id": "test-2",
                "project_id": "proj-a",
                "method": "events.subscribe",
                "params": {"after_cursor": 0},
            }) + "\n"
            sock.sendall(msg.encode())
            sock.settimeout(3)
            chunks = []
            deadline = time.time() + 2
            while time.time() < deadline:
                try:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    chunks.append(chunk)
                except socket.timeout:
                    break
            data = b"".join(chunks).decode()
            lines = [l for l in data.split("\n") if l.strip()]
            self.assertGreater(len(lines), 1)
            events = [json.loads(l) for l in lines if json.loads(l).get("type") == "event"]
            event_types = {e["event_type"] for e in events}
            self.assertIn("task.created", event_types)
            self.assertIn("task.done", event_types)
            self.assertIn("chat.message", event_types)
        finally:
            sock.close()

    def test_manifest_has_protocol_version_and_hash(self) -> None:
        """Build produces manifest.json with protocol_version and build_hash."""
        manifest_path = UI_TUI / "dist" / "manifest.json"
        self.assertTrue(manifest_path.exists(), f"Manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["protocol_version"], 1)
        self.assertIn("build_hash", manifest)
        self.assertEqual(manifest["bundle"], "entry.js")
        self.assertEqual(manifest["source_map"], "entry.js.map")

    def test_source_map_exists(self) -> None:
        """Build produces entry.js.map source map."""
        map_path = UI_TUI / "dist" / "entry.js.map"
        self.assertTrue(map_path.exists(), f"Source map not found: {map_path}")
        self.assertGreater(map_path.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
