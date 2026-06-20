"""Phase 2 acceptance gate tests.

Adversarial end-to-end checks required before Phase 2 sign-off. These tests
exercise the integrated Supervisor stack (scheduler, broker, capacity, methods,
socket server) against real persistence. Failures indicate production gaps.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.db import (
    connect,
    create_task,
    init_db,
    project_list_daemon_runs,
    project_list_events,
    project_list_tasks,
    project_task_counts,
    transition_task,
)
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor import MultiProjectSupervisor
from local_cli_coordinator.supervisor_capacity import SharedCapacity
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.supervisor_protocol import (
    PROTOCOL_VERSION,
    RequestEnvelope,
    ResponseEnvelope,
)
from local_cli_coordinator.supervisor_scheduler import FairProjectScheduler
from local_cli_coordinator import supervisor_server as supervisor_server_module
from local_cli_coordinator.supervisor_server import SupervisorServer, send_request

from tests.helpers import ROOT, SRC

PROJECTS = ("proj-a", "proj-b", "proj-c")
MIN_TICKS = 50
MAX_WAIT_BEHIND_OTHER_RUNNABLE = 2

PROJECT_SCOPED_TABLES = frozenset({
    "tasks",
    "events",
    "artifacts",
    "daemon_runs",
    "task_leases",
    "supervisor_events",
})

SQL_DISCOVERY_ALLOWLIST = frozenset({
    "select distinct project_id from tasks",
})


def _create_projects(conn, project_ids: list[str], *, tasks_per: int = 2) -> None:
    for pid in project_ids:
        for i in range(tasks_per):
            create_task(
                conn,
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


def _make_supervisor(
    paths: RuntimePaths,
    project_ids: list[str],
    *,
    broker: EventBroker | None = None,
    methods: SupervisorMethods | None = None,
) -> MultiProjectSupervisor:
    return MultiProjectSupervisor(
        paths=paths,
        scheduler=FairProjectScheduler(project_ids),
        broker=broker or EventBroker(),
        capacity=SharedCapacity(max_global_running=4, max_per_project=2),
        methods=methods or SupervisorMethods(),
    )


def _project_request(method: str, project_id: str, request_id: str) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id,
        project_id=project_id,
        method=method,
        params={},
    )


def _max_wait_behind_other_runnable(schedule_log: list[str]) -> dict[str, int]:
    """Return max consecutive ticks each project waited while others ran."""
    projects = list(dict.fromkeys(schedule_log))
    waits: dict[str, int] = {pid: 0 for pid in projects}
    max_waits: dict[str, int] = {pid: 0 for pid in projects}

    for scheduled in schedule_log:
        for pid in projects:
            if pid == scheduled:
                waits[pid] = 0
            else:
                waits[pid] += 1
                max_waits[pid] = max(max_waits[pid], waits[pid])
    return max_waits


def _last_tick_project(sup: MultiProjectSupervisor) -> str:
    conn = connect(sup._paths.database)  # noqa: SLF001 - gate inspects persistence
    try:
        init_db(conn)
        row = conn.execute(
            "select project_id from supervisor_events "
            "where event_type = 'tick_scheduled' "
            "order by id desc limit 1"
        ).fetchone()
        return row["project_id"] if row else ""
    finally:
        conn.close()


def _extract_sql_literals(path: Path, *, prefix: str | None = None) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals: list[str] = []

    if prefix is None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            text = " ".join(node.value.lower().split())
            if any(keyword in text for keyword in ("select ", "insert ", "update ", "delete ")):
                literals.append(text)
        return literals

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith(prefix):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
                continue
            text = " ".join(child.value.lower().split())
            if any(keyword in text for keyword in ("select ", "insert ", "update ", "delete ")):
                literals.append(text)
    return literals


def _sql_targets_project_scoped_table(sql: str) -> str | None:
    for table in PROJECT_SCOPED_TABLES:
        if re.search(rf"\b{table}\b", sql):
            return table
    return None


class Phase2GateTestCase(TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
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
        self._tmpdir.cleanup()


class GateRunnablePolicyTests(Phase2GateTestCase):
    def test_gate_projects_without_ready_tasks_are_not_scheduled(self) -> None:
        """Runnable policy must consult project-scoped task state, not capacity only."""
        task_id = create_task(
            self.conn,
            title="only-task",
            repo="demo",
            source_path="inbox/a.md",
            priority="normal",
            capabilities=["code"],
            goal="g",
            acceptance_criteria=["a"],
            verification_commands=["echo ok"],
            project_id="proj-a",
        )
        transition_task(self.conn, task_id, "done", "accepted")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a"])
        ticks = 0
        for _ in range(10):
            before = _count_tick_events(self.paths)
            sup.tick()
            if _count_tick_events(self.paths) > before:
                ticks += 1

        self.assertEqual(
            ticks,
            0,
            "scheduler scheduled proj-a even though it has no ready tasks",
        )


class GateFairSchedulingTests(Phase2GateTestCase):
    def test_gate_three_projects_run_at_least_fifty_ticks(self) -> None:
        _create_projects(self.conn, list(PROJECTS), tasks_per=3)
        self.conn.close()

        sup = _make_supervisor(self.paths, list(PROJECTS))
        schedule_log: list[str] = []

        for _ in range(60):
            before = _count_tick_events(self.paths)
            sup.tick()
            after = _count_tick_events(self.paths)
            if after > before:
                schedule_log.append(_last_tick_project(sup))

        self.assertGreaterEqual(
            len(schedule_log),
            MIN_TICKS,
            f"expected >= {MIN_TICKS} scheduler ticks, got {len(schedule_log)}",
        )

        counts = Counter(schedule_log)
        for pid in PROJECTS:
            self.assertGreater(
                counts[pid],
                0,
                f"{pid} never scheduled across {len(schedule_log)} ticks",
            )

    def test_gate_max_wait_behind_two_other_runnable_projects(self) -> None:
        _create_projects(self.conn, list(PROJECTS), tasks_per=2)
        self.conn.close()

        sup = _make_supervisor(self.paths, list(PROJECTS))
        schedule_log: list[str] = []
        for _ in range(90):
            before = _count_tick_events(self.paths)
            sup.tick()
            if _count_tick_events(self.paths) > before:
                schedule_log.append(_last_tick_project(sup))

        max_waits = _max_wait_behind_other_runnable(schedule_log)
        for pid, waited in max_waits.items():
            self.assertLessEqual(
                waited,
                MAX_WAIT_BEHIND_OTHER_RUNNABLE,
                f"{pid} waited {waited} ticks behind other runnable projects",
            )


class GateSocketClientTests(Phase2GateTestCase):
    def _start_integrated_server(
        self,
        sup: MultiProjectSupervisor,
        *,
        tick_interval: float = 0.02,
    ) -> tuple[SupervisorServer, threading.Thread, threading.Event]:
        stop_ticks = threading.Event()

        def handler(request: RequestEnvelope) -> ResponseEnvelope:
            conn = connect(self.paths.database)
            try:
                init_db(conn)
                return sup._methods.handle(conn, request)  # noqa: SLF001
            finally:
                conn.close()

        server = SupervisorServer(self.paths, handler=handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        def tick_loop() -> None:
            while not stop_ticks.is_set() and not sup.is_shutdown_requested():
                sup.tick()
                time.sleep(tick_interval)

        tick_thread = threading.Thread(target=tick_loop, daemon=True)
        tick_thread.start()

        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                send_request(
                    self.paths.socket,
                    RequestEnvelope(
                        protocol_version=PROTOCOL_VERSION,
                        request_id="startup-ping",
                        project_id=None,
                        method="system.ping",
                        params={},
                    ),
                    timeout=0.2,
                )
                break
            except Exception:
                time.sleep(0.02)
        else:
            self.fail("integrated supervisor server did not become ready")

        return server, tick_thread, stop_ticks

    def test_gate_two_real_socket_clients_attach_detach_worker_continues(self) -> None:
        _create_projects(self.conn, ["proj-a"], tasks_per=2)
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a"])
        server, tick_thread, stop_ticks = self._start_integrated_server(sup)

        ticks_before = _count_tick_events(self.paths)

        clients: list[socket.socket] = []
        try:
            for i in range(2):
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(str(self.paths.socket))
                clients.append(client)
                req = _project_request("project.status", "proj-a", f"client-{i}")
                payload = (
                    '{"type":"request","protocol_version":1,'
                    f'"request_id":"{req.request_id}","project_id":"proj-a",'
                    '"method":"project.status","params":{}}\n'
                )
                client.sendall(payload.encode("utf-8"))
                client.settimeout(2.0)
                line = client.makefile("rb").readline()
                self.assertTrue(line, f"client {i} got no response")

            # Detach both clients abruptly (no explicit unsubscribe).
            for client in clients:
                client.close()
            clients.clear()

            deadline = time.time() + 2.0
            while time.time() < deadline:
                if _count_tick_events(self.paths) >= ticks_before + 5:
                    break
                time.sleep(0.05)
        finally:
            for client in clients:
                client.close()
            stop_ticks.set()
            server.request_shutdown()
            tick_thread.join(timeout=2.0)

        ticks_after = _count_tick_events(self.paths)
        self.assertGreater(
            ticks_after,
            ticks_before + 3,
            "scheduler worker stopped after clients detached",
        )


class GateRestartIdempotencyTests(Phase2GateTestCase):
    def test_gate_restart_does_not_duplicate_tasks_commits_or_events(self) -> None:
        task_ids = []
        for pid in PROJECTS:
            task_ids.append(
                create_task(
                    self.conn,
                    title=f"{pid}-only",
                    repo="demo",
                    source_path=f"inbox/{pid}.md",
                    priority="normal",
                    capabilities=["code"],
                    goal="g",
                    acceptance_criteria=["a"],
                    verification_commands=["echo ok"],
                    project_id=pid,
                )
            )

        # Simulate prior work so restart must not replay it.
        transition_task(self.conn, task_ids[0], "committing", "creating commit")
        transition_task(self.conn, task_ids[0], "done", "accepted")
        self.conn.commit()
        self.conn.close()

        broker = EventBroker()
        sup1 = _make_supervisor(self.paths, list(PROJECTS), broker=broker)
        for _ in range(15):
            sup1.tick()

        conn_snapshot = connect(self.paths.database)
        try:
            init_db(conn_snapshot)
            tasks_after_first = {
                pid: project_list_tasks(conn_snapshot, project_id=pid)
                for pid in PROJECTS
            }
            daemon_runs_after_first = {
                pid: project_list_daemon_runs(conn_snapshot, project_id=pid)
                for pid in PROJECTS
            }
        finally:
            conn_snapshot.close()

        events_after_first = _all_supervisor_events(self.paths)

        # Restart with fresh in-memory components, same on-disk state.
        sup2 = _make_supervisor(self.paths, list(PROJECTS), broker=EventBroker())
        for _ in range(15):
            sup2.tick()

        conn = connect(self.paths.database)
        try:
            init_db(conn)
            for pid in PROJECTS:
                tasks = project_list_tasks(conn, project_id=pid)
                self.assertEqual(
                    len(tasks),
                    len(tasks_after_first[pid]),
                    f"task rows duplicated for {pid}",
                )

            done_task = next(t for t in project_list_tasks(conn, project_id=PROJECTS[0]) if t["id"] == task_ids[0])
            self.assertEqual(done_task["state"], "done", "restart re-opened completed task")

            for pid in PROJECTS:
                runs = project_list_daemon_runs(conn, project_id=pid)
                self.assertEqual(
                    len(runs),
                    len(daemon_runs_after_first[pid]),
                    f"daemon run rows duplicated for {pid}",
                )
        finally:
            conn.close()

        events_after_second = _all_supervisor_events(self.paths)
        self.assertGreater(
            len(events_after_second),
            len(events_after_first),
            "restart produced no new supervisor events",
        )

        # Cursors must stay unique per project; no duplicate replay rows.
        for pid in PROJECTS:
            cursors = [e["cursor"] for e in events_after_second if e["project_id"] == pid]
            self.assertEqual(
                len(cursors),
                len(set(cursors)),
                f"duplicate supervisor event cursors for {pid}",
            )

        # Task lifecycle events must not be duplicated on restart.
        conn = connect(self.paths.database)
        try:
            for pid in PROJECTS:
                lifecycle = project_list_events(conn, project_id=pid)
                transitions = [(e["task_id"], e["old_state"], e["new_state"]) for e in lifecycle]
                self.assertEqual(
                    len(transitions),
                    len(set(transitions)),
                    f"duplicate task transition events for {pid}",
                )
        finally:
            conn.close()


class GateMethodsIntegrationTests(Phase2GateTestCase):
    def test_gate_events_subscribe_registers_on_live_broker(self) -> None:
        """events.subscribe must attach to the supervisor broker, not a placeholder."""
        _create_projects(self.conn, ["proj-a"], tasks_per=1)
        self.conn.close()

        broker = EventBroker()
        methods = SupervisorMethods()
        _make_supervisor(self.paths, ["proj-a"], broker=broker, methods=methods)

        conn = connect(self.paths.database)
        try:
            init_db(conn)
            resp = methods.handle(
                conn,
                _project_request("events.subscribe", "proj-a", "sub-1"),
            )
        finally:
            conn.close()

        self.assertTrue(resp.ok, resp.error)
        self.assertGreater(
            len(broker._subscribers),  # noqa: SLF001 - gate verifies broker wiring
            0,
            "events.subscribe did not register on the live supervisor broker",
        )


class GateProjectIsolationTests(Phase2GateTestCase):
    def test_gate_three_projects_tasks_and_events_strictly_isolated(self) -> None:
        _create_projects(self.conn, list(PROJECTS), tasks_per=4)
        self.conn.close()

        broker = EventBroker()
        sup = _make_supervisor(self.paths, list(PROJECTS), broker=broker)
        for _ in range(30):
            sup.tick()

        conn = connect(self.paths.database)
        try:
            init_db(conn)
            task_ids_by_project = {
                pid: {t["id"] for t in project_list_tasks(conn, project_id=pid)}
                for pid in PROJECTS
            }
            for pid in PROJECTS:
                self.assertEqual(len(task_ids_by_project[pid]), 4)

            for pid in PROJECTS:
                for event in project_list_events(conn, project_id=pid):
                    self.assertEqual(event["project_id"], pid)
                    self.assertIn(event["task_id"], task_ids_by_project[pid])

            for pid in PROJECTS:
                rows = conn.execute(
                    "select project_id, payload from supervisor_events where project_id = ?",
                    (pid,),
                ).fetchall()
                self.assertTrue(rows, f"no supervisor events for {pid}")
                for row in rows:
                    self.assertEqual(row["project_id"], pid)
                    payload = row["payload"]
                    if '"project_id"' in payload:
                        self.assertIn(pid, payload)

            other_projects = {
                pid: conn.execute(
                    "select id from tasks where project_id = ?",
                    (pid,),
                ).fetchall()
                for pid in PROJECTS
            }
            for pid in PROJECTS:
                ids = {row["id"] for row in other_projects[pid]}
                self.assertEqual(ids, task_ids_by_project[pid])
        finally:
            conn.close()


class GateSqlAuditTests(TestCase):
    def test_gate_phase2_sql_queries_include_project_id(self) -> None:
        """Audit SQL introduced in Phase 2 (supervisor modules + project_* APIs)."""
        violations: list[str] = []

        supervisor_sources = (
            SRC / "local_cli_coordinator" / "supervisor.py",
            SRC / "local_cli_coordinator" / "supervisor_events.py",
        )
        for path in supervisor_sources:
            for sql in _extract_sql_literals(path):
                violations.extend(_audit_sql(path.name, sql))

        db_path = SRC / "local_cli_coordinator" / "db.py"
        for sql in _extract_sql_literals(db_path, prefix="project_"):
            violations.extend(_audit_sql(db_path.name, sql))

        self.assertEqual(
            violations,
            [],
            "Phase 2 SQL must scope project data:\n" + "\n".join(violations),
        )


class GateFileHandleLeakTests(TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)
        self._env = os.environ.copy()
        self._env["PYTHONPATH"] = str(SRC)
        self._env["COORDINATOR_HOME"] = str(self.home)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_gate_send_request_closes_socket_readers(self) -> None:
        """Client transport must close socket makefile readers after each request."""
        send_source = inspect.getsource(send_request)
        serve_source = inspect.getsource(supervisor_server_module.SupervisorServer._serve_client)

        self.assertNotIn(
            ".makefile(",
            send_source,
            "send_request uses socket makefile without an explicit close helper",
        )
        self.assertNotIn(
            'makefile("rb")',
            serve_source,
            "_serve_client uses socket makefile without an explicit close helper",
        )

    def test_gate_subprocess_supervisor_closes_stderr_pipe(self) -> None:
        """Mirror MultiProjectSupervisorCliTest: stderr PIPE must be drained and closed."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "local_cli_coordinator", "supervisor", "start", "--foreground"],
            cwd=ROOT,
            env=self._env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        deadline = time.time() + 5.0
        started = False
        while time.time() < deadline:
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr else ""
                self.fail(f"supervisor exited early: {stderr}")
            status = subprocess.run(
                [sys.executable, "-m", "local_cli_coordinator", "supervisor", "status"],
                cwd=ROOT,
                env=self._env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if status.returncode == 0 and "running" in status.stdout.lower():
                started = True
                break
            time.sleep(0.05)
        self.assertTrue(started, "supervisor did not start")

        subprocess.run(
            [sys.executable, "-m", "local_cli_coordinator", "supervisor", "stop"],
            cwd=ROOT,
            env=self._env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        proc.wait(timeout=5.0)

        self.assertIsNotNone(proc.stderr)
        self.assertTrue(
            proc.stderr.closed,
            "supervisor subprocess left stderr PIPE open; drain and close after stop",
        )


def _audit_sql(source_name: str, sql: str) -> list[str]:
    table = _sql_targets_project_scoped_table(sql)
    if table is None:
        return []

    normalized = " ".join(sql.split())
    if normalized in SQL_DISCOVERY_ALLOWLIST:
        return []
    if "project_id" in sql:
        return []
    if sql.strip().startswith("insert into"):
        return [f"{source_name}: INSERT into {table} missing project_id column"]
    return [f"{source_name}: query on {table} missing project_id predicate: {sql!r}"]


def _count_tick_events(paths: RuntimePaths) -> int:
    conn = connect(paths.database)
    try:
        init_db(conn)
        row = conn.execute(
            "select count(*) as cnt from supervisor_events where event_type = 'tick_scheduled'"
        ).fetchone()
        return int(row["cnt"])
    finally:
        conn.close()


def _all_supervisor_events(paths: RuntimePaths) -> list[dict[str, object]]:
    conn = connect(paths.database)
    try:
        init_db(conn)
        rows = conn.execute(
            "select project_id, cursor, event_type, payload from supervisor_events order by id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()