"""Phase 2 acceptance gate tests (adversarial).

Requires real fake-agent execution, per-project repo attribution,
capacity enforcement, unified pause, live socket events, restart
exactly-once, engine pipeline fidelity, ResourceWarning-free execution,
safe shutdown, subscription cleanup, and default XDG config loading.
Empty tick_scheduled-only passes are rejected.
"""

from __future__ import annotations

import ast
import gc
import inspect
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import warnings
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import (
    artifact_kinds,
    connect,
    create_task,
    get_task,
    init_db,
    list_attempts,
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
    decode_envelope,
)
from local_cli_coordinator.supervisor_scheduler import FairProjectScheduler
from local_cli_coordinator import supervisor_server as supervisor_server_module
from local_cli_coordinator.supervisor_server import SupervisorServer, send_request

from tests.helpers import ROOT, SRC, init_git_repo

PROJECTS = ("proj-a", "proj-b", "proj-c")
MIN_TICKS = 50
MAX_WAIT_BEHIND_OTHER_RUNNABLE = 2
MARKER = "feature.txt"
MARKER_CONTENT = "done"

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


def _worker_command(*, slow_seconds: float = 0.0) -> str:
    if slow_seconds > 0:
        body = (
            f"import time; time.sleep({slow_seconds}); "
            f"from pathlib import Path; Path('{MARKER}').write_text('{MARKER_CONTENT}')"
        )
    else:
        body = f"from pathlib import Path; Path('{MARKER}').write_text('{MARKER_CONTENT}')"
    return f'{sys.executable} -c "{body}"'


def _verify_command() -> str:
    return (
        f'{sys.executable} -c "from pathlib import Path; '
        f"assert Path('{MARKER}').read_text() == '{MARKER_CONTENT}'\""
    )


def _failing_verify_command() -> str:
    return f'{sys.executable} -c "import sys; sys.exit(1)"'


@contextmanager
def _no_resource_warnings():
    """Treat leaked DB sockets and other ResourceWarnings as hard failures."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        yield
        gc.collect()


def _policy(*, max_tasks_per_run: int = 1) -> PolicyConfig:
    return PolicyConfig(
        require_single_repo=True,
        require_acceptance_criteria=True,
        require_verification_commands=True,
        require_handoff_summary=False,
        max_files_touched=3,
        max_expected_minutes=30,
        max_attempts=3,
        split_if_touches_multiple_subsystems=True,
        split_if_research_and_code_are_mixed=True,
        max_tasks_per_run=max_tasks_per_run,
        max_tasks_per_day=100,
        max_consecutive_failures=3,
    )


def _setup_project_repos(root: Path) -> dict[str, Path]:
    repos: dict[str, Path] = {}
    for pid in PROJECTS:
        repo = root / "repos" / pid
        init_git_repo(repo)
        repos[pid] = repo
    return repos


def _gate_config(
    repos: dict[str, Path],
    *,
    slow_seconds: float = 0.0,
    max_global_concurrency: int = 4,
    verify_commands: list[str] | None = None,
) -> CoordinatorConfig:
    repo_configs = {
        f"demo-{pid}": RepoConfig(
            id=f"demo-{pid}",
            path=repo_path,
            default_branch="main",
            remote="origin",
            branch_prefix="coord/",
            allow_push=False,
            merge_policy="no_push",
            verify_commands=verify_commands or [_verify_command()],
            review_policy="tests_only",
        )
        for pid, repo_path in repos.items()
    }
    return CoordinatorConfig(
        agents={
            "worker": AgentConfig(
                id="worker",
                command=_worker_command(slow_seconds=slow_seconds),
                capabilities=["code"],
                max_concurrency=max_global_concurrency,
                role="worker",
            )
        },
        repos=repo_configs,
        policy=_policy(),
        daemon_policy=DaemonPolicyConfig(run_discovery_before_tasks=False),
    )


def _write_gate_config_files(home: Path, repos: dict[str, Path], *, slow_seconds: float = 0.0) -> None:
    config_dir = home / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    worker = _worker_command(slow_seconds=slow_seconds).replace('"', '\\"')
    config_dir.joinpath("agents.toml").write_text(textwrap.dedent(f"""
        [agents.worker]
        command = "{worker}"
        capabilities = ["code"]
        max_concurrency = 2
        role = "worker"
    """).strip(), encoding="utf-8")

    repo_lines = []
    for pid, repo_path in repos.items():
        verify = _verify_command().replace('"', '\\"')
        repo_lines.append(textwrap.dedent(f"""
            [repos."demo-{pid}"]
            path = "{repo_path}"
            default_branch = "main"
            remote = "origin"
            branch_prefix = "coord/"
            allow_push = false
            merge_policy = "no_push"
            review_policy = "tests_only"
            verify_commands = ["{verify}"]
        """).strip())
    config_dir.joinpath("repos.toml").write_text("\n\n".join(repo_lines), encoding="utf-8")

    config_dir.joinpath("policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = true
        require_acceptance_criteria = true
        require_verification_commands = true
        require_handoff_summary = false
        max_files_touched = 3
        max_expected_minutes = 30
        max_attempts = 3
        max_tasks_per_run = 1
        max_tasks_per_day = 100
        max_consecutive_failures = 3
        split_if_touches_multiple_subsystems = true
        split_if_research_and_code_are_mixed = true

        [daemon_policy]
        run_discovery_before_tasks = false
    """).strip(), encoding="utf-8")


