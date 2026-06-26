#!/usr/bin/env python3
"""Deterministic multi-project Supervisor soak harness.

Runs fake workers across multiple projects with fixed seeds, records
scheduling fairness and isolation metrics as JSON, and exits non-zero on
violation.
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (SRC, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from local_cli_coordinator.config import (  # noqa: E402
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import (  # noqa: E402
    connect,
    create_task,
    init_db,
    project_task_counts,
)
from local_cli_coordinator.runtime_paths import RuntimePaths  # noqa: E402
from local_cli_coordinator.supervisor import MultiProjectSupervisor  # noqa: E402
from local_cli_coordinator.supervisor_capacity import SharedCapacity  # noqa: E402
from local_cli_coordinator.supervisor_events import EventBroker  # noqa: E402
from local_cli_coordinator.supervisor_methods import SupervisorMethods  # noqa: E402
from local_cli_coordinator.supervisor_protocol import PROTOCOL_VERSION  # noqa: E402
from local_cli_coordinator.supervisor_scheduler import FairProjectScheduler  # noqa: E402
from local_cli_coordinator.supervisor_server import SupervisorServer  # noqa: E402
from tests.helpers import init_git_repo  # noqa: E402

MARKER = "feature.txt"
MARKER_CONTENT = "done"
MAX_WAIT_BEHIND_OTHER_RUNNABLE = 2


def _worker_command(python: str) -> str:
    body = f"from pathlib import Path; Path('{MARKER}').write_text('{MARKER_CONTENT}')"
    return f'{python} -c "{body}"'


def _verify_command(python: str) -> str:
    return (
        f'{python} -c "from pathlib import Path; '
        f"assert Path('{MARKER}').read_text() == '{MARKER_CONTENT}'\""
    )


def _policy() -> PolicyConfig:
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
        max_tasks_per_run=1,
        max_tasks_per_day=100,
        max_consecutive_failures=3,
    )


def _setup(root: Path, project_count: int, python: str) -> tuple[RuntimePaths, dict[str, Path], CoordinatorConfig, list[str]]:
    paths = RuntimePaths(root / "config", root / "data", root / "state")
    paths.create()

    repos: dict[str, Path] = {}
    project_ids: list[str] = []
    for index in range(project_count):
        name = f"proj-{chr(ord('a') + index)}"
        repo = root / "repos" / name
        init_git_repo(repo)
        repos[name] = repo
        project_ids.append(name)

    config_dir = paths.config_dir
    worker = _worker_command(python).replace('"', '\\"')
    verify = _verify_command(python).replace('"', '\\"')
    config_dir.joinpath("agents.toml").write_text(textwrap.dedent(f"""
        [agents.worker]
        command = "{worker}"
        capabilities = ["code"]
        max_concurrency = 2
        role = "worker"
    """).strip(), encoding="utf-8")

    repo_blocks = []
    repo_configs: dict[str, RepoConfig] = {}
    for name, repo_path in repos.items():
        repo_id = f"demo-{name}"
        repo_blocks.append(textwrap.dedent(f"""
            [repos."{repo_id}"]
            path = "{repo_path}"
            default_branch = "main"
            remote = "origin"
            branch_prefix = "coord/"
            allow_push = false
            merge_policy = "no_push"
            review_policy = "tests_only"
            verify_commands = ["{verify}"]
        """).strip())
        repo_configs[repo_id] = RepoConfig(
            id=repo_id,
            path=repo_path,
            default_branch="main",
            remote="origin",
            branch_prefix="coord/",
            allow_push=False,
            merge_policy="no_push",
            verify_commands=[_verify_command(python)],
            review_policy="tests_only",
        )
    config_dir.joinpath("repos.toml").write_text("\n\n".join(repo_blocks), encoding="utf-8")
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

    config = CoordinatorConfig(
        agents={
            "worker": AgentConfig(
                id="worker",
                command=_worker_command(python),
                capabilities=["code"],
                max_concurrency=2,
                role="worker",
            )
        },
        repos=repo_configs,
        policy=_policy(),
        daemon_policy=DaemonPolicyConfig(run_discovery_before_tasks=False),
    )
    return paths, repos, config, project_ids


def _seed_tasks(paths: RuntimePaths, project_ids: list[str], *, per_project: int) -> None:
    conn = connect(paths.database)
    init_db(conn)
    try:
        for project_id in project_ids:
            for index in range(per_project):
                create_task(
                    conn,
                    title=f"soak-{project_id}-{index}",
                    repo=f"demo-{project_id}",
                    source_path=f"inbox/{project_id}/task-{index}.md",
                    priority="normal",
                    capabilities=["code"],
                    goal=f"Create {MARKER} for {project_id}",
                    acceptance_criteria=[f"{MARKER} contains {MARKER_CONTENT}"],
                    verification_commands=[],
                    project_id=project_id,
                )
    finally:
        conn.close()


def _schedule_log(paths: RuntimePaths) -> list[str]:
    conn = connect(paths.database)
    try:
        init_db(conn)
        rows = conn.execute(
            "select project_id from supervisor_events "
            "where event_type = 'tick_scheduled' order by id"
        ).fetchall()
        return [row["project_id"] for row in rows]
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


def _event_cursors(paths: RuntimePaths, project_ids: list[str]) -> dict[str, list[int]]:
    conn = connect(paths.database)
    try:
        init_db(conn)
        result: dict[str, list[int]] = {}
        for project_id in project_ids:
            rows = conn.execute(
                "select cursor from supervisor_events where project_id = ? order by cursor",
                (project_id,),
            ).fetchall()
            result[project_id] = [int(row["cursor"]) for row in rows]
        return result
    finally:
        conn.close()


def _completed_task_ids(paths: RuntimePaths) -> list[str]:
    conn = connect(paths.database)
    try:
        init_db(conn)
        rows = conn.execute(
            "select payload from supervisor_events where event_type = 'cycle_complete'"
        ).fetchall()
        completed: list[str] = []
        for row in rows:
            payload = json.loads(row["payload"])
            task_id = payload.get("task_id")
            if payload.get("tasks_processed", 0) > 0 and task_id:
                completed.append(str(task_id))
        return completed
    finally:
        conn.close()


def _active_leases(paths: RuntimePaths) -> list[dict[str, str]]:
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


def _drain_workers(sup: MultiProjectSupervisor, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with sup._futures_lock:  # noqa: SLF001
            pending = [future for future in sup._active_futures.values() if not future.done()]  # noqa: SLF001
        if not pending:
            sup.join_workers(timeout=1.0)
            return
        time.sleep(0.02)
    for future in pending:
        future.result(timeout=5)


def _subscribe_once(paths: RuntimePaths, project_id: str) -> None:
    payload = json.dumps({
        "type": "request",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": f"soak-{project_id}",
        "project_id": project_id,
        "method": "events.subscribe",
        "params": {"after": 0},
    }) + "\n"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(paths.socket))
        sock.sendall(payload.encode("utf-8"))
        sock.settimeout(2.0)
        sock.recv(4096)
    finally:
        sock.close()


def run_soak(*, projects: int, ticks: int, seed: int, python: str) -> dict:
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(prefix="coord-soak-") as tmp:
        root = Path(tmp)
        paths, _repos, config, project_ids = _setup(root, projects, python)
        per_project = max(3, ticks // max(projects, 1) // 2)
        _seed_tasks(paths, project_ids, per_project=per_project)

        broker = EventBroker()
        methods = SupervisorMethods(broker=broker)
        sup = MultiProjectSupervisor(
            paths=paths,
            scheduler=FairProjectScheduler(project_ids),
            broker=broker,
            capacity=SharedCapacity(max_global_running=4, max_per_project=2),
            methods=methods,
            config=config,
        )

        def handler(request):
            from local_cli_coordinator.supervisor_protocol import ResponseEnvelope

            if request.method == "system.ping":
                return ResponseEnvelope(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=request.request_id,
                    ok=True,
                    result={"pong": True},
                    error=None,
                )
            conn = connect(paths.database)
            init_db(conn)
            try:
                return methods.handle(conn, request)
            finally:
                conn.close()

        server = SupervisorServer(paths, handler=handler, methods=methods)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        stop_reconnects = threading.Event()
        reconnect_counts: dict[str, int] = {project_id: 0 for project_id in project_ids}
        reconnect_threads: list[threading.Thread] = []
        for project_id in project_ids:
            thread = threading.Thread(
                target=lambda pid=project_id: _reconnect_loop(
                    paths,
                    pid,
                    reconnect_counts,
                    stop_reconnects,
                    rng,
                ),
                daemon=True,
            )
            reconnect_threads.append(thread)
            thread.start()

        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                _subscribe_once(paths, project_ids[0])
                break
            except OSError:
                time.sleep(0.05)

        for _ in range(ticks):
            sup.tick()
            _drain_workers(sup, timeout=2.0)
            time.sleep(0.01)

        stop_reconnects.set()
        for thread in reconnect_threads:
            thread.join(timeout=2.0)

        server.request_shutdown()
        server_thread.join(timeout=5.0)
        sup.request_shutdown()
        sup.join_workers(timeout=30.0, shutdown=True)

        schedule = _schedule_log(paths)
        max_waits = _max_wait_behind_other_runnable(schedule)
        cursors = _event_cursors(paths, project_ids)
        completed = _completed_task_ids(paths)
        duplicates = sorted({
            task_id for task_id in completed
            if completed.count(task_id) > 1
        })
        leases = _active_leases(paths)

        violations: list[str] = []
        if duplicates:
            violations.append(f"duplicate task ids: {duplicates}")
        if leases:
            violations.append(f"active leases at end: {leases}")
        for project_id, values in cursors.items():
            if values != sorted(set(values)):
                violations.append(f"non-monotonic cursors for {project_id}")
            for previous, current in zip(values, values[1:]):
                if current <= previous:
                    violations.append(f"cursor not strictly increasing for {project_id}")
                    break
        for project_id, waited in max_waits.items():
            if waited > MAX_WAIT_BEHIND_OTHER_RUNNABLE:
                violations.append(
                    f"{project_id} waited {waited} turns (> {MAX_WAIT_BEHIND_OTHER_RUNNABLE})"
                )

        done_total = 0
        conn = connect(paths.database)
        try:
            init_db(conn)
            for project_id in project_ids:
                done_total += project_task_counts(conn, project_id=project_id).get("done", 0)
        finally:
            conn.close()
        if done_total == 0:
            violations.append("no tasks completed during soak")

        return {
            "ok": not violations,
            "violations": violations,
            "projects": projects,
            "ticks": ticks,
            "seed": seed,
            "project_scheduling_order": schedule,
            "max_wait": max_waits,
            "max_wait_global": max(max_waits.values()) if max_waits else 0,
            "duplicate_task_ids": duplicates,
            "event_cursors": cursors,
            "reconnect_counts": reconnect_counts,
            "final_leases": leases,
            "tasks_completed": done_total,
        }


def _reconnect_loop(
    paths: RuntimePaths,
    project_id: str,
    counts: dict[str, int],
    stop: threading.Event,
    rng: random.Random,
) -> None:
    while not stop.is_set():
        try:
            _subscribe_once(paths, project_id)
            counts[project_id] += 1
        except OSError:
            pass
        time.sleep(rng.uniform(0.02, 0.08))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic global Supervisor soak")
    parser.add_argument("--projects", type=int, default=3)
    parser.add_argument("--ticks", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    report = run_soak(
        projects=args.projects,
        ticks=args.ticks,
        seed=args.seed,
        python=args.python,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())