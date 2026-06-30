"""PTY integration tests for the Coordinator TUI.

Spawns the built TUI in a real pseudo-terminal (pty) against the fake
Supervisor and verifies rendering, terminal size, cleanup, and
interactive behavior.

P1-2: Real assertions on child exit status and rendered content.
P1-3: Real PTY keystroke tests for composer and detach.
P1-4: Gate E scenarios — resize, reconnect, active-work termination, cleanup.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pty
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
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


def _pty_term_flags(fd: int) -> tuple[bool, bool]:
    """Return (ICANON, ECHO) for the PTY slave line discipline."""
    attrs = termios.tcgetattr(fd)
    lflag = attrs[3]
    return bool(lflag & termios.ICANON), bool(lflag & termios.ECHO)


def _assert_pty_terminal_restored(fd: int) -> None:
    """Assert canonical mode and echo are restored on the PTY."""
    icanon, echo = _pty_term_flags(fd)
    if not icanon:
        raise AssertionError("ICANON not restored after detach")
    if not echo:
        raise AssertionError("ECHO not restored after detach")


def _wait_for_exit(pid: int, deadline: float) -> int | None:
    """Wait for pid to exit before deadline. Returns exit code or None."""
    while time.time() < deadline:
        try:
            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid != 0:
                if os.WIFEXITED(status):
                    return os.WEXITSTATUS(status)
                if os.WIFSIGNALED(status):
                    return -os.WTERMSIG(status)
                return status
        except ChildProcessError:
            return 0
        time.sleep(0.05)
    return None


def _wait_for_exit_draining(pid: int, fd: int, timeout: float) -> int | None:
    """Wait for pid to exit while draining PTY output.

    Detach paths write to stdout while the PTY master is unread; without
    draining, the child blocks and never reaches process.exit().
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            try:
                os.read(fd, 4096)
            except OSError:
                break
        try:
            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid != 0:
                if os.WIFEXITED(status):
                    return os.WEXITSTATUS(status)
                if os.WIFSIGNALED(status):
                    return -os.WTERMSIG(status)
                return status
        except ChildProcessError:
            return 0
    return None


def _spawn_tui(
    socket_path: str,
    project_id: str,
    *,
    cols: int = 0,
    rows: int = 0,
):
    """Spawn the TUI in a real pseudo-terminal.

    Returns (pid, master_fd). Caller must kill and close.
    """
    env = {**os.environ, "NO_COLOR": "1", "TERM": "xterm-256color"}
    master_fd, slave_fd = pty.openpty()

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

    # Apply size on the master after the child is running. Pre-fork TIOCSWINSZ on
    # the slave leaves detach blocked on macOS when the PTY master is unread.
    if cols > 0 and rows > 0:
        _resize_pty(master_fd, cols, rows)
        time.sleep(0.1)

    return pid, master_fd