def _write_xdg_gate_config_files(
    config_dir: Path,
    repos: dict[str, Path],
    *,
    slow_seconds: float = 0.0,
    verify_command: str | None = None,
) -> None:
    """Write config directly into XDG config_dir (not config_dir/config)."""
    config_dir.mkdir(parents=True, exist_ok=True)
    worker = _worker_command(slow_seconds=slow_seconds).replace('"', '\\"')
    verify = (verify_command or _verify_command()).replace('"', '\\"')
    config_dir.joinpath("agents.toml").write_text(textwrap.dedent(f"""
        [agents.worker]
        command = "{worker}"
        capabilities = ["code"]
        max_concurrency = 2
        role = "worker"
    """).strip(), encoding="utf-8")

    repo_lines = []
    for pid, repo_path in repos.items():
        repo_lines.append(textwrap.dedent(f"""
            [repos."demo-{pid}"]
            path = "{repo_path}"
            default_branch = "main"
            remote = "origin"
            branch_prefix = "coord/"
            allow_push = false
            merge_policy = "no_push"
            review_policy = "tests_only"
            verify_commands = ["{verify}"]
        """).strip())
    config_dir.joinpath("repos.toml").write_text("\n\n".join(repo_lines), encoding="utf-8")

    config_dir.joinpath("policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = true
        require_acceptance_criteria = true
        require_verification_commands = true
        require_handoff_summary = false
        max_files_touched = 3
        max_expected_minutes = 30
        max_attempts = 3
        max_tasks_per_run = 1
        max_tasks_per_day = 100
        max_consecutive_failures = 3
        split_if_touches_multiple_subsystems = true
        split_if_research_and_code_are_mixed = true

        [daemon_policy]
        run_discovery_before_tasks = false
    """).strip(), encoding="utf-8")


def _create_project_task(conn, *, project_id: str, title: str) -> str:
    return create_task(
        conn,
        title=title,
        repo=f"demo-{project_id}",
        source_path=f"inbox/{project_id}/{title}.md",
        priority="normal",
        capabilities=["code"],
        goal=f"Create {MARKER} for {project_id}",
        acceptance_criteria=[f"{MARKER} contains {MARKER_CONTENT}"],
        verification_commands=[],
        project_id=project_id,
    )


def _make_supervisor(
    paths: RuntimePaths,
    project_ids: list[str],
    config: CoordinatorConfig,
    *,
    broker: EventBroker | None = None,
    capacity: SharedCapacity | None = None,
    methods: SupervisorMethods | None = None,
) -> MultiProjectSupervisor:
    shared_broker = broker or EventBroker()
    shared_methods = methods or SupervisorMethods(broker=shared_broker)
    if methods is not None:
        shared_methods._broker = shared_broker  # noqa: SLF001
    return MultiProjectSupervisor(
        paths=paths,
        scheduler=FairProjectScheduler(project_ids),
        broker=shared_broker,
        capacity=capacity or SharedCapacity(max_global_running=4, max_per_project=2),
        methods=shared_methods,
        config=config,
    )


def _project_request(
    method: str,
    project_id: str,
    request_id: str,
    **params: object,
) -> RequestEnvelope:
    return RequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id,
        project_id=project_id,
        method=method,
        params=params,
    )


def _pending_futures(sup: MultiProjectSupervisor) -> list:
    if hasattr(sup, "_futures_lock"):
        with sup._futures_lock:  # noqa: SLF001
            return [f for f in sup._active_futures.values() if not f.done()]  # noqa: SLF001
    return [f for f in sup._active_futures.values() if not f.done()]  # noqa: SLF001


def _drain_workers(sup: MultiProjectSupervisor, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pending_futures(sup):
            if hasattr(sup, "join_workers"):
                sup.join_workers(timeout=1.0)
            return
        time.sleep(0.02)
    for future in _pending_futures(sup):
        future.result(timeout=5)


def _tick_and_drain(sup: MultiProjectSupervisor) -> None:
    with _no_resource_warnings():
        sup.tick()
        _drain_workers(sup)


def _wait_until(paths: RuntimePaths, predicate, *, timeout: float = 60.0, tick_fn) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        tick_fn()
        time.sleep(0.05)
    raise AssertionError("condition not met before timeout")


def _done_counts(paths: RuntimePaths, project_ids: list[str]) -> dict[str, int]:
    conn = connect(paths.database)
    try:
        init_db(conn)
        return {
            pid: project_task_counts(conn, project_id=pid).get("done", 0)
            for pid in project_ids
        }
    finally:
        conn.close()


def _count_cycle_complete_with_work(paths: RuntimePaths) -> int:
    conn = connect(paths.database)
    try:
        init_db(conn)
        rows = conn.execute(
            "select payload from supervisor_events where event_type = 'cycle_complete'"
        ).fetchall()
        total = 0
        for row in rows:
            payload = json.loads(row["payload"])
            total += int(payload.get("tasks_processed", 0))
        return total
    finally:
        conn.close()


def _schedule_log(paths: RuntimePaths, *, limit: int = 300) -> list[str]:
    conn = connect(paths.database)
    try:
        init_db(conn)
        rows = conn.execute(
            "select project_id from supervisor_events "
            "where event_type = 'tick_scheduled' "
            "order by id desc limit ?",
            (limit,),
        ).fetchall()
        return [row["project_id"] for row in reversed(rows)]
    finally:
        conn.close()


def _max_wait_behind_other_runnable(schedule_log: list[str]) -> dict[str, int]:
    projects = list(dict.fromkeys(schedule_log))
    waits = {pid: 0 for pid in projects}
    max_waits = {pid: 0 for pid in projects}
    for scheduled in schedule_log:
        for pid in projects:
            if pid == scheduled:
                waits[pid] = 0
            else:
                waits[pid] += 1
                max_waits[pid] = max(max_waits[pid], waits[pid])
    return max_waits


def _active_db_leases(paths: RuntimePaths) -> list[dict[str, str]]:
    conn = connect(paths.database)
    try:
        init_db(conn)
        rows = conn.execute(
            "select tl.task_id, t.project_id "
            "from task_leases tl join tasks t on t.id = tl.task_id "
            "where tl.released_at is null"
        ).fetchall()
        return [{"task_id": row["task_id"], "project_id": row["project_id"]} for row in rows]
    finally:
        conn.close()


def _transition_count(conn, task_id: str, new_state: str) -> int:
    row = conn.execute(
        "select count(*) as cnt from events where task_id = ? and new_state = ?",
        (task_id, new_state),
    ).fetchone()
    return int(row["cnt"])


def _assert_task_completed_via_engine(
    test: TestCase,
    conn,
    task_id: str,
    *,
    bare_repo: Path | None = None,
) -> Path:
    """Successful tasks must run in an isolated worktree via the engine pipeline."""
    task = get_task(conn, task_id)
    test.assertEqual(task["state"], "done", f"task {task_id} not done")
    test.assertTrue(task["worktree_path"], "task missing worktree_path (engine pipeline bypassed)")
    worktree = Path(task["worktree_path"])
    test.assertTrue(worktree.is_dir(), f"worktree missing: {worktree}")
    if bare_repo is not None:
        test.assertNotEqual(
            worktree.resolve(),
            bare_repo.resolve(),
            "worktree must not be bare repo root",
        )
        test.assertFalse(
            (bare_repo / MARKER).exists(),
            "marker must not land on bare repo main checkout",
        )
    marker = worktree / MARKER
    test.assertTrue(marker.exists(), f"marker missing in worktree {worktree}")
    test.assertEqual(marker.read_text(encoding="utf-8"), MARKER_CONTENT)
    kinds = artifact_kinds(conn, task_id)
    test.assertIn("verifier_log", kinds, "missing verifier_log (verification step skipped)")
    return worktree


def _running_task_count(conn, *, project_id: str) -> int:
    row = conn.execute(
        "select count(*) as cnt from tasks where project_id = ? and state = 'running'",
        (project_id,),
    ).fetchone()
    return int(row["cnt"])


class Phase2GateHarness(TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = RuntimePaths(
            self.root / "config",
            self.root / "data",
            self.root / "state",
        )
        self.paths.create()
        self.repos = _setup_project_repos(self.root)
        self.config = _gate_config(self.repos)
        self.conn = connect(self.paths.database)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            gc.collect()
            self._tmpdir.cleanup()


class GateExecutionTests(Phase2GateHarness):
    def test_gate_fake_agent_completes_tasks_in_project_repos(self) -> None:
        task_ids = {
            pid: _create_project_task(self.conn, project_id=pid, title=f"task-{pid}")
            for pid in PROJECTS
        }
        self.conn.close()

        sup = _make_supervisor(self.paths, list(PROJECTS), self.config)
        with _no_resource_warnings():
            _wait_until(
                self.paths,
                lambda: sum(_done_counts(self.paths, list(PROJECTS)).values()) == len(PROJECTS),
                tick_fn=lambda: _tick_and_drain(sup),
            )

        self.assertGreater(_count_cycle_complete_with_work(self.paths), 0)

        conn = connect(self.paths.database)
        try:
            for pid in PROJECTS:
                counts = project_task_counts(conn, project_id=pid)
                self.assertEqual(counts.get("done", 0), 1, f"{pid} task did not finish")
                _assert_task_completed_via_engine(
                    self, conn, task_ids[pid], bare_repo=self.repos[pid],
                )
        finally:
            conn.close()


class GateProjectAttributionTests(Phase2GateHarness):
    def test_gate_scheduled_project_claims_only_its_own_ready_task(self) -> None:
        older = _create_project_task(self.conn, project_id="proj-b", title="older-b")
        newer = _create_project_task(self.conn, project_id="proj-a", title="newer-a")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a", "proj-b"], self.config)
        _tick_and_drain(sup)

        conn = connect(self.paths.database)
        try:
            proj_a = get_task(conn, newer)
            proj_b = get_task(conn, older)
            self.assertEqual(proj_a["state"], "done")
            self.assertEqual(proj_b["state"], "ready")
            _assert_task_completed_via_engine(self, conn, newer, bare_repo=self.repos["proj-a"])
            self.assertFalse((self.repos["proj-b"] / MARKER).exists())
        finally:
            conn.close()


class GateCapacityTests(Phase2GateHarness):
    def test_gate_shared_capacity_acquire_and_release_are_used(self) -> None:
        slow_config = _gate_config(self.repos, slow_seconds=0.8)
        _create_project_task(self.conn, project_id="proj-a", title="slow-a")
        self.conn.close()

        capacity = SharedCapacity(max_global_running=1, max_per_project=1)
        sup = _make_supervisor(self.paths, list(PROJECTS), slow_config, capacity=capacity)

        sup.tick()
        seen_active = 0
        deadline = time.time() + 3.0
        while time.time() < deadline:
            seen_active = max(seen_active, capacity.active_count())
            if seen_active > 0:
                break
            time.sleep(0.02)
        _drain_workers(sup)

        self.assertGreater(
            seen_active,
            0,
            "SharedCapacity.try_acquire/release never held an active lease during execution",
        )

    def test_gate_projects_run_concurrently_via_worker_executor(self) -> None:
        slow_config = _gate_config(self.repos, slow_seconds=1.0, max_global_concurrency=3)
        for pid in PROJECTS:
            _create_project_task(self.conn, project_id=pid, title=f"slow-{pid}")
        self.conn.close()

        sup = _make_supervisor(
            self.paths,
            list(PROJECTS),
            slow_config,
            capacity=SharedCapacity(max_global_running=3, max_per_project=1),
        )

        stop = threading.Event()

        def burst_ticks() -> None:
            while not stop.is_set():
                sup.tick()
                time.sleep(0.01)

        thread = threading.Thread(target=burst_ticks, daemon=True)
        thread.start()

        observed = 0
        deadline = time.time() + 10.0
        while time.time() < deadline:
            projects = {lease["project_id"] for lease in _active_db_leases(self.paths)}
            observed = max(observed, len(projects))
            if observed >= 2:
                break
            time.sleep(0.02)

        stop.set()
        thread.join(timeout=2.0)
        _drain_workers(sup)

        self.assertGreaterEqual(observed, 2)


class GatePauseTests(Phase2GateHarness):
    def _start_server(self, sup: MultiProjectSupervisor) -> SupervisorServer:
        def handler(request: RequestEnvelope) -> ResponseEnvelope:
            if request.method == "system.ping":
                return ResponseEnvelope(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=request.request_id,
                    ok=True,
                    result={"pong": True},
                    error=None,
                )
            conn = connect(self.paths.database)
            try:
                init_db(conn)
                return sup._methods.handle(conn, request)  # noqa: SLF001
            finally:
                conn.close()

        server = SupervisorServer(self.paths, handler=handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                send_request(
                    self.paths.socket,
                    RequestEnvelope(
                        protocol_version=PROTOCOL_VERSION,
                        request_id="ping",
                        project_id=None,
                        method="system.ping",
                        params={},
                    ),
                )
                return server
            except Exception:
                time.sleep(0.02)
        self.fail("server not ready")

    def test_gate_api_pause_prevents_scheduling_and_execution(self) -> None:
        task_a = _create_project_task(self.conn, project_id="proj-a", title="pause-a")
        task_b = _create_project_task(self.conn, project_id="proj-b", title="pause-b")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a", "proj-b"], self.config)
        server = self._start_server(sup)
        try:
            pause = send_request(
                self.paths.socket,
                _project_request("project.pause", "proj-a", "pause-req"),
            )
            self.assertTrue(pause.ok, pause.error)

            for _ in range(12):
                _tick_and_drain(sup)

            conn = connect(self.paths.database)
            try:
                self.assertEqual(get_task(conn, task_a)["state"], "ready")
                self.assertEqual(get_task(conn, task_b)["state"], "done")
                self.assertFalse((self.repos["proj-a"] / MARKER).exists())
                _assert_task_completed_via_engine(self, conn, task_b, bare_repo=self.repos["proj-b"])
            finally:
                conn.close()
        finally:
            server.request_shutdown()


class GateLiveEventTests(Phase2GateHarness):
    def test_gate_socket_client_receives_live_event_after_subscribe(self) -> None:
        _create_project_task(self.conn, project_id="proj-a", title="live-a")
        self.conn.close()

        broker = EventBroker()
        methods = SupervisorMethods(broker=broker)
        sup = _make_supervisor(self.paths, ["proj-a"], self.config, broker=broker, methods=methods)

        def handler(request: RequestEnvelope) -> ResponseEnvelope:
            conn = connect(self.paths.database)
            try:
                init_db(conn)
                return methods.handle(conn, request)
            finally:
                conn.close()

        server = SupervisorServer(self.paths, handler=handler, methods=methods)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(0.1)

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(self.paths.socket))
        try:
            subscribe = (
                '{"type":"request","protocol_version":1,'
                '"request_id":"sub-1","project_id":"proj-a",'
                '"method":"events.subscribe","params":{"after":0}}\n'
            )
            client.sendall(subscribe.encode("utf-8"))
            client.settimeout(3.0)
            first = supervisor_server_module._recv_line(client)
            self.assertTrue(first, "missing subscribe response")

            tick_thread = threading.Thread(
                target=lambda: _tick_and_drain(sup),
                daemon=False,
            )
            tick_thread.start()
            try:
                try:
                    second = supervisor_server_module._recv_line(client)
                except TimeoutError:
                    self.fail("socket client did not receive a live event after subscribe")

                envelope = decode_envelope(second.decode("utf-8").strip())
                self.assertEqual(getattr(envelope, "type", None), "event")
                self.assertEqual(getattr(envelope, "event_type", None), "tick_scheduled")
            finally:
                tick_thread.join(timeout=30.0)
                self.assertFalse(
                    tick_thread.is_alive(),
                    "background tick thread did not finish before resource audit",
                )
        finally:
            client.close()
            server.request_shutdown()


class GateRestartExactlyOnceTests(Phase2GateHarness):
    def test_gate_restart_executes_ready_tasks_exactly_once(self) -> None:
        task_ids = {
            pid: _create_project_task(self.conn, project_id=pid, title=f"once-{pid}")
            for pid in PROJECTS
        }
        self.conn.close()

        sup1 = _make_supervisor(self.paths, list(PROJECTS), self.config)
        _wait_until(
            self.paths,
            lambda: _done_counts(self.paths, list(PROJECTS))["proj-a"] == 1,
            tick_fn=lambda: _tick_and_drain(sup1),
        )

        conn = connect(self.paths.database)
        try:
            snapshots = {
                pid: {
                    "state": get_task(conn, task_ids[pid])["state"],
                    "running_transitions": _transition_count(conn, task_ids[pid], "running"),
                    "attempts": len(list_attempts(conn, task_ids[pid])),
                }
                for pid in PROJECTS
            }
        finally:
            conn.close()

        sup2 = _make_supervisor(self.paths, list(PROJECTS), self.config)
        _wait_until(
            self.paths,
            lambda: sum(_done_counts(self.paths, list(PROJECTS)).values()) == len(PROJECTS),
            tick_fn=lambda: _tick_and_drain(sup2),
        )

        conn = connect(self.paths.database)
        try:
            for pid in PROJECTS:
                task = get_task(conn, task_ids[pid])
                self.assertEqual(task["state"], "done")
                running = _transition_count(conn, task_ids[pid], "running")
                expected_running = snapshots[pid]["running_transitions"]
                if snapshots[pid]["state"] != "done":
                    expected_running += 1
                self.assertEqual(running, expected_running, f"{pid} executed more than once")
                if snapshots[pid]["state"] == "done":
                    self.assertEqual(
                        len(list_attempts(conn, task_ids[pid])),
                        snapshots[pid]["attempts"],
                    )
        finally:
            conn.close()


class GateFairSchedulingTests(Phase2GateHarness):
    def test_gate_fifty_ticks_drive_real_execution_across_three_projects(self) -> None:
        slow_config = _gate_config(self.repos, slow_seconds=0.1)
        for pid in PROJECTS:
            for i in range(8):
                _create_project_task(self.conn, project_id=pid, title=f"fair-{pid}-{i}")
        self.conn.close()

        sup = _make_supervisor(self.paths, list(PROJECTS), slow_config)
        tick_invocations = 60
        for _ in range(tick_invocations):
            sup.tick()
            _drain_workers(sup, timeout=1.0)

        self.assertGreaterEqual(tick_invocations, MIN_TICKS)

        schedule = _schedule_log(self.paths)
        self.assertGreater(len(schedule), 0, "scheduler never scheduled runnable projects")
        self.assertGreater(
            _count_cycle_complete_with_work(self.paths),
            0,
            "ticks did not drive real task execution",
        )

        max_waits = _max_wait_behind_other_runnable(schedule)
        for pid, waited in max_waits.items():
            self.assertLessEqual(waited, MAX_WAIT_BEHIND_OTHER_RUNNABLE)

        self.assertEqual(sum(_done_counts(self.paths, list(PROJECTS)).values()), 24)


class GateCliPathTests(TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(SRC)
        self.env["COORDINATOR_HOME"] = str(self.home)
        self.paths = RuntimePaths(self.home / "config", self.home / "data", self.home / "state")
        self.paths.create()
        self.repos = _setup_project_repos(self.home)
        _write_gate_config_files(self.home, self.repos)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_gate_cli_supervisor_uses_coordinator_home_config_and_executes_tasks(self) -> None:
        conn = connect(self.paths.database)
        init_db(conn)
        task_id = _create_project_task(conn, project_id="proj-a", title="cli-a")
        conn.close()

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "local_cli_coordinator",
                "supervisor",
                "start",
                "--foreground",
            ],
            cwd=ROOT,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            deadline = time.time() + 8.0
            while time.time() < deadline:
                if proc.poll() is not None:
                    stdout = proc.stdout.read() if proc.stdout else ""
                    stderr = proc.stderr.read() if proc.stderr else ""
                    self.fail(
                        "CLI supervisor exited before executing task; "
                        f"COORDINATOR_HOME config was not loaded\nstdout={stdout}\nstderr={stderr}"
                    )
                conn = connect(self.paths.database)
                try:
                    if get_task(conn, task_id)["state"] == "done":
                        break
                finally:
                    conn.close()
                time.sleep(0.2)
            else:
                self.fail("CLI supervisor never completed the ready task")

            conn = connect(self.paths.database)
            try:
                _assert_task_completed_via_engine(self, conn, task_id, bare_repo=self.repos["proj-a"])
            finally:
                conn.close()

            subprocess.run(
                [sys.executable, "-m", "local_cli_coordinator", "supervisor", "stop"],
                cwd=ROOT,
                env=self.env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            proc.wait(timeout=10.0)
        finally:
            if proc.poll() is None:
                proc.kill()
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()

    def test_gate_cli_tick_loop_logs_exceptions_instead_of_silent_pass(self) -> None:
        from local_cli_coordinator import cli as cli_module

        source = inspect.getsource(cli_module._cmd_supervisor_start)
        tick_loop = source.split("def tick_loop", 1)[1].split("tick_thread", 1)[0]
        self.assertNotRegex(
            tick_loop,
            r"except Exception:\s*\n\s*pass",
            "CLI supervisor tick loop swallows exceptions silently",
        )
        self.assertIn("log.exception", tick_loop)


class GateRunnablePolicyTests(Phase2GateHarness):
    def test_gate_projects_without_ready_tasks_are_not_scheduled(self) -> None:
        task_id = _create_project_task(self.conn, project_id="proj-a", title="finished")
        transition_task(self.conn, task_id, "done", "accepted")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a"], self.config)
        before = len(_schedule_log(self.paths))
        for _ in range(10):
            _tick_and_drain(sup)
        after = len(_schedule_log(self.paths))
        self.assertEqual(after, before)


class GateProjectIsolationTests(Phase2GateHarness):
    def test_gate_three_projects_tasks_and_events_strictly_isolated(self) -> None:
        task_ids = {
            pid: _create_project_task(self.conn, project_id=pid, title=f"iso-{pid}")
            for pid in PROJECTS
        }
        self.conn.close()

        sup = _make_supervisor(self.paths, list(PROJECTS), self.config)
        _wait_until(
            self.paths,
            lambda: sum(_done_counts(self.paths, list(PROJECTS)).values()) == len(PROJECTS),
            tick_fn=lambda: _tick_and_drain(sup),
        )

        conn = connect(self.paths.database)
        try:
            for pid in PROJECTS:
                tasks = project_list_tasks(conn, project_id=pid)
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]["id"], task_ids[pid])
                for event in project_list_events(conn, project_id=pid):
                    self.assertEqual(event["project_id"], pid)
                    self.assertEqual(event["task_id"], task_ids[pid])
                _assert_task_completed_via_engine(
                    self, conn, task_ids[pid], bare_repo=self.repos[pid],
                )
        finally:
            conn.close()


class GateSqlAuditTests(TestCase):
    def test_gate_phase2_sql_queries_include_project_id(self) -> None:
        violations: list[str] = []
        for path in (
            SRC / "local_cli_coordinator" / "supervisor.py",
            SRC / "local_cli_coordinator" / "supervisor_events.py",
            SRC / "local_cli_coordinator" / "project_runtime.py",
        ):
            for sql in _extract_sql_literals(path):
                violations.extend(_audit_sql(path.name, sql))

        db_path = SRC / "local_cli_coordinator" / "db.py"
        for sql in _extract_sql_literals(db_path, prefix="project_"):
            violations.extend(_audit_sql(db_path.name, sql))
        for sql in _extract_sql_literals(db_path, prefix="claim_project"):
            violations.extend(_audit_sql(db_path.name, sql))

        self.assertEqual(violations, [], "\n".join(violations))


class GateVerificationFailureTests(Phase2GateHarness):
    def test_gate_agent_success_with_verification_failure_is_not_done(self) -> None:
        failing_config = _gate_config(
            self.repos,
            verify_commands=[_failing_verify_command()],
        )
        task_id = _create_project_task(self.conn, project_id="proj-a", title="verify-fail")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a"], failing_config)
        with _no_resource_warnings():
            for _ in range(20):
                _tick_and_drain(sup)
                conn = connect(self.paths.database)
                try:
                    state = get_task(conn, task_id)["state"]
                finally:
                    conn.close()
                if state != "ready":
                    break

        conn = connect(self.paths.database)
        try:
            task = get_task(conn, task_id)
            self.assertNotEqual(
                task["state"],
                "done",
                "verification failure must not mark task done",
            )
            self.assertIn(task["state"], {"failed", "verifying", "running"})
            self.assertTrue(task["worktree_path"], "engine must still create an isolated worktree")
        finally:
            conn.close()


class GateResourceWarningTests(Phase2GateHarness):
    def test_gate_burst_ticks_emit_no_resource_warnings(self) -> None:
        for pid in PROJECTS:
            _create_project_task(self.conn, project_id=pid, title=f"warn-{pid}")
        self.conn.close()

        sup = _make_supervisor(self.paths, list(PROJECTS), self.config)
        with _no_resource_warnings():
            for _ in range(30):
                sup.tick()
                time.sleep(0.01)
            _drain_workers(sup)


class GateShutdownSafetyTests(Phase2GateHarness):
    def test_gate_shutdown_waits_workers_before_teardown(self) -> None:
        slow_config = _gate_config(self.repos, slow_seconds=2.5)
        _create_project_task(self.conn, project_id="proj-a", title="slow-shutdown")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a"], slow_config)
        with _no_resource_warnings():
            sup.tick()
            time.sleep(0.15)
            sup.request_shutdown()
            sup.join_workers(timeout=10.0)
            gc.collect()


class GatePerProjectConcurrencyTests(Phase2GateHarness):
    def test_gate_same_project_respects_max_per_project_limit(self) -> None:
        slow_config = _gate_config(self.repos, slow_seconds=1.0)
        _create_project_task(self.conn, project_id="proj-a", title="concurrent-a1")
        _create_project_task(self.conn, project_id="proj-a", title="concurrent-a2")
        self.conn.close()

        capacity = SharedCapacity(max_global_running=4, max_per_project=1)
        sup = _make_supervisor(
            self.paths,
            ["proj-a"],
            slow_config,
            capacity=capacity,
        )

        max_running = 0
        stop = threading.Event()

        def burst_ticks() -> None:
            while not stop.is_set():
                with _no_resource_warnings():
                    sup.tick()
                time.sleep(0.02)

        thread = threading.Thread(target=burst_ticks, daemon=True)
        thread.start()

        deadline = time.time() + 20.0
        try:
            while time.time() < deadline:
                conn = connect(self.paths.database)
                try:
                    running = _running_task_count(conn, project_id="proj-a")
                    max_running = max(max_running, running)
                    counts = project_task_counts(conn, project_id="proj-a")
                    if counts.get("done", 0) == 2:
                        break
                finally:
                    conn.close()
                time.sleep(0.03)
            else:
                self.fail("both proj-a tasks did not finish")
        finally:
            stop.set()
            thread.join(timeout=2.0)
            _drain_workers(sup)

        self.assertLessEqual(
            max_running,
            1,
            f"same project exceeded max_per_project=1 (observed {max_running} running)",
        )


class GateProjectStopSemanticsTests(Phase2GateHarness):
    def _start_server(self, sup: MultiProjectSupervisor) -> SupervisorServer:
        def handler(request: RequestEnvelope) -> ResponseEnvelope:
            if request.method == "system.ping":
                return ResponseEnvelope(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=request.request_id,
                    ok=True,
                    result={"pong": True},
                    error=None,
                )
            conn = connect(self.paths.database)
            try:
                init_db(conn)
                return sup._methods.handle(conn, request)  # noqa: SLF001
            finally:
                conn.close()

        server = SupervisorServer(self.paths, handler=handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                send_request(
                    self.paths.socket,
                    RequestEnvelope(
                        protocol_version=PROTOCOL_VERSION,
                        request_id="ping",
                        project_id=None,
                        method="system.ping",
                        params={},
                    ),
                )
                return server
            except Exception:
                time.sleep(0.02)
        self.fail("server not ready")

    def test_gate_project_stop_does_not_resume_paused_project(self) -> None:
        task_a = _create_project_task(self.conn, project_id="proj-a", title="stop-a")
        task_b = _create_project_task(self.conn, project_id="proj-b", title="stop-b")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a", "proj-b"], self.config)
        server = self._start_server(sup)
        try:
            pause = send_request(
                self.paths.socket,
                _project_request("project.pause", "proj-a", "pause-req"),
            )
            self.assertTrue(pause.ok, pause.error)

            stop = send_request(
                self.paths.socket,
                _project_request("project.stop", "proj-a", "stop-req"),
            )
            self.assertTrue(stop.ok, stop.error)

            with _no_resource_warnings():
                for _ in range(16):
                    _tick_and_drain(sup)

            conn = connect(self.paths.database)
            try:
                self.assertEqual(get_task(conn, task_a)["state"], "ready")
                self.assertEqual(get_task(conn, task_b)["state"], "done")
                self.assertFalse((self.repos["proj-a"] / MARKER).exists())
                _assert_task_completed_via_engine(self, conn, task_b, bare_repo=self.repos["proj-b"])
            finally:
                conn.close()
        finally:
            server.request_shutdown()


class GateLeaseCleanupTests(Phase2GateHarness):
    def test_gate_active_db_leases_zero_after_task_done(self) -> None:
        task_id = _create_project_task(self.conn, project_id="proj-a", title="lease-clean")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a"], self.config)
        with _no_resource_warnings():
            _tick_and_drain(sup)

        conn = connect(self.paths.database)
        try:
            self.assertEqual(get_task(conn, task_id)["state"], "done")
        finally:
            conn.close()

        self.assertEqual(
            _active_db_leases(self.paths),
            [],
            f"task leases leaked after completion: {_active_db_leases(self.paths)}",
        )


class GateMultiClientSubscriptionTests(Phase2GateHarness):
    def test_gate_three_clients_disconnect_out_of_order_leave_no_subscribers(self) -> None:
        _create_project_task(self.conn, project_id="proj-a", title="multi-sub")
        self.conn.close()

        broker = EventBroker()
        methods = SupervisorMethods(broker=broker)
        sup = _make_supervisor(self.paths, ["proj-a"], self.config, broker=broker, methods=methods)

        def handler(request: RequestEnvelope) -> ResponseEnvelope:
            conn = connect(self.paths.database)
            try:
                init_db(conn)
                return methods.handle(conn, request)
            finally:
                conn.close()

        server = SupervisorServer(self.paths, handler=handler, methods=methods)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(0.1)

        subscribe_payload = (
            '{"type":"request","protocol_version":1,'
            '"request_id":"sub-%s","project_id":"proj-a",'
            '"method":"events.subscribe","params":{"after":0}}\n'
        )

        clients: list[socket.socket] = []
        try:
            for i in range(3):
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(str(self.paths.socket))
                client.sendall((subscribe_payload % i).encode("utf-8"))
                client.settimeout(3.0)
                first = supervisor_server_module._recv_line(client)
                self.assertTrue(first, f"missing subscribe response for client {i}")
                clients.append(client)

            self.assertEqual(len(broker._subscribers), 3)  # noqa: SLF001

            for idx in (0, 2, 1):
                clients[idx].close()
                time.sleep(0.1)
        finally:
            for client in clients:
                try:
                    client.close()
                except OSError:
                    pass

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if len(broker._subscribers) == 0 and len(methods._live_queues) == 0:  # noqa: SLF001
                break
            time.sleep(0.05)
        else:
            self.fail(
                "subscriptions leaked after three out-of-order disconnects: "
                f"subscribers={len(broker._subscribers)}, "  # noqa: SLF001
                f"live_queues={len(methods._live_queues)}"  # noqa: SLF001
            )

        server.request_shutdown()


class GateAgentConcurrencyTests(Phase2GateHarness):
    def test_gate_worker_max_concurrency_limits_parallel_leases(self) -> None:
        slow_config = _gate_config(self.repos, slow_seconds=1.0, max_global_concurrency=1)
        _create_project_task(self.conn, project_id="proj-a", title="agent-conc-1")
        _create_project_task(self.conn, project_id="proj-a", title="agent-conc-2")
        self.conn.close()

        sup = _make_supervisor(
            self.paths,
            ["proj-a"],
            slow_config,
            capacity=SharedCapacity(max_global_running=4, max_per_project=2),
        )

        max_worker_leases = 0
        stop = threading.Event()

        def burst_ticks() -> None:
            while not stop.is_set():
                with _no_resource_warnings():
                    sup.tick()
                time.sleep(0.02)

        thread = threading.Thread(target=burst_ticks, daemon=True)
        thread.start()

        deadline = time.time() + 20.0
        try:
            while time.time() < deadline:
                conn = connect(self.paths.database)
                try:
                    row = conn.execute(
                        "select count(*) as cnt from task_leases "
                        "where agent_id = ? and released_at is null",
                        ("worker",),
                    ).fetchone()
                    max_worker_leases = max(max_worker_leases, int(row["cnt"]))
                    counts = project_task_counts(conn, project_id="proj-a")
                    if counts.get("done", 0) == 2:
                        break
                finally:
                    conn.close()
                time.sleep(0.03)
            else:
                self.fail("both proj-a tasks did not finish under agent concurrency gate")
        finally:
            stop.set()
            thread.join(timeout=2.0)
            _drain_workers(sup)

        self.assertLessEqual(
            max_worker_leases,
            1,
            f"worker max_concurrency=1 exceeded (observed {max_worker_leases} active leases)",
        )


class GateEventBrokerThreadSafetyTests(Phase2GateHarness):
    def test_gate_concurrent_publish_never_collides_on_cursor(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        broker = EventBroker()
        thread_count = 12
        barrier = threading.Barrier(thread_count)
        errors: list[Exception] = []

        def publish_once(i: int) -> int:
            conn = connect(self.paths.database)
            init_db(conn)
            try:
                barrier.wait(timeout=5.0)
                return broker.publish(conn, "proj-a", f"evt-{i}", {"i": i})
            except Exception as exc:
                errors.append(exc)
                raise
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=thread_count) as pool:
            cursors = list(pool.map(publish_once, range(thread_count)))

        self.assertEqual(errors, [], f"concurrent publish errors: {errors}")
        self.assertEqual(len(set(cursors)), thread_count)

        conn = connect(self.paths.database)
        try:
            init_db(conn)
            count = conn.execute(
                "select count(*) as cnt from supervisor_events where project_id = ?",
                ("proj-a",),
            ).fetchone()["cnt"]
            self.assertEqual(count, thread_count)
        finally:
            conn.close()

    def test_gate_publish_while_unsubscribing_never_mutates_subscriber_dict(self) -> None:
        broker = EventBroker()
        tokens = [broker.subscribe("proj-a", lambda e: None) for _ in range(3)]
        stop = threading.Event()
        errors: list[Exception] = []

        def churn_subscriptions() -> None:
            try:
                for _ in range(80):
                    if stop.is_set():
                        break
                    for token in tokens:
                        broker.unsubscribe(token)
                    for i in range(len(tokens)):
                        tokens[i] = broker.subscribe("proj-a", lambda e: None)
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=churn_subscriptions, daemon=True)
        thread.start()
        try:
            conn = connect(self.paths.database)
            init_db(conn)
            try:
                for i in range(40):
                    broker.publish(conn, "proj-a", f"live-{i}", {})
            finally:
                conn.close()
        finally:
            stop.set()
            thread.join(timeout=2.0)

        self.assertEqual(errors, [])


class GateTickLeaseCleanupTests(Phase2GateHarness):
    def test_gate_tick_releases_lease_when_publish_fails(self) -> None:
        from unittest.mock import patch

        task_id = _create_project_task(self.conn, project_id="proj-a", title="publish-fail")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a"], self.config)

        def boom(*_args, **_kwargs):
            raise RuntimeError("injected event failure")

        with patch.object(sup._broker, "publish", side_effect=boom):  # noqa: SLF001
            with self.assertRaises(RuntimeError):
                sup.tick()

        self.assertEqual(_active_db_leases(self.paths), [])

    def test_gate_tick_releases_lease_when_submit_fails(self) -> None:
        from unittest.mock import patch

        task_id = _create_project_task(self.conn, project_id="proj-a", title="submit-fail")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a"], self.config)

        def boom(*_args, **_kwargs):
            raise RuntimeError("injected submit failure")

        with patch.object(sup._executor, "submit", side_effect=boom):  # noqa: SLF001
            with self.assertRaises(RuntimeError):
                sup.tick()

        self.assertEqual(_active_db_leases(self.paths), [])


class GateAtomicClaimTests(Phase2GateHarness):
    def test_gate_atomic_claim_binds_matching_task_and_agent(self) -> None:
        from local_cli_coordinator.db import claim_project_ready_task

        code_config = CoordinatorConfig(
            agents={
                "code": AgentConfig(
                    id="code",
                    command=_worker_command(),
                    capabilities=["code"],
                    max_concurrency=2,
                    role="worker",
                ),
                "docs": AgentConfig(
                    id="docs",
                    command=_worker_command(),
                    capabilities=["docs"],
                    max_concurrency=2,
                    role="worker",
                ),
            },
            repos=self.config.repos,
            policy=self.config.policy,
            daemon_policy=self.config.daemon_policy,
        )

        docs_task = _create_project_task(
            self.conn,
            project_id="proj-a",
            title="docs-first",
        )
        self.conn.execute(
            "update tasks set capabilities = ? where id = ?",
            ("docs", docs_task),
        )
        code_task = _create_project_task(
            self.conn,
            project_id="proj-a",
            title="code-second",
        )
        self.conn.commit()

        claimed, agent_id = claim_project_ready_task(
            self.conn, "proj-a", code_config, preferred_agent_id="code"
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(agent_id, "code")
        self.assertEqual(claimed["id"], code_task)
        self.assertEqual(claimed["capabilities"], "code")


class GateMultiAgentSelectionTests(Phase2GateHarness):
    def test_gate_selects_second_agent_when_first_is_at_capacity(self) -> None:
        from local_cli_coordinator.project_runtime import select_available_agent
        from local_cli_coordinator.db import acquire_task_lease

        multi_config = CoordinatorConfig(
            agents={
                "worker-a": AgentConfig(
                    id="worker-a",
                    command=_worker_command(),
                    capabilities=["code"],
                    max_concurrency=1,
                    role="worker",
                ),
                "worker-b": AgentConfig(
                    id="worker-b",
                    command=_worker_command(),
                    capabilities=["code"],
                    max_concurrency=1,
                    role="worker",
                ),
            },
            repos=self.config.repos,
            policy=self.config.policy,
            daemon_policy=self.config.daemon_policy,
        )

        first_id = _create_project_task(self.conn, project_id="proj-a", title="multi-agent-1")
        _create_project_task(self.conn, project_id="proj-a", title="multi-agent-2")
        acquire_task_lease(self.conn, first_id, "worker-a")
        agent, _ = select_available_agent(self.conn, multi_config, "proj-a")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.id, "worker-b")


class GateShutdownTimeoutTests(Phase2GateHarness):
    def test_gate_join_workers_timeout_returns_false_without_faking_success(self) -> None:
        slow_config = _gate_config(self.repos, slow_seconds=5.0)
        _create_project_task(self.conn, project_id="proj-a", title="shutdown-timeout")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a"], slow_config)
        try:
            with _no_resource_warnings():
                sup.tick()
                time.sleep(0.15)
                all_done = sup.join_workers(timeout=0.2)

            self.assertFalse(all_done, "join_workers must report timeout instead of pretending success")
            self.assertGreater(
                len(sup._active_futures),  # noqa: SLF001
                0,
                "timed-out workers must remain tracked in _active_futures",
            )
        finally:
            sup.join_workers(timeout=10.0)

    def test_gate_cli_shutdown_checks_join_workers_result(self) -> None:
        from local_cli_coordinator import cli as cli_module

        source = inspect.getsource(cli_module._cmd_supervisor_start)
        finally_block = source.split("finally:", 1)[1]
        self.assertIn("join_workers", finally_block)
        self.assertRegex(
            finally_block,
            r"if not sup\.join_workers\([^)]*shutdown=True",
            "CLI must treat join_workers failure as shutdown error",
        )
        self.assertIn("return 1", finally_block)


class GateDynamicProjectTests(Phase2GateHarness):
    def test_gate_new_project_task_scheduled_after_runtime_refresh(self) -> None:
        initial = _create_project_task(self.conn, project_id="proj-a", title="initial-a")
        self.conn.close()

        sup = _make_supervisor(self.paths, ["proj-a"], self.config)
        with _no_resource_warnings():
            _tick_and_drain(sup)

        conn = connect(self.paths.database)
        try:
            self.assertEqual(get_task(conn, initial)["state"], "done")
            late_id = _create_project_task(conn, project_id="proj-c", title="late-c")
        finally:
            conn.close()

        with _no_resource_warnings():
            _wait_until(
                self.paths,
                lambda: _done_counts(self.paths, ["proj-c"]).get("proj-c", 0) == 1,
                tick_fn=lambda: _tick_and_drain(sup),
                timeout=30.0,
            )

        conn = connect(self.paths.database)
        try:
            _assert_task_completed_via_engine(
                self, conn, late_id, bare_repo=self.repos["proj-c"],
            )
        finally:
            conn.close()


class GateSubscriptionCleanupTests(Phase2GateHarness):
    def test_gate_client_disconnect_clears_broker_subscriptions(self) -> None:
        _create_project_task(self.conn, project_id="proj-a", title="sub-cleanup")
        self.conn.close()

        broker = EventBroker()
        methods = SupervisorMethods(broker=broker)
        sup = _make_supervisor(self.paths, ["proj-a"], self.config, broker=broker, methods=methods)

        def handler(request: RequestEnvelope) -> ResponseEnvelope:
            conn = connect(self.paths.database)
            try:
                init_db(conn)
                return methods.handle(conn, request)
            finally:
                conn.close()

        server = SupervisorServer(self.paths, handler=handler, methods=methods)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(0.1)

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(self.paths.socket))
        try:
            subscribe = (
                '{"type":"request","protocol_version":1,'
                '"request_id":"sub-1","project_id":"proj-a",'
                '"method":"events.subscribe","params":{"after":0}}\n'
            )
            client.sendall(subscribe.encode("utf-8"))
            client.settimeout(3.0)
            first = supervisor_server_module._recv_line(client)
            self.assertTrue(first, "missing subscribe response")
            self.assertGreater(len(broker._subscribers), 0)  # noqa: SLF001
        finally:
            client.close()

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if len(broker._subscribers) == 0 and len(methods._live_queues) == 0:  # noqa: SLF001
                break
            time.sleep(0.05)
        else:
            self.fail(
                "broker subscriptions leaked after client disconnect: "
                f"subscribers={len(broker._subscribers)}, "  # noqa: SLF001
                f"live_queues={len(methods._live_queues)}"  # noqa: SLF001
            )

        server.request_shutdown()


class GateXdgConfigTests(TestCase):
    def setUp(self) -> None:
        from local_cli_coordinator.runtime_paths import resolve_runtime_paths

        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(SRC)
        self.env["HOME"] = str(self.home)
        self.env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        self.env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        self.env["XDG_STATE_HOME"] = str(self.home / ".local" / "state")
        self.env.pop("COORDINATOR_HOME", None)

        with _apply_env(self.env):
            self.paths = resolve_runtime_paths()
        self.paths.create()
        self.repos = _setup_project_repos(self.home)
        _write_xdg_gate_config_files(self.paths.config_dir, self.repos)

    def tearDown(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            gc.collect()
            self._tmpdir.cleanup()

    def test_gate_cli_supervisor_uses_default_xdg_config_and_executes_tasks(self) -> None:
        conn = connect(self.paths.database)
        init_db(conn)
        task_id = _create_project_task(conn, project_id="proj-a", title="xdg-a")
        conn.close()

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "local_cli_coordinator",
                "supervisor",
                "start",
                "--foreground",
            ],
            cwd=ROOT,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            deadline = time.time() + 12.0
            while time.time() < deadline:
                if proc.poll() is not None:
                    stdout = proc.stdout.read() if proc.stdout else ""
                    stderr = proc.stderr.read() if proc.stderr else ""
                    self.fail(
                        "CLI supervisor exited before executing task; "
                        f"default XDG config was not loaded\nstdout={stdout}\nstderr={stderr}"
                    )
                conn = connect(self.paths.database)
                try:
                    if get_task(conn, task_id)["state"] == "done":
                        break
                finally:
                    conn.close()
                time.sleep(0.2)
            else:
                self.fail("CLI supervisor never completed the ready task via XDG config")

            conn = connect(self.paths.database)
            try:
                _assert_task_completed_via_engine(self, conn, task_id, bare_repo=self.repos["proj-a"])
            finally:
                conn.close()

            subprocess.run(
                [sys.executable, "-m", "local_cli_coordinator", "supervisor", "stop"],
                cwd=ROOT,
                env=self.env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            proc.wait(timeout=10.0)
        finally:
            if proc.poll() is None:
                proc.kill()
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()


@contextmanager
def _apply_env(env: dict[str, str]):
    old = os.environ.copy()
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


class GateRound3SourceAuditTests(TestCase):
    def test_gate_project_runtime_delegates_to_engine_process_task(self) -> None:
        source = (SRC / "local_cli_coordinator" / "project_runtime.py").read_text(encoding="utf-8")
        self.assertIn("_process_task", source)
        self.assertNotIn("subprocess.run", source)
        self.assertNotIn("transition_task(conn, task_id, \"done\"", source)

    def test_gate_supervisor_tick_acquires_capacity_with_closed_connections(self) -> None:
        source = (SRC / "local_cli_coordinator" / "supervisor.py").read_text(encoding="utf-8")
        self.assertIn("try_acquire", source, "tick must enforce SharedCapacity")
        self.assertNotIn("tick-{project_id}", source, "capacity key must not be fixed per project")
        tick_body = source.split("def tick", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("_get_conn", tick_body)
        acquire_pos = tick_body.find("try_acquire")
        conn_pos = tick_body.find("_get_conn")
        self.assertGreater(conn_pos, -1)
        self.assertGreater(acquire_pos, -1)
        with_block = "with self._get_conn() as conn:"
        self.assertIn(with_block, tick_body, "tick must acquire capacity inside _get_conn context")
        self.assertGreater(
            tick_body.find("try_acquire", tick_body.find(with_block)),
            tick_body.find(with_block),
            "try_acquire must run inside the context-managed connection block",
        )

    def test_gate_project_stop_marks_stopped_instead_of_only_resuming(self) -> None:
        source = inspect.getsource(
            __import__(
                "local_cli_coordinator.supervisor_methods",
                fromlist=["SupervisorMethods"],
            ).SupervisorMethods._handle_project_stop
        )
        self.assertIn(
            "_stopped.add",
            source,
            "project.stop must add project to stopped set",
        )
        self.assertFalse(
            "discard" in source and "_stopped.add" not in source,
            "project.stop must not only discard pause (that resumes scheduling)",
        )

    def test_gate_supervisor_start_joins_workers_on_shutdown(self) -> None:
        from local_cli_coordinator import cli as cli_module

        source = inspect.getsource(cli_module._cmd_supervisor_start)
        self.assertIn("join_workers", source)
        finally_block = source.split("finally:", 1)[1]
        self.assertIn("join_workers", finally_block)
        self.assertIn("shutdown=True", finally_block)

    def test_gate_supervisor_uses_monotonic_capacity_keys(self) -> None:
        source = (SRC / "local_cli_coordinator" / "supervisor.py").read_text(encoding="utf-8")
        tick_body = source.split("def tick", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("_worker_counter", tick_body)
        self.assertNotIn("id(decision)", tick_body)

    def test_gate_supervisor_tick_aborts_pre_claimed_lease_before_worker_handoff(self) -> None:
        source = (SRC / "local_cli_coordinator" / "supervisor.py").read_text(encoding="utf-8")
        tick_body = source.split("def tick", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("submitted", tick_body)
        self.assertIn("_abort_tick_claim", tick_body)
        self.assertIn("if not submitted and claimed_task_id is not None:", tick_body)
        run_body = source.split("def _run_project_cycle", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("finally:", run_body)
        self.assertIn("release_task_lease(conn, task_id)", run_body)

    def test_gate_project_runtime_releases_task_lease_in_finally(self) -> None:
        source = (SRC / "local_cli_coordinator" / "project_runtime.py").read_text(encoding="utf-8")
        self.assertIn("release_task_lease", source)
        run_body = source.split("def run_project_cycle", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("finally:", run_body)
        self.assertIn("release_task_lease(conn, task_id)", run_body)

    def test_gate_event_broker_unsubscribe_uses_token_map(self) -> None:
        source = (SRC / "local_cli_coordinator" / "supervisor_events.py").read_text(encoding="utf-8")
        self.assertIn("_subscribers: dict[int, _Subscription]", source)
        self.assertIn("self._subscribers.pop(token, None)", source)
        self.assertIn("begin immediate", source)
        self.assertIn("list(self._subscribers.values())", source)
        self.assertNotRegex(
            source,
            r"self\._subscribers\.pop\(token\)",
            "unsubscribe must not treat token as list index",
        )

    def test_gate_process_task_honors_assigned_agent_id(self) -> None:
        source = (SRC / "local_cli_coordinator" / "engine.py").read_text(encoding="utf-8")
        body = source.split("def _process_task", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if agent_id is not None:", body)
        self.assertIn("config.agents.get(agent_id)", body)

    def test_gate_atomic_claim_lives_in_db_begin_immediate(self) -> None:
        source = (SRC / "local_cli_coordinator" / "db.py").read_text(encoding="utf-8")
        body = source.split("def claim_project_ready_task", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("begin immediate", body)
        self.assertIn("_agents_for_task", body)
        self.assertIn("_try_acquire_task_lease", body)

    def test_gate_project_runtime_delegates_atomic_claim(self) -> None:
        source = (SRC / "local_cli_coordinator" / "project_runtime.py").read_text(encoding="utf-8")
        self.assertIn("claim_project_ready_task", source)
        self.assertNotIn("claim_project_task", source)

    def test_gate_cli_load_config_supports_xdg_layout(self) -> None:
        source = inspect.getsource(
            __import__("local_cli_coordinator.cli", fromlist=["cli"])._cmd_supervisor_start
        )
        self.assertNotIn(
            "load_config(paths.config_dir.parent)",
            source,
            "XDG config_dir already points at coordinator config root",
        )


class GateResourceWarningHarnessTests(TestCase):
    def test_gate_leaked_coordinator_connection_fails_under_global_warning_policy(self) -> None:
        import tempfile
        from pathlib import Path
        import tests

        with tempfile.TemporaryDirectory() as tmp:
            leaked = connect(Path(tmp) / "leak.db")
            with self.assertRaises(ResourceWarning):
                tests._audit_open_connections()
            leaked.close()

    def test_gate_test_harness_tracks_sqlite_close_and_runs_gc_collect(self) -> None:
        import tests

        source = inspect.getsource(tests._run_with_resource_guard)
        self.assertIn("gc.collect()", source)
        self.assertIn("_audit_open_connections", source)
        self.assertIn("_TrackedConnection", inspect.getsource(tests._install_tracked_connect))

    def test_gate_background_thread_connection_leak_fails_audit(self) -> None:
        import tempfile
        from pathlib import Path
        import tests

        ready = threading.Event()
        close_signal = threading.Event()
        worker_thread_id: list[int] = []

        def worker() -> None:
            conn = connect(Path(tempfile.mkdtemp()) / "bg.db")
            worker_thread_id.append(threading.get_ident())
            ready.set()
            close_signal.wait(timeout=2.0)
            conn.close()

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(ready.wait(timeout=2.0))

        with self.assertRaises(ResourceWarning) as ctx:
            tests._audit_open_connections()
        self.assertIn("background-thread leak", str(ctx.exception))
        self.assertNotEqual(worker_thread_id[0], threading.main_thread().ident)

        close_signal.set()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())


class GateFileHandleLeakTests(TestCase):
    def test_gate_send_request_closes_socket_readers(self) -> None:
        send_source = inspect.getsource(send_request)
        serve_source = inspect.getsource(supervisor_server_module.SupervisorServer._serve_client)
        self.assertNotIn(".makefile(", send_source)
        self.assertNotIn('makefile("rb")', serve_source)

    def test_gate_supervisor_cli_integration_closes_subprocess_stderr(self) -> None:
        source = (ROOT / "tests/test_supervisor_cli.py").read_text(encoding="utf-8")
        self.assertIn(
            "stderr.close()",
            source,
            "tests/test_supervisor_cli.py must drain/close stderr PIPE after supervisor subprocess exits",
        )


def _extract_sql_literals(path: Path, *, prefix: str | None = None) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals: list[str] = []

    if prefix is None:
        walk_roots = [tree]
    else:
        walk_roots = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith(prefix)
        ]

    for root in walk_roots:
        for child in ast.walk(root):
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