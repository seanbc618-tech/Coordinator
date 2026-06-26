"""Tests for supervisor administrative CLI commands."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from local_cli_coordinator.cli import build_parser
from tests.helpers import ROOT, SRC, run_cli


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


class SupervisorParserTest(unittest.TestCase):
    """Verify supervisor subcommands parse correctly."""

    def test_supervisor_start_foreground(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["supervisor", "start", "--foreground"])
        self.assertEqual(args.command, "supervisor")
        self.assertEqual(args.supervisor_command, "start")
        self.assertTrue(args.foreground)

    def test_supervisor_status(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["supervisor", "status"])
        self.assertEqual(args.command, "supervisor")
        self.assertEqual(args.supervisor_command, "status")

    def test_supervisor_stop(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["supervisor", "stop"])
        self.assertEqual(args.supervisor_command, "stop")

    def test_supervisor_restart(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["supervisor", "restart"])
        self.assertEqual(args.command, "supervisor")
        self.assertEqual(args.supervisor_command, "restart")

    def test_project_inspect(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["project", "inspect", "/tmp/repo"])
        self.assertEqual(args.command, "project")
        self.assertEqual(args.project_command, "inspect")
        self.assertEqual(args.path, "/tmp/repo")

    def test_project_add_yes(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["project", "add", "/tmp/repo", "--yes"])
        self.assertEqual(args.command, "project")
        self.assertEqual(args.project_command, "add")
        self.assertEqual(args.path, "/tmp/repo")
        self.assertTrue(args.yes)

    def test_project_add_without_yes(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["project", "add", "/tmp/repo"])
        self.assertFalse(args.yes)

    def test_existing_commands_still_work(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--root", "/tmp/test", "status"])
        self.assertEqual(args.root, "/tmp/test")
        self.assertEqual(args.command, "status")


class SupervisorCliIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)
        self._processes: list[subprocess.Popen[str]] = []
        self._write_config()

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

    def tearDown(self) -> None:
        for process in self._processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            if process.stderr:
                try:
                    process.stderr.read()
                except (OSError, ValueError):
                    pass
                process.stderr.close()
        self._tmpdir.cleanup()

    def _start_supervisor(self) -> subprocess.Popen[str]:
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
        deadline = time.time() + 5.0
        while time.time() < deadline:
            status = _run_cli_with_home(self.home, "supervisor", "status")
            if status.returncode == 0 and "Supervisor is running" in status.stdout:
                return process
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr else ""
                self.fail(f"supervisor exited before becoming ready: stderr={stderr}")
            time.sleep(0.05)
        self.fail("supervisor did not become ready in time")

    def test_foreground_start_status_and_stop(self) -> None:
        process = self._start_supervisor()

        status = _run_cli_with_home(self.home, "supervisor", "status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("Supervisor is running", status.stdout)

        stop = _run_cli_with_home(self.home, "supervisor", "stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIn("Supervisor shutting down", stop.stdout)

        process.wait(timeout=5.0)
        self.assertEqual(process.returncode, 0)

        stopped = _run_cli_with_home(self.home, "supervisor", "status")
        self.assertEqual(stopped.returncode, 1)
        self.assertIn("not running", stopped.stdout)

    def test_second_start_is_rejected(self) -> None:
        self._start_supervisor()
        duplicate = _run_cli_with_home(self.home, "supervisor", "start", "--foreground")
        self.assertEqual(duplicate.returncode, 1)
        self.assertIn("already running", duplicate.stderr)

    def test_start_without_foreground_starts_detached(self) -> None:
        result = _run_cli_with_home(self.home, "supervisor", "start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Supervisor is running", result.stdout)

        status = _run_cli_with_home(self.home, "supervisor", "status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("Supervisor is running", status.stdout)

    def test_supervisor_restart_replaces_process(self) -> None:
        self._start_supervisor()
        lock_path = self.home / "state" / "supervisor.lock"
        old_pid = int(json.loads(lock_path.read_text(encoding="utf-8"))["pid"])

        restart = _run_cli_with_home(self.home, "supervisor", "restart")
        self.assertEqual(restart.returncode, 0, restart.stderr)
        self.assertIn("Supervisor restarted", restart.stdout)

        deadline = time.time() + 10.0
        new_pid: int | None = None
        while time.time() < deadline:
            if not lock_path.exists():
                time.sleep(0.05)
                continue
            new_pid = int(json.loads(lock_path.read_text(encoding="utf-8"))["pid"])
            if new_pid != old_pid:
                break
            time.sleep(0.05)
        self.assertIsNotNone(new_pid)
        assert new_pid is not None
        self.assertNotEqual(new_pid, old_pid)

        socket_path = self.home / "state" / "coordinator.sock"
        self.assertTrue(socket_path.exists())
        self.assertTrue(lock_path.exists())

        status = _run_cli_with_home(self.home, "supervisor", "status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("Supervisor is running", status.stdout)