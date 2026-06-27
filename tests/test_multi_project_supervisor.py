"""End-to-end tests for multi-project Supervisor."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.config import (
    AgentConfig,
    AutonomyConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, init_db, create_task, project_task_counts
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.supervisor import MultiProjectSupervisor
from local_cli_coordinator.supervisor_scheduler import FairProjectScheduler
from local_cli_coordinator.supervisor_events import EventBroker
from local_cli_coordinator.supervisor_capacity import SharedCapacity
from local_cli_coordinator.supervisor_methods import SupervisorMethods
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import ROOT, SRC, init_git_repo


def _test_config(tmp: Path, *, autonomy_enabled: bool = False) -> CoordinatorConfig:
    repo = tmp / "repo"
    repo.mkdir(exist_ok=True)
    return CoordinatorConfig(
        agents={
            "test": AgentConfig(
                id="test",
                command=f"{sys.executable} -c 'pass'",
                capabilities=["code"],
                max_concurrency=1,
            ),
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=repo,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[],
                review_policy="tests_only",
                autonomy_enabled=autonomy_enabled,
            ),
        },
        autonomy=AutonomyConfig(enabled=autonomy_enabled),
        policy=PolicyConfig(
            require_single_repo=True,
            require_acceptance_criteria=False,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=10,
            max_expected_minutes=30,
            max_attempts=3,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
        ),
        daemon_policy=DaemonPolicyConfig(run_discovery_before_tasks=False),
    )


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

    config = _test_config(tmp)
    scheduler = FairProjectScheduler(projects)
    broker = EventBroker()
    capacity = SharedCapacity(max_global_running=4, max_per_project=2)
    methods = SupervisorMethods(broker=broker)

    return MultiProjectSupervisor(
        paths=paths,
        scheduler=scheduler,
        broker=broker,
        capacity=capacity,
        methods=methods,
        config=config,
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
        sup.tick()
        sup.join_workers(timeout=5.0)

    def test_status_fields(self) -> None:
        sup = _make_supervisor(self.root, ["proj-a"])
        status = sup.status()
        self.assertIn("projects", status)
        self.assertIn("active_tasks", status)

    def test_graceful_shutdown(self) -> None:
        sup = _make_supervisor(self.root, ["proj-a"])
        sup.request_shutdown()
        self.assertTrue(sup.is_shutdown_requested())

    def test_active_autonomous_run_makes_project_runnable_without_ready_task(self) -> None:
        """A project with an active run session must be scheduled even with no ready tasks."""
        from local_cli_coordinator.autonomous_runs import (
            AutonomousRunOptions,
            start_run_session,
        )

        repo = self.root / "repo"
        repo.mkdir(exist_ok=True)
        init_git_repo(repo)
        paths = RuntimePaths(self.root / "config", self.root / "data", self.root / "state")
        paths.create()
        conn = connect(paths.database)
        init_db(conn)
        draft = inspect_project(repo)
        register_project(conn, draft, confirmed=True)
        project_id = conn.execute("select id from projects limit 1").fetchone()["id"]
        goal_id = create_goal(conn, "Autonomous run goal", "test", project_id=project_id)
        transition_goal(conn, goal_id, "active")
        from tests.helpers import insert_terminal_task

        insert_terminal_task(
            conn,
            task_id="task-done-001",
            title="done-task",
            state="done",
            project_id=project_id,
        )
        start_run_session(
            conn,
            project_id=project_id,
            goal_id=goal_id,
            options=AutonomousRunOptions(idle_backoff_seconds=0),
        )
        conn.commit()

        config = _test_config(self.root, autonomy_enabled=True)
        scheduler = FairProjectScheduler([project_id])
        broker = EventBroker()
        capacity = SharedCapacity(max_global_running=4, max_per_project=2)
        methods = SupervisorMethods(broker=broker)
        sup = MultiProjectSupervisor(
            paths=paths,
            scheduler=scheduler,
            broker=broker,
            capacity=capacity,
            methods=methods,
            config=config,
        )

        sup.tick()
        sup.join_workers(timeout=5.0)

        verify_conn = connect(paths.database)
        init_db(verify_conn)
        run_steps = verify_conn.execute(
            "select count(*) from autonomous_run_steps where project_id = ?",
            (project_id,),
        ).fetchone()[0]
        loop_iterations = verify_conn.execute(
            "select count(*) from loop_iterations where project_id = ?",
            (project_id,),
        ).fetchone()[0]
        verify_conn.close()
        conn.close()
        self.assertGreater(run_steps + loop_iterations, 0)


class MultiProjectSupervisorCliTest(TestCase):
    """Subprocess integration tests for the Supervisor."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)
        self._env = os.environ.copy()
        self._env["PYTHONPATH"] = str(SRC)
        self._env["COORDINATOR_HOME"] = str(self.home)
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
        for p in self._processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
            if p.stderr:
                try:
                    p.stderr.read()
                except (OSError, ValueError):
                    pass
                p.stderr.close()
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
                if proc.stderr:
                    proc.stderr.close()
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
