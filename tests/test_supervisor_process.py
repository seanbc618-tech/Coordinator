"""Tests for detached Supervisor process lifecycle."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from local_cli_coordinator.locks import acquire_lock_at, release_lock_at
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_identity import (
    REQUIRED_CLIENT_CAPABILITIES,
    RUNTIME_COMPATIBILITY,
    SupervisorIdentity,
    is_compatible_identity,
)
from local_cli_coordinator.supervisor_process import (
    EnsureSupervisorResult,
    SupervisorIncompatibleError,
    SupervisorReadinessError,
    ensure_supervisor,
    ping_supervisor,
    ping_supervisor_identity,
    startup_lock_path,
    supervisor_log_path,
    supervisor_spawn_argv,
)
from local_cli_coordinator.supervisor_server import send_request
from local_cli_coordinator.supervisor_protocol import PROTOCOL_VERSION, RequestEnvelope
from tests.helpers import ROOT, SRC


def _ping_request(request_id: str = "ping-1") -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id,
        project_id=None,
        method="system.ping",
        params={},
    )


def _run_cli_with_home(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["COORDINATOR_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "local_cli_coordinator", *args],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _live_supervisor_pid(home: Path) -> int | None:
    lock_path = home / "state" / "supervisor.lock"
    if not lock_path.exists():
        return None
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(data["pid"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:
        return None


class SupervisorProcessTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)
        self.paths = RuntimePaths(
            config_dir=self.home / "config",
            data_dir=self.home / "data",
            state_dir=self.home / "state",
        )
        self.paths.create()
        self._write_config()
        self._processes: list[subprocess.Popen[str]] = []
        self._old_home = os.environ.get("COORDINATOR_HOME")
        self._old_pythonpath = os.environ.get("PYTHONPATH")
        os.environ["COORDINATOR_HOME"] = str(self.home)
        os.environ["PYTHONPATH"] = str(SRC)

    def _write_config(self) -> None:
        config_dir = self.home / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        repo = self.home / "repo"
        repo.mkdir(exist_ok=True)
        config_dir.joinpath("agents.toml").write_text(
            '[agents.test]\n'
            f'command = "{sys.executable} -c \\"pass\\""\n'
            'capabilities = ["code"]\n'
            'max_concurrency = 1\n'
            'role = "worker"\n'
        )
        config_dir.joinpath("repos.toml").write_text(
            '[repos.demo]\n'
            f'path = "{repo}"\n'
            'default_branch = "main"\n'
        )
        config_dir.joinpath("policy.toml").write_text(
            '[task_policy]\n'
            'require_single_repo = true\n'
            'require_acceptance_criteria = false\n'
            'require_verification_commands = false\n'
            'require_handoff_summary = false\n'
            'max_files_touched = 10\n'
            'max_expected_minutes = 30\n'
            'max_attempts = 3\n'
            'split_if_touches_multiple_subsystems = false\n'
            'split_if_research_and_code_are_mixed = false\n'
            'max_tasks_per_run = 1\n'
            'max_tasks_per_day = 100\n'
            'max_consecutive_failures = 3\n'
        )

    def _close_process_streams(self, process: subprocess.Popen[str]) -> None:
        for stream in (process.stdout, process.stderr, process.stdin):
            if stream is None:
                continue
            try:
                if stream.readable():
                    stream.read()
            except (OSError, ValueError):
                pass
            try:
                stream.close()
            except OSError:
                pass

    def tearDown(self) -> None:
        stop = _run_cli_with_home(self.home, "supervisor", "stop")
        if stop.returncode != 0:
            for process in self._processes:
                if process.poll() is None:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        process.terminate()
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except (ProcessLookupError, PermissionError, OSError):
                            process.kill()
                        process.wait(timeout=2.0)
        for process in self._processes:
            self._close_process_streams(process)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
        if self._old_home is None:
            os.environ.pop("COORDINATOR_HOME", None)
        else:
            os.environ["COORDINATOR_HOME"] = self._old_home
        if self._old_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = self._old_pythonpath
        self._tmpdir.cleanup()

    def _start_foreground_supervisor(self) -> subprocess.Popen[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        env["COORDINATOR_HOME"] = str(self.home)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "local_cli_coordinator",
                "supervisor",
                "start",
                "--foreground",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._processes.append(process)
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if ping_supervisor(self.paths):
                return process
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr else ""
                self.fail(f"supervisor exited before becoming ready: stderr={stderr}")
            time.sleep(0.05)
        self.fail("supervisor did not become ready in time")


class EnsureSupervisorTests(SupervisorProcessTestBase):
    def test_attach_to_existing_supervisor(self) -> None:
        existing = self._start_foreground_supervisor()
        existing_pid = _live_supervisor_pid(self.home)
        self.assertIsNotNone(existing_pid)

        result = ensure_supervisor(self.paths)

        self.assertIsInstance(result, EnsureSupervisorResult)
        self.assertTrue(result.attached)
        self.assertFalse(result.started)
        self.assertEqual(_live_supervisor_pid(self.home), existing_pid)
        self.assertEqual(existing.poll(), None)

    def test_detached_start_when_absent(self) -> None:
        result = ensure_supervisor(self.paths)

        self.assertFalse(result.attached)
        self.assertTrue(result.started)
        self.assertIsNotNone(result.pid)
        self.assertTrue(ping_supervisor(self.paths))
        supervisor_pid = _live_supervisor_pid(self.home)
        self.assertIsNotNone(supervisor_pid)
        self.assertNotEqual(supervisor_pid, os.getpid())

    def test_simultaneous_start_race_produces_one_process(self) -> None:
        barrier = threading.Barrier(2)
        results: list[EnsureSupervisorResult] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                barrier.wait()
                results.append(ensure_supervisor(self.paths, readiness_timeout=20.0))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        started = [result for result in results if result.started]
        attached = [result for result in results if result.attached]
        self.assertEqual(len(started), 1)
        self.assertEqual(len(attached), 1)
        supervisor_pid = _live_supervisor_pid(self.home)
        self.assertIsNotNone(supervisor_pid)
        self.assertTrue(ping_supervisor(self.paths))

    def test_readiness_timeout_cleanup(self) -> None:
        hang = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(3600)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._processes.append(hang)

        with mock.patch(
            "local_cli_coordinator.supervisor_process._spawn_detached_supervisor",
            return_value=hang.pid,
        ):
            with self.assertRaises(SupervisorReadinessError):
                ensure_supervisor(self.paths, readiness_timeout=0.5, poll_interval=0.05)

        self.assertIsNone(_live_supervisor_pid(self.home))
        self.assertFalse(self.paths.socket.exists())
        self.assertFalse(startup_lock_path(self.paths).exists())
        deadline = time.time() + 2.0
        while time.time() < deadline and hang.poll() is None:
            time.sleep(0.05)
        self.assertIsNotNone(hang.poll())

    def test_stale_startup_lock_is_cleared(self) -> None:
        lock_path = startup_lock_path(self.paths)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps({"pid": 999999999, "acquired_at": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        result = ensure_supervisor(self.paths)

        self.assertTrue(result.started or result.attached)
        self.assertTrue(ping_supervisor(self.paths))
        self.assertFalse(startup_lock_path(self.paths).exists())

    def test_log_file_location(self) -> None:
        log_path = supervisor_log_path(self.paths)
        self.assertEqual(log_path, self.paths.state_dir / "supervisor.log")

        ensure_supervisor(self.paths)

        self.assertTrue(log_path.exists())
        self.assertGreater(log_path.stat().st_size, 0)

    def test_graceful_shutdown(self) -> None:
        ensure_supervisor(self.paths)
        self.assertTrue(ping_supervisor(self.paths))

        stop = _run_cli_with_home(self.home, "supervisor", "stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIn("Supervisor shutting down", stop.stdout)

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if not ping_supervisor(self.paths):
                break
            time.sleep(0.05)
        self.assertFalse(ping_supervisor(self.paths))
        self.assertFalse(self.paths.socket.exists())
        self.assertFalse(self.paths.lock.exists())

    def test_tui_detach_leaves_process_alive(self) -> None:
        script = (
            "import os\n"
            "from pathlib import Path\n"
            "from local_cli_coordinator.runtime_paths import RuntimePaths\n"
            "from local_cli_coordinator.supervisor_process import ensure_supervisor\n"
            "home = Path(os.environ['COORDINATOR_HOME'])\n"
            "paths = RuntimePaths(home / 'config', home / 'data', home / 'state')\n"
            "ensure_supervisor(paths)\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        env["COORDINATOR_HOME"] = str(self.home)
        launcher = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(launcher.returncode, 0, launcher.stderr)

        supervisor_pid = _live_supervisor_pid(self.home)
        self.assertIsNotNone(supervisor_pid)
        self.assertTrue(ping_supervisor(self.paths))
        response = send_request(self.paths.socket, _ping_request("detach-check"))
        self.assertTrue(response.ok)
        self.assertEqual(response.result.get("pong"), True)

    def test_detached_launcher_exit_has_no_resource_warning(self) -> None:
        script = (
            "import os\n"
            "from pathlib import Path\n"
            "from local_cli_coordinator.runtime_paths import RuntimePaths\n"
            "from local_cli_coordinator.supervisor_process import ensure_supervisor\n"
            "home = Path(os.environ['COORDINATOR_HOME'])\n"
            "paths = RuntimePaths(home / 'config', home / 'data', home / 'state')\n"
            "ensure_supervisor(paths)\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        env["COORDINATOR_HOME"] = str(self.home)
        launcher = subprocess.run(
            [sys.executable, "-W", "error::ResourceWarning", "-c", script],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(launcher.returncode, 0, launcher.stderr)
        self.assertEqual(launcher.stderr, "")
        self.assertTrue(ping_supervisor(self.paths))
        self.assertIsNotNone(_live_supervisor_pid(self.home))

    def test_spawn_uses_argv_not_shell(self) -> None:
        self.assertEqual(
            supervisor_spawn_argv(),
            [
                sys.executable,
                "-m",
                "local_cli_coordinator",
                "supervisor",
                "start",
                "--foreground",
            ],
        )
        with mock.patch(
            "local_cli_coordinator.supervisor_process.os.fork",
            return_value=4242,
        ):
            with mock.patch(
                "local_cli_coordinator.supervisor_process._wait_until_ready",
                return_value=None,
            ):
                ensure_supervisor(self.paths, readiness_timeout=1.0)


class SupervisorRuntimeIdentityTests(SupervisorProcessTestBase):
    def test_system_ping_exposes_runtime_identity(self) -> None:
        self._start_foreground_supervisor()
        response = send_request(self.paths.socket, _ping_request("identity-check"))
        self.assertTrue(response.ok, response.error)
        self.assertIsNotNone(response.result)

        result = response.result or {}
        self.assertTrue(result.get("pong"))
        self.assertIn("pid", result)
        self.assertEqual(result.get("runtime_compatibility"), RUNTIME_COMPATIBILITY)
        self.assertEqual(
            set(result.get("capabilities", [])),
            set(REQUIRED_CLIENT_CAPABILITIES),
        )
        self.assertIn("started_at", result)
        self.assertIn("active_workers", result)
        self.assertIsInstance(result.get("active_workers"), int)

        identity = SupervisorIdentity.from_ping_result(result)
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertTrue(is_compatible_identity(identity))

    def test_ping_supervisor_identity_returns_structured_result(self) -> None:
        self._start_foreground_supervisor()
        identity = ping_supervisor_identity(self.paths)
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.runtime_compatibility, RUNTIME_COMPATIBILITY)
        self.assertTrue(is_compatible_identity(identity))
        self.assertTrue(ping_supervisor(self.paths))

    def test_incompatible_supervisor_is_rejected(self) -> None:
        with mock.patch(
            "local_cli_coordinator.supervisor_process._supervisor_ping_result",
            return_value={"pong": True},
        ):
            self.assertIsNone(ping_supervisor_identity(self.paths))
            self.assertFalse(ping_supervisor(self.paths))
            with self.assertRaises(SupervisorIncompatibleError):
                ensure_supervisor(self.paths)


class SupervisorStartCliTests(SupervisorProcessTestBase):
    def test_supervisor_start_without_foreground_ensures_detached(self) -> None:
        result = _run_cli_with_home(self.home, "supervisor", "start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Supervisor is running", result.stdout)
        self.assertTrue(ping_supervisor(self.paths))
        self.assertIsNotNone(_live_supervisor_pid(self.home))

    def test_supervisor_start_reports_missing_config_file(self) -> None:
        agents_path = self.home / "config" / "agents.toml"
        self.assertTrue(agents_path.exists())
        agents_path.unlink()

        started_at = time.time()
        result = _run_cli_with_home(self.home, "supervisor", "start")
        elapsed = time.time() - started_at

        combined = f"{result.stdout}\n{result.stderr}".lower()
        self.assertIn("missing config file", combined)
        self.assertIn("agents.toml", combined)
        self.assertLess(elapsed, 5.0, "missing config should fail immediately")
        self.assertFalse(ping_supervisor(self.paths))


if __name__ == "__main__":
    unittest.main()