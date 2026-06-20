"""End-to-end tests for multi-project Supervisor."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.db import connect, init_db, create_task
from local_cli_coordinator.supervisor import MultiProjectSupervisor
from local_cli_coordinator.supervisor_scheduler import FairProjectScheduler
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_capacity import SharedCapacity
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import ROOT, SRC


def _make_supervisor(tmp: Path, projects: list[str]) -> MultiProjectSupervisor:
    paths = RuntimePaths(tmp / "config", tmp / "data", tmp / "state")
    paths.create()
    conn = connect(paths.database)
    init_db(conn)

    for pid in projects:
        create_task(
            conn, title=f"task-{pid}", repo="demo", source_path="x",
            priority="normal", capabilities=["code"], goal="g",
            acceptance_criteria=["a"], verification_commands=[],
            project_id=pid,
        )
    conn.close()

    scheduler = FairProjectScheduler(projects)
    broker = EventBroker()
    capacity = SharedCapacity(max_global_running=4, max_per_project=2)
    methods = SupervisorMethods()

    return MultiProjectSupervisor(
        paths=paths,
        scheduler=scheduler,
        broker=broker,
        capacity=capacity,
        methods=methods,
    )


class MultiProjectSupervisorTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_supervisor_creates_and_runs(self) -> None:
        sup = _make_supervisor(self.root, ["proj-a", "proj-b"])
        self.assertIsNotNone(sup)

    def test_tick_processes_projects(self) -> None:
        sup = _make_supervisor(self.root, ["proj-a", "proj-b"])
        # tick should attempt to schedule and process projects
        sup.tick()

    def test_status_fields(self) -> None:
        sup = _make_supervisor(self.root, ["proj-a"])
        status = sup.status()
        self.assertIn("projects", status)
        self.assertIn("active_tasks", status)

    def test_graceful_shutdown(self) -> None:
        sup = _make_supervisor(self.root, ["proj-a"])
        sup.request_shutdown()
        self.assertTrue(sup.is_shutdown_requested())


class MultiProjectSupervisorCliTest(TestCase):
    """Subprocess integration tests for the Supervisor."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)
        self._env = os.environ.copy()
        self._env["PYTHONPATH"] = str(SRC)
        self._env["COORDINATOR_HOME"] = str(self.home)
        self._processes: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        for p in self._processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
        self._tmpdir.cleanup()

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "local_cli_coordinator", *args],
            cwd=ROOT,
            env=self._env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _start_supervisor(self) -> subprocess.Popen[str]:
        proc = subprocess.Popen(
            [sys.executable, "-m", "local_cli_coordinator",
             "supervisor", "start", "--foreground"],
            cwd=ROOT,
            env=self._env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._processes.append(proc)
        deadline = time.time() + 5
        while time.time() < deadline:
            r = self._run("supervisor", "status")
            if r.returncode == 0 and "running" in r.stdout.lower():
                return proc
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr else ""
                proc.stderr.close() if proc.stderr else None
                self.fail(f"supervisor exited: {stderr}")
            time.sleep(0.05)
        self.fail("supervisor did not start")

    def test_supervisor_start_stop(self) -> None:
        proc = self._start_supervisor()
        r = self._run("supervisor", "stop")
        self.assertEqual(r.returncode, 0)
        proc.wait(timeout=5)

    def test_supervisor_status_when_running(self) -> None:
        self._start_supervisor()
        r = self._run("supervisor", "status")
        self.assertEqual(r.returncode, 0)
        self.assertIn("running", r.stdout.lower())