def _cleanup_tui(pid: int, fd: int) -> None:
    """Kill TUI process and close fd. Force-kill if still alive.

    Sends SIGTERM first so TUI cleanup can run while PTY is open,
    then closes the fd and reaps with a blocking waitpid.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    # Let TUI cleanup run while PTY is still open.
    time.sleep(1.0)
    # Close master fd — unblocks any TUI cleanup that writes to terminal.
    try:
        os.close(fd)
    except OSError:
        pass
    # Blocking reap. If child already exited during WNOHANG loop above,
    # this raises ChildProcessError (already reaped) — expected.
    try:
        _, status = os.waitpid(pid, 0)
        if os.WIFSIGNALED(status):
            pass  # killed by signal, expected during cleanup
    except ChildProcessError:
        pass


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    return re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)


def _final_frame_text(text: str) -> str:
    """Return text after the last full-screen clear (final render snapshot)."""
    parts = re.split(r"\x1b\[2J|\x1b\[3J", text)
    return _strip_ansi(parts[-1] if parts else text)


def _normalized_frame_text(text: str) -> str:
    """Collapse wrapped PTY lines for stable substring assertions."""
    return re.sub(r"\s+", " ", _final_frame_text(text)).strip()


def _count_occurrences(text: str, needle: str) -> int:
    """Count non-overlapping occurrences of needle in the final PTY frame."""
    return _normalized_frame_text(text).count(needle)


def _type_char(fd: int, char: str, delay: float = 0.2) -> None:
    """Type a single character into the PTY with a delay.

    Ink treats rapid input as a paste chunk. Each character must arrive
    individually with enough gap for Ink to process it separately.
    """
    os.write(fd, char.encode("utf-8"))
    time.sleep(delay)


def _drain_pty(fd: int, quiet_time: float = 0.3, max_time: float | None = None) -> None:
    """Drain pending PTY output until quiet for quiet_time seconds.

    When max_time is set, stop after that many seconds even if output continues.
    Live TUIs can render indefinitely; uncapped draining can stall detach helpers.
    """
    started = time.time()
    deadline = time.time() + quiet_time
    while time.time() < deadline:
        if max_time is not None and time.time() - started >= max_time:
            break
        remaining = deadline - time.time()
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.05))
        if ready:
            try:
                os.read(fd, 4096)
            except OSError:
                break
            deadline = time.time() + quiet_time  # reset quiet timer


def _type_char_and_wait(fd: int, char: str, timeout: float = 5.0) -> None:
    """Type a character and wait for Ink to fully process it.

    1. Drain all pending output (from previous renders).
    2. Write the character.
    3. Wait for new render output (confirming the character was processed).
    """
    # Step 1: Drain until quiet.
    _drain_pty(fd, quiet_time=0.2)
    # Step 2: Write character.
    os.write(fd, char.encode("utf-8"))
    # Step 3: Wait for new render output.
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.1))
        if ready:
            try:
                os.read(fd, 4096)
            except OSError:
                break
            # Got new output — character was processed.
            return
    # Timeout — continue anyway.


def _type_string(fd: int, text: str, delay: float = 0.2) -> None:
    """Type a string character by character into the PTY."""
    for ch in text:
        _type_char(fd, ch, delay)


def _type_string_and_wait(fd: int, text: str) -> None:
    """Type a string, waiting for each character to be processed.

    Uses _type_char_and_wait for each character to prevent Ink from
    treating rapid input as a paste chunk.
    """
    for ch in text:
        _type_char_and_wait(fd, ch)


def _type_enter(fd: int) -> None:
    """Press Enter (carriage return)."""
    os.write(fd, b"\r")
    time.sleep(0.2)


def _type_enter_and_wait(fd: int, timeout: float = 5.0) -> None:
    """Press Enter and wait for the submit to be processed."""
    _drain_pty(fd, quiet_time=0.2)
    os.write(fd, b"\r")
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.1))
        if ready:
            try:
                os.read(fd, 4096)
            except OSError:
                break
            return


def _type_ctrl_c(fd: int) -> None:
    """Press Ctrl+C."""
    # Drain pending frames so the child event loop can read the byte; an unread
    # PTY master otherwise blocks stdout and stalls detach.
    _drain_pty(fd, quiet_time=0.2, max_time=1.0)
    os.write(fd, b"\x03")
    time.sleep(0.1)


def _resize_pty(fd: int, cols: int, rows: int) -> None:
    """Resize the PTY with TIOCSWINSZ and send SIGWINCH."""
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    # SIGWINCH is sent to the foreground process group by the kernel
    # when TIOCSWINSZ is called on the master fd.


def _wait_for_connection(fd: int, timeout: float = 10.0) -> str:
    """Read from PTY until 'connected' appears or timeout.

    Returns all output read. The Composer is disabled until the connection
    state becomes 'connected', so interactive tests must call this before
    sending keystrokes.
    """
    chunks = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.2))
        if ready:
            try:
                data = os.read(fd, 4096)
                if not data:
                    break
                chunks.append(data)
            except OSError:
                break
        text = b"".join(chunks).decode("utf-8", errors="replace")
        if "connected" in text:
            # Let TUI re-render with connected state. Ink batches renders,
            # so the Composer may not be enabled immediately.
            time.sleep(2.0)
            # Drain any remaining buffered output.
            while True:
                ready2, _, _ = select.select([fd], [], [], 0.1)
                if not ready2:
                    break
                try:
                    extra = os.read(fd, 4096)
                    if not extra:
                        break
                    chunks.append(extra)
                except OSError:
                    break
            return b"".join(chunks).decode("utf-8", errors="replace")
    return b"".join(chunks).decode("utf-8", errors="replace")


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

    def setUp(self) -> None:
        """Reset request log and replay history before each test."""
        self.server.drain_requests()
        self.server.clear_request_event()
        self.server.reset_session()

    # ──────────────────────────────────────────────────────────────
    # P1-2: Real PTY assertions
    # ──────────────────────────────────────────────────────────────

    def test_tui_renders_in_pty_120_cols(self) -> None:
        """TUI renders Coordinator, project ID, and connected content at 120 cols."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=120, rows=40)
        try:
            output = _wait_for_connection(fd)
            self.assertGreater(len(output), 0, "TUI produced no output at 120 cols")
            self.assertIn("Coordinator", output, "Output missing 'Coordinator' at 120 cols")
            self.assertIn("proj-a", output, "Output missing project ID at 120 cols")
            self.assertIn("connected", output, "Output missing connected state at 120 cols")
            self.assertIn("Implement auth", output, "Output missing activity at 120 cols")
        finally:
            _cleanup_tui(pid, fd)

    def test_tui_renders_in_pty_80_cols(self) -> None:
        """TUI renders Coordinator, project ID, and connected content at 80 cols."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=40)
        try:
            output = _wait_for_connection(fd)
            self.assertGreater(len(output), 0, "TUI produced no output at 80 cols")
            self.assertIn("Coordinator", output, "Output missing 'Coordinator' at 80 cols")
            self.assertIn("proj-a", output, "Output missing project ID at 80 cols")
            self.assertIn("connected", output, "Output missing connected state at 80 cols")
            self.assertIn("Implement auth", output, "Output missing activity at 80 cols")
        finally:
            _cleanup_tui(pid, fd)

    def test_tui_renders_in_pty_50_cols(self) -> None:
        """TUI renders project ID and connected content at 50 cols.

        At 50 columns (< 60), Header renders only ``◆ {projectId}``
        without the word "Coordinator".
        """
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=50, rows=30)
        try:
            output = _wait_for_connection(fd)
            self.assertGreater(len(output), 0, "TUI produced no output at 50 cols")
            self.assertIn("proj-a", output, "Output missing project ID at 50 cols")
            self.assertIn("connected", output, "Output missing connected state at 50 cols")
            self.assertIn("Implement auth", output, "Output missing activity at 50 cols")
        finally:
            _cleanup_tui(pid, fd)

    def test_tui_sigterm_exits_cleanly(self) -> None:
        """SIGTERM exits within deadline with code 143 while PTY fd stays open."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a")
        try:
            _wait_for_connection(fd)
            os.kill(pid, signal.SIGTERM)
            exit_code = _wait_for_exit(pid, time.time() + 10)
            if exit_code is None:
                self.fail("TUI did not exit after SIGTERM (PTY fd kept open)")
            self.assertEqual(exit_code, 143, f"Expected SIGTERM exit code 143, got {exit_code}")
            self.assertNotEqual(exit_code, -9, "SIGKILL must not be used for detach")
            _assert_pty_terminal_restored(fd)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
            msg = json.dumps({
                "type": "request",
                "protocol_version": 1,
                "request_id": "ping-after-sigterm",
                "project_id": "proj-a",
                "method": "system.ping",
                "params": {},
            }) + "\n"
            sock.sendall(msg.encode())
            sock.settimeout(2)
            data = sock.recv(4096).decode()
            resp = json.loads([l for l in data.split("\n") if l.strip()][0])
            self.assertTrue(resp["ok"], "Supervisor ping failed after SIGTERM detach")
        finally:
            sock.close()

    def test_tui_no_args_exits_with_usage(self) -> None:
        """TUI exits with usage message when invoked with no args."""
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
            exit_code = _wait_for_exit(pid, time.time() + 5)
            self.assertIsNotNone(exit_code, "No-args process did not exit")
            self.assertEqual(exit_code, 1)
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

    def test_bundle_has_no_test_hooks(self) -> None:
        """Bundle must not contain production test hook env vars."""
        bundle_path = UI_TUI / "dist" / "entry.js"
        self.assertTrue(bundle_path.exists(), f"Bundle not found: {bundle_path}")
        content = bundle_path.read_text()
        self.assertNotIn("COORDINATOR_TUI_TEST_SUBMIT", content,
                         "Test hook COORDINATOR_TUI_TEST_SUBMIT found in production bundle")
        self.assertNotIn("COORDINATOR_TUI_TEST_UNCAUGHT", content,
                         "Test hook COORDINATOR_TUI_TEST_UNCAUGHT found in production bundle")

    # ──────────────────────────────────────────────────────────────
    # P1-3: Real composer and detach behavior (PTY keystrokes)
    # ──────────────────────────────────────────────────────────────

    def test_pty_types_hello_and_sends_chat(self) -> None:
        """Type 'hello' + Enter via PTY; assert one chat.send RPC and Commander output."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            _wait_for_connection(fd)
            # Drain any buffered output after connection.
            _read_available(fd, timeout=0.5)
            # Type hello character by character, waiting for each to render.
            _type_string_and_wait(fd, "hello")
            _type_enter_and_wait(fd)
            # Wait for chat.send to arrive at the server.
            self.assertTrue(
                self.server.wait_for_request_method("chat.send", timeout=10),
                "chat.send RPC not received after typing hello+Enter",
            )
            requests = self.server.drain_requests()
            chat_sends = [p for m, p in requests if m == "chat.send"]
            self.assertEqual(len(chat_sends), 1, f"Expected one chat.send, got {requests}")
            self.assertEqual(chat_sends[0].get("text"), "hello")
            # The fake supervisor returns Commander-style response with
            # chat.message event. Read PTY output to verify Commander output.
            output = _read_available(fd, timeout=3.0)
            self.assertIn(
                "report back when they finish",
                _normalized_frame_text(output),
                "Commander response not visible in PTY output",
            )
        finally:
            _cleanup_tui(pid, fd)

    def test_pty_shutdown_once_sends_no_rpc(self) -> None:
        """Type /shutdown + Enter once; assert no system.shutdown request."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            _wait_for_connection(fd)
            _read_available(fd, timeout=0.5)
            _type_string_and_wait(fd, "/shutdown")
            _type_enter_and_wait(fd)
            # Wait a bit to confirm no RPC is sent (first invocation is pending).
            time.sleep(3.0)
            requests = self.server.drain_requests()
            shutdowns = [m for m, _ in requests if m == "system.shutdown"]
            self.assertEqual(shutdowns, [], f"Unexpected shutdown RPCs: {requests}")
        finally:
            _cleanup_tui(pid, fd)

    def test_pty_shutdown_twice_sends_one_rpc(self) -> None:
        """Type /shutdown twice; assert exactly one system.shutdown request."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            _wait_for_connection(fd)
            _read_available(fd, timeout=0.5)
            # First /shutdown — enters pending confirmation state.
            _type_string_and_wait(fd, "/shutdown")
            _type_enter_and_wait(fd)
            # Second /shutdown — confirms and sends RPC.
            _type_string_and_wait(fd, "/shutdown")
            _type_enter_and_wait(fd)
            self.assertTrue(
                self.server.wait_for_request_method("system.shutdown", timeout=10),
                "system.shutdown RPC not received after confirmation",
            )
            requests = self.server.drain_requests()
            shutdowns = [m for m, _ in requests if m == "system.shutdown"]
            self.assertEqual(len(shutdowns), 1, f"Expected one shutdown, got {requests}")
        finally:
            _cleanup_tui(pid, fd)

    def test_pty_shutdown_status_shutdown_sends_no_rpc(self) -> None:
        """Type /shutdown, /status, /shutdown; assert no shutdown, one status."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            _wait_for_connection(fd)
            _read_available(fd, timeout=0.5)
            # First /shutdown — pending.
            _type_string_and_wait(fd, "/shutdown")
            _type_enter_and_wait(fd)
            # /status — clears pending destructive.
            _type_string_and_wait(fd, "/status")
            _type_enter_and_wait(fd)
            # Second /shutdown — new pending (not confirmed).
            _type_string_and_wait(fd, "/shutdown")
            _type_enter_and_wait(fd)
            time.sleep(3.0)
            requests = self.server.drain_requests()
            shutdowns = [m for m, _ in requests if m == "system.shutdown"]
            self.assertEqual(shutdowns, [], f"Unexpected shutdown RPCs: {requests}")
            statuses = [m for m, _ in requests if m == "project.status"]
            self.assertEqual(len(statuses), 1, f"Expected one status RPC: {requests}")
        finally:
            _cleanup_tui(pid, fd)

    def test_sigint_exits_and_supervisor_still_responds(self) -> None:
        """SIGINT (Ctrl+C) exits TUI; Supervisor still responds to ping.

        Ink's cleanup reads from stdin (PTY slave), which blocks while the
        master fd is open. We send SIGINT, wait for the graceful handler to
        run, then close the master fd to unblock Ink's stdin read so the
        process can complete its exit.
        """
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            _wait_for_connection(fd)
            time.sleep(0.5)
            # Send SIGINT — simulates Ctrl+C detach.
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            # Give the graceful handler time to start cleanup.
            time.sleep(2.0)
            # Close master fd to unblock Ink's stdin read during cleanup.
            try:
                os.close(fd)
            except OSError:
                pass
            # Now the process should exit promptly.
            exit_code = _wait_for_exit(pid, time.time() + 5)
            if exit_code is None:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except (ProcessLookupError, ChildProcessError):
                    pass
                self.fail("TUI did not exit after closing PTY fd")
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        finally:
            pass
        # Supervisor should still be responsive.
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
            msg = json.dumps({
                "type": "request",
                "protocol_version": 1,
                "request_id": "ping-after-detach",
                "project_id": "proj-a",
                "method": "system.ping",
                "params": {},
            }) + "\n"
            sock.sendall(msg.encode())
            sock.settimeout(2)
            data = sock.recv(4096).decode()
            lines = [l for l in data.split("\n") if l.strip()]
            resp = json.loads(lines[0])
            self.assertTrue(resp["ok"], "Supervisor ping failed after SIGINT detach")
        finally:
            sock.close()

    def test_tui_connects_and_subscribes(self) -> None:
        """TUI connects to Supervisor and subscribes to events.

        Verifies the connection handshake: events.subscribe is sent
        after the TUI connects and establishes the event stream.
        """
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            _wait_for_connection(fd)
            time.sleep(0.5)
            requests = self.server.drain_requests()
            methods = {r[0] for r in requests}
            self.assertIn("events.subscribe", methods,
                          f"TUI did not subscribe: {requests}")
        finally:
            _cleanup_tui(pid, fd)

    def test_tui_renders_activity_content(self) -> None:
        """TUI renders activity content from Supervisor events.

        The fake Supervisor sends task.created, task.done, and
        chat.message events. Verify these appear in the rendered output.
        """
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=120, rows=40)
        try:
            output = _wait_for_connection(fd)
            # The fake Supervisor sends "Task completed successfully!" and
            # task activity. Check for task-related content.
            self.assertIn("Implement auth", output,
                          "Activity content not rendered")
        finally:
            _cleanup_tui(pid, fd)

    # ──────────────────────────────────────────────────────────────
    # P1-4: Gate E scenarios
    # ──────────────────────────────────────────────────────────────

    def test_resize_120_to_50_renders_narrow(self) -> None:
        """Resize PTY from 120→50 columns; assert valid narrow render."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=120, rows=40)
        try:
            output_wide = _wait_for_connection(fd)
            self.assertIn("Coordinator", output_wide)
            self.assertIn("connected", output_wide)
            _resize_pty(fd, 50, 30)
            time.sleep(1.5)
            output_narrow = _read_available(fd, timeout=3.0)
            all_output = output_wide + output_narrow
            self.assertIn("proj-a", all_output, "proj-a missing after resize to 50 cols")
            self.assertIn("connected", all_output, "connected missing after resize to 50 cols")
            self.assertIn("Implement auth", all_output, "activity missing after resize to 50 cols")
            self.assertNotIn("Coordinator", output_narrow,
                             "Wide-only label should not appear in narrow re-render")
        finally:
            _cleanup_tui(pid, fd)

    def test_reconnect_replays_missed_events(self) -> None:
        """Drop connection, reconnect, replay missed cursors — each event once."""
        marker = "Task completed successfully!"
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            output_before = _wait_for_connection(fd)
            self.assertGreaterEqual(_count_occurrences(output_before, marker), 1,
                                    "Initial render should show completion message")
            self.server.drain_requests()
            self.server.disconnect_clients()
            time.sleep(4.0)
            output_after = _read_available(fd, timeout=3.0)
            full_output = output_before + output_after
            final_frame = _final_frame_text(full_output)
            self.assertEqual(final_frame.count(marker), 1,
                             "Reconnect replay must not duplicate transcript content")
            requests = self.server.drain_requests()
            methods = {r[0] for r in requests}
            self.assertIn("events.subscribe", methods,
                            f"Expected reconnect subscribe, got: {requests}")
        finally:
            _cleanup_tui(pid, fd)

    def test_reconnect_replays_chat_message_history(self) -> None:
        """Chat messages sent before disconnect replay once on reconnect."""
        chat_marker = "report back when they finish"
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            output_before = _wait_for_connection(fd)
            _read_available(fd, timeout=0.5)
            _type_string_and_wait(fd, "replay-me")
            _type_enter_and_wait(fd)
            self.assertTrue(
                self.server.wait_for_request_method("chat.send", timeout=10),
                "chat.send RPC not received before disconnect",
            )
            output_before += _read_available(fd, timeout=3.0)
            self.assertIn(
                chat_marker,
                _normalized_frame_text(output_before),
                "Commander chat message not rendered before disconnect",
            )
            self.server.drain_requests()
            self.server.disconnect_clients()
            time.sleep(4.0)
            output_after = _read_available(fd, timeout=3.0)
            full_output = output_before + output_after
            final_frame = _final_frame_text(full_output)
            self.assertEqual(
                _normalized_frame_text(full_output).count(chat_marker),
                1,
                "Reconnect replay must include chat.message history once",
            )
            self.assertNotIn("Received:", final_frame,
                             "Reconnect replay must not show legacy echo format")
            requests = self.server.drain_requests()
            methods = {r[0] for r in requests}
            self.assertIn("events.subscribe", methods,
                          f"Expected reconnect subscribe, got: {requests}")
        finally:
            _cleanup_tui(pid, fd)

    def test_pty_help_shows_commands_without_rpc(self) -> None:
        """Type /help; assert local help text, no unsupported method error."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=100, rows=70)
        try:
            _wait_for_connection(fd)
            _read_available(fd, timeout=0.5)
            _type_string_and_wait(fd, "/help")
            _type_enter_and_wait(fd)
            time.sleep(1.0)
            output = _read_available(fd, timeout=3.0)
            frame = _final_frame_text(output)
            self.assertIn("/task <id>", frame)
            self.assertIn("/goal confirm", frame)
            self.assertNotIn("unsupported method", frame.lower())
            requests = self.server.drain_requests()
            methods = {m for m, _ in requests}
            self.assertNotIn("system.help", methods)
        finally:
            _cleanup_tui(pid, fd)

    def test_pty_chat_shows_user_message_once(self) -> None:
        """User chat text appears exactly once with Commander response."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=100, rows=30)
        try:
            _wait_for_connection(fd)
            _read_available(fd, timeout=0.5)
            _type_string_and_wait(fd, "hello once")
            _type_enter_and_wait(fd)
            self.assertTrue(
                self.server.wait_for_request_method("chat.send", timeout=10),
                "chat.send RPC not received",
            )
            output = _read_available(fd, timeout=3.0)
            frame = _final_frame_text(output)
            self.assertEqual(frame.count("> hello once"), 1)
            self.assertEqual(
                _normalized_frame_text(output).count("report back when they finish"),
                1,
            )
        finally:
            _cleanup_tui(pid, fd)

    def test_pty_unknown_slash_stays_local(self) -> None:
        """Unknown slash commands stay local and never call chat.send."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=100, rows=30)
        try:
            _wait_for_connection(fd)
            _read_available(fd, timeout=0.5)
            _type_string_and_wait(fd, "/taskz")
            _type_enter_and_wait(fd)
            time.sleep(1.0)
            output = _read_available(fd, timeout=3.0)
            frame = _final_frame_text(output)
            self.assertIn("Unknown command: /taskz", frame)
            self.assertIn("/help", frame)
            requests = self.server.drain_requests()
            methods = {m for m, _ in requests}
            self.assertNotIn("chat.send", methods)
        finally:
            _cleanup_tui(pid, fd)

    def test_pty_plan_slash_calls_project_plan_rpc(self) -> None:
        """Phase 6D: /plan must route through project.plan Supervisor RPC."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=100, rows=30)
        try:
            _wait_for_connection(fd)
            _read_available(fd, timeout=0.5)
            _type_string_and_wait(fd, "/plan")
            _type_enter_and_wait(fd)
            self.assertTrue(
                self.server.wait_for_request_method("project.plan", timeout=10),
                "project.plan RPC not received for /plan",
            )
            output = _read_available(fd, timeout=3.0)
            frame = _final_frame_text(output)
            self.assertNotIn("unsupported method", frame.lower())
            self.assertNotIn("Unknown command: /plan", frame)
        finally:
            _cleanup_tui(pid, fd)

    def _assert_footer_visible_once(self, frame: str) -> None:
        self.assertIn("connected", frame)
        self.assertEqual(frame.count("Tab"), 1)

    def _assert_pty_layout_frame(self, frame: str) -> None:
        """Assert footer, composer, and activity content in a PTY layout snapshot."""
        normalized = re.sub(r"\s+", " ", _strip_ansi(frame))
        self.assertIn("auth", normalized)
        self.assertIn("Implement", normalized)
        self._assert_footer_visible_once(frame)
        self.assertIn("❯", frame, "Composer prompt missing from layout frame")
        # Header/footer/composer chrome consumes rows; transcript must still fit.
        self.assertLessEqual(len(frame.splitlines()), 30)

    def test_pty_transcript_layout_at_40_cols(self) -> None:
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=40, rows=24)
        try:
            output = _wait_for_connection(fd)
            self._assert_pty_layout_frame(_final_frame_text(output))
        finally:
            _cleanup_tui(pid, fd)

    def test_pty_transcript_layout_at_80_cols(self) -> None:
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            output = _wait_for_connection(fd)
            self._assert_pty_layout_frame(_final_frame_text(output))
        finally:
            _cleanup_tui(pid, fd)

    def test_pty_transcript_layout_at_120_cols(self) -> None:
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=120, rows=24)
        try:
            output = _wait_for_connection(fd)
            self._assert_pty_layout_frame(_final_frame_text(output))
        finally:
            _cleanup_tui(pid, fd)

    def test_pty_task_shows_baseline_detail(self) -> None:
        """Type /task; assert goal, verify commands, and failure note render."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=120, rows=40)
        try:
            _wait_for_connection(fd)
            _read_available(fd, timeout=0.5)
            _type_string_and_wait(fd, "/task task-baseline-001")
            _type_enter_and_wait(fd)
            self.assertTrue(
                self.server.wait_for_request_method("project.task", timeout=10),
                "project.task RPC not received",
            )
            output = _read_available(fd, timeout=3.0)
            frame = _final_frame_text(output)
            normalized = re.sub(r"\s+", " ", _strip_ansi(frame))
            self.assertIn("Run baseline acceptance checks", normalized)
            self.assertIn("uv run pytest -q", normalized)
            self.assertIn("no changed files", normalized)
            self.assertIn("agent.log", normalized)
        finally:
            _cleanup_tui(pid, fd)

    def test_work_counter_advances_during_tui_termination(self) -> None:
        """Work simulation continues advancing after TUI is terminated."""
        self.server.start_work()
        try:
            self.assertTrue(self.server.wait_work_counter(3, timeout=5),
                            "Work counter did not reach 3")
            pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
            _wait_for_connection(fd)
            counter_before = self.server.get_work_counter()
            # Kill TUI.
            os.kill(pid, signal.SIGTERM)
            time.sleep(1.0)
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            # Work should still be advancing independently.
            self.assertTrue(self.server.wait_work_counter(counter_before + 2, timeout=5),
                            f"Work counter did not advance past {counter_before}")
            counter_after = self.server.get_work_counter()
            self.assertGreater(counter_after, counter_before,
                               "Work counter should continue after TUI termination")
        finally:
            self.server.stop_work()

    def test_terminal_cleanup_after_ctrl_c(self) -> None:
        """Ctrl+C exits cleanly with PTY fd open; terminal flags restored."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a")
        try:
            _wait_for_connection(fd)
            _type_ctrl_c(fd)
            exit_code = _wait_for_exit(pid, time.time() + 10)
            if exit_code is None:
                self.fail("TUI did not exit after Ctrl+C (PTY fd kept open)")
            self.assertEqual(exit_code, 0, f"Expected clean exit 0 after Ctrl+C, got {exit_code}")
            self.assertNotEqual(exit_code, -9, "SIGKILL must not be used for detach")
            _assert_pty_terminal_restored(fd)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
            msg = json.dumps({
                "type": "request",
                "protocol_version": 1,
                "request_id": "ping-after-ctrl-c",
                "project_id": "proj-a",
                "method": "system.ping",
                "params": {},
            }) + "\n"
            sock.sendall(msg.encode())
            sock.settimeout(2)
            data = sock.recv(4096).decode()
            resp = json.loads([l for l in data.split("\n") if l.strip()][0])
            self.assertTrue(resp["ok"], "Supervisor ping failed after Ctrl+C detach")
        finally:
            sock.close()

    def test_ctrl_c_when_disconnected_exits_promptly(self) -> None:
        """Ctrl+C exits cleanly when Supervisor socket is unavailable."""
        bogus_path = os.path.join(self.tmp_dir, "nonexistent.sock")
        pid, fd = _spawn_tui(bogus_path, "proj-a")
        try:
            _read_available(fd, timeout=5.0)
            _type_ctrl_c(fd)
            exit_code = _wait_for_exit(pid, time.time() + 10)
            if exit_code is None:
                self.fail("TUI did not exit after Ctrl+C while disconnected (PTY fd kept open)")
            self.assertEqual(exit_code, 0, f"Expected clean exit 0, got {exit_code}")
            self.assertNotEqual(exit_code, -9, "SIGKILL must not be used for detach")
            _assert_pty_terminal_restored(fd)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass

    def test_pty_quit_exits_cleanly(self) -> None:
        """Type /quit + Enter; clean exit with PTY fd open and terminal restored."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a")
        try:
            _wait_for_connection(fd)
            _read_available(fd, timeout=0.5)
            _type_string_and_wait(fd, "/quit")
            _type_enter_and_wait(fd)
            exit_code = _wait_for_exit(pid, time.time() + 10)
            if exit_code is None:
                self.fail("TUI did not exit after /quit (PTY fd kept open)")
            self.assertEqual(exit_code, 0, f"Expected clean exit 0 after /quit, got {exit_code}")
            self.assertNotEqual(exit_code, -9, "SIGKILL must not be used for detach")
            _assert_pty_terminal_restored(fd)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
            msg = json.dumps({
                "type": "request",
                "protocol_version": 1,
                "request_id": "ping-after-quit",
                "project_id": "proj-a",
                "method": "system.ping",
                "params": {},
            }) + "\n"
            sock.sendall(msg.encode())
            sock.settimeout(2)
            data = sock.recv(4096).decode()
            resp = json.loads([l for l in data.split("\n") if l.strip()][0])
            self.assertTrue(resp["ok"], "Supervisor ping failed after /quit detach")
        finally:
            sock.close()

    def test_manifest_build_hash_matches_bundle(self) -> None:
        """manifest build_hash matches the bundled entry.js content."""
        manifest_path = UI_TUI / "dist" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        bundle_bytes = BUNDLE.read_bytes()
        expected = hashlib.sha256(bundle_bytes).hexdigest()[:16]
        self.assertEqual(manifest["build_hash"], expected)

    def test_terminal_cleanup_after_sigterm(self) -> None:
        """SIGTERM exits cleanly with PTY fd open; terminal flags restored."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a")
        try:
            _wait_for_connection(fd)
            os.kill(pid, signal.SIGTERM)
            exit_code = _wait_for_exit(pid, time.time() + 10)
            if exit_code is None:
                self.fail("TUI did not exit after SIGTERM (PTY fd kept open)")
            self.assertEqual(exit_code, 143, f"Expected SIGTERM exit code 143, got {exit_code}")
            self.assertNotEqual(exit_code, -9, "SIGKILL must not be used for detach")
            _assert_pty_terminal_restored(fd)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
            msg = json.dumps({
                "type": "request",
                "protocol_version": 1,
                "request_id": "ping-after-sigterm-cleanup",
                "project_id": "proj-a",
                "method": "system.ping",
                "params": {},
            }) + "\n"
            sock.sendall(msg.encode())
            sock.settimeout(2)
            data = sock.recv(4096).decode()
            resp = json.loads([l for l in data.split("\n") if l.strip()][0])
            self.assertTrue(resp["ok"], "Supervisor ping failed after SIGTERM detach")
        finally:
            sock.close()

    def test_terminal_cleanup_after_sighup(self) -> None:
        """SIGHUP exits cleanly with PTY fd open; terminal flags restored."""
        pid, fd = _spawn_tui(self.socket_path, "proj-a")
        try:
            _wait_for_connection(fd)
            os.kill(pid, signal.SIGHUP)
            exit_code = _wait_for_exit(pid, time.time() + 10)
            if exit_code is None:
                self.fail("TUI did not exit after SIGHUP (PTY fd kept open)")
            self.assertEqual(exit_code, 129, f"Expected SIGHUP exit code 129, got {exit_code}")
            self.assertNotEqual(exit_code, -9, "SIGKILL must not be used for detach")
            _assert_pty_terminal_restored(fd)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
            msg = json.dumps({
                "type": "request",
                "protocol_version": 1,
                "request_id": "ping-after-sighup",
                "project_id": "proj-a",
                "method": "system.ping",
                "params": {},
            }) + "\n"
            sock.sendall(msg.encode())
            sock.settimeout(2)
            data = sock.recv(4096).decode()
            resp = json.loads([l for l in data.split("\n") if l.strip()][0])
            self.assertTrue(resp["ok"], "Supervisor ping failed after SIGHUP detach")
        finally:
            sock.close()

    def test_foreign_event_does_not_enter_transcript(self) -> None:
        """Foreign-project event cannot enter transcript or activity state.

        P2: Defense-in-depth — even if the Supervisor sends a foreign event,
        the TUI should reject it via reduceEvent's projectId guard.
        """
        pid, fd = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            _wait_for_connection(fd)
            # Send a foreign event directly via the fake supervisor.
            self.server.send_foreign_event(
                "chat.message",
                {"role": "coordinator", "text": "FOREIGN_INTRUSION"},
                "proj-other",
            )
            time.sleep(0.5)
            output = _read_available(fd, timeout=1.0)
            # The foreign text should NOT appear in the TUI output.
            self.assertNotIn("FOREIGN_INTRUSION", output,
                             "Foreign-project event leaked into TUI output")
        finally:
            _cleanup_tui(pid, fd)

    def test_reattach_skips_onboarding_for_registered_project(self) -> None:
        """Second TUI launch for same project skips onboarding."""
        # First launch — connected (FakeSupervisor has project pre-registered).
        pid1, fd1 = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            output1 = _wait_for_connection(fd1)
            self.assertIn("connected", output1, "First launch did not connect")
            self.assertNotIn("Register this project?", _final_frame_text(output1),
                             "First launch should not show onboarding for pre-registered project")
            # Detach.
            _type_string_and_wait(fd1, "/quit")
            _type_enter_and_wait(fd1)
            _wait_for_exit_draining(pid1, fd1, timeout=10.0)
        finally:
            try:
                os.close(fd1)
            except OSError:
                pass
            try:
                os.waitpid(pid1, 0)
            except ChildProcessError:
                pass
        # Second launch — same project, should skip onboarding.
        pid2, fd2 = _spawn_tui(self.socket_path, "proj-a", cols=80, rows=24)
        try:
            output2 = _wait_for_connection(fd2)
            self.assertIn("connected", output2, "Re-attach did not connect")
            self.assertIn("proj-a", _strip_ansi(output2), "Re-attach missing project ID")
            self.assertNotIn("Register this project?", _final_frame_text(output2),
                             "Re-attach should skip onboarding for registered project")
        finally:
            _cleanup_tui(pid2, fd2)


if __name__ == "__main__":
    unittest.main()
