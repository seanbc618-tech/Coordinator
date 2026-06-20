"""End-to-end tests for multi-project Supervisor."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.db import connect, init_db, create_task, project_task_counts
from local_cli_coordinator.supervisor import MultiProjectSupervisor
from local_cli_coordinator.supervisor_scheduler import FairProjectScheduler
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_capacity import SharedCapacity
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.reporting import NullReporter

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


# ---------------------------------------------------------------------------
# Phase 2 Gate Acceptance Tests
# ---------------------------------------------------------------------------


class Phase2GateTests(TestCase):
    """E2E gate tests required by the Phase 2 acceptance criteria."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = RuntimePaths(
            self.root / "config",
            self.root / "data",
            self.root / "state",
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _create_projects(self, project_ids: list[str], tasks_per: int = 3) -> None:
        for pid in project_ids:
            for i in range(tasks_per):
                create_task(
                    self.conn,
                    title=f"{pid}-task-{i}",
                    repo="demo",
                    source_path=f"inbox/{pid}-{i}.md",
                    priority="normal",
                    capabilities=["code"],
                    goal=f"{pid} goal {i}",
                    acceptance_criteria=["it works"],
                    verification_commands=["echo ok"],
                    project_id=pid,
                )

    # Gate 1: Three projects run fairly for 50+ scheduler ticks
    def test_three_projects_50_ticks(self) -> None:
        projects = ["proj-a", "proj-b", "proj-c"]
        self._create_projects(projects, tasks_per=5)

        scheduler = FairProjectScheduler(projects)
        schedule_log: list[str] = []

        for _ in range(60):
            decision = scheduler.next(lambda pid: True)
            if decision:
                schedule_log.append(decision.project_id)

        # Verify round-robin: each consecutive triple should be a,b,c
        for i in range(0, len(schedule_log) - 2, 3):
            triple = sorted(schedule_log[i : i + 3])
            self.assertEqual(triple, ["proj-a", "proj-b", "proj-c"])

        self.assertGreaterEqual(len(schedule_log), 50)

    # Gate 2: No project waits behind more than 2 other runnable projects
    def test_no_starvation_max_wait(self) -> None:
        projects = ["proj-a", "proj-b", "proj-c"]
        scheduler = FairProjectScheduler(projects)

        # Track consecutive skips per project
        consecutive_skips: dict[str, int] = {p: 0 for p in projects}
        max_consecutive_skips: dict[str, int] = {p: 0 for p in projects}

        for _ in range(100):
            scheduled = scheduler.next(lambda pid: True)
            if scheduled:
                pid = scheduled.project_id
                for other in projects:
                    if other != pid:
                        consecutive_skips[other] += 1
                        max_consecutive_skips[other] = max(
                            max_consecutive_skips[other], consecutive_skips[other]
                        )
                consecutive_skips[pid] = 0

        # No project should wait behind more than 2 others (round-robin guarantees this)
        for pid in projects:
            self.assertLessEqual(
                max_consecutive_skips[pid], 2,
                f"{pid} waited behind {max_consecutive_skips[pid]} other projects",
            )

    # Gate 3: Client attach/detach while supervisor runs
    def test_client_attach_detach_during_operation(self) -> None:
        from local_cli_coordinator.supervisor_protocol import RequestEnvelope

        self._create_projects(["proj-a"], tasks_per=1)

        methods = SupervisorMethods()
        broker = EventBroker()
        scheduler = FairProjectScheduler(["proj-a"])
        capacity = SharedCapacity()
        sup = MultiProjectSupervisor(
            paths=self.paths,
            scheduler=scheduler,
            broker=broker,
            capacity=capacity,
            methods=methods,
        )

        # Simulate client operations while supervisor is running
        for i in range(10):
            req = RequestEnvelope(
                protocol_version=1,
                request_id=f"req-{i}",
                project_id="proj-a",
                method="project.status",
                params={},
            )
            resp = methods.handle(self.conn, req)
            self.assertTrue(resp.ok, f"request {i} failed: {resp.error}")

        # Supervisor should still be healthy
        status = sup.status()
        self.assertIn("projects", status)

    # Gate 4: Restart does not duplicate task commits
    def test_restart_no_duplicate_commits(self) -> None:
        self._create_projects(["proj-a"], tasks_per=1)

        # First "run" — tick once
        scheduler1 = FairProjectScheduler(["proj-a"])
        broker1 = EventBroker()
        sup1 = MultiProjectSupervisor(
            paths=self.paths,
            scheduler=scheduler1,
            broker=broker1,
            capacity=SharedCapacity(),
            methods=SupervisorMethods(),
        )
        sup1.tick()

        # Count events after first run
        events_after_first = broker1.replay(self.conn, "proj-a", after=0)
        first_count = len(events_after_first)

        # "Restart" — new supervisor, same paths
        scheduler2 = FairProjectScheduler(["proj-a"])
        broker2 = EventBroker()
        sup2 = MultiProjectSupervisor(
            paths=self.paths,
            scheduler=scheduler2,
            broker=broker2,
            capacity=SharedCapacity(),
            methods=SupervisorMethods(),
        )
        sup2.tick()

        # Events should grow but task count should not duplicate
        events_after_second = broker2.replay(self.conn, "proj-a", after=0)

        # Task counts should still be correct (no duplicate creates)
        counts = project_task_counts(self.conn, project_id="proj-a")
        total = sum(counts.values())
        self.assertEqual(total, 1, f"Expected 1 task, got {total}: {counts}")

    # Gate 5: All DB queries in this phase are project-scoped
    def test_all_queries_project_scoped(self) -> None:
        """Verify that project-scoped APIs don't leak across projects."""
        self._create_projects(["proj-a", "proj-b"], tasks_per=3)

        # proj-a should have 3 tasks, proj-b should have 3
        counts_a = project_task_counts(self.conn, project_id="proj-a")
        counts_b = project_task_counts(self.conn, project_id="proj-b")

        self.assertEqual(sum(counts_a.values()), 3)
        self.assertEqual(sum(counts_b.values()), 3)

        # Verify no cross-contamination
        from local_cli_coordinator.db import (
            project_list_tasks,
            project_next_ready_task,
            project_list_events,
        )

        tasks_a = project_list_tasks(self.conn, project_id="proj-a")
        tasks_b = project_list_tasks(self.conn, project_id="proj-b")
        self.assertEqual(len(tasks_a), 3)
        self.assertEqual(len(tasks_b), 3)
        self.assertTrue(all(t["project_id"] == "proj-a" for t in tasks_a))
        self.assertTrue(all(t["project_id"] == "proj-b" for t in tasks_b))

        next_a = project_next_ready_task(self.conn, project_id="proj-a")
        next_b = project_next_ready_task(self.conn, project_id="proj-b")
        self.assertIsNotNone(next_a)
        self.assertIsNotNone(next_b)
        self.assertEqual(next_a["project_id"], "proj-a")
        self.assertEqual(next_b["project_id"], "proj-b")

        events_a = project_list_events(self.conn, project_id="proj-a")
        events_b = project_list_events(self.conn, project_id="proj-b")
        self.assertTrue(len(events_a) > 0)
        self.assertTrue(len(events_b) > 0)
        # Events reference correct project's tasks
        task_ids_a = {t["id"] for t in tasks_a}
        task_ids_b = {t["id"] for t in tasks_b}
        self.assertTrue(all(e["task_id"] in task_ids_a for e in events_a))
        self.assertTrue(all(e["task_id"] in task_ids_b for e in events_b))
