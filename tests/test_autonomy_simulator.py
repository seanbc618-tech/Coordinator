"""Phase 17: dry-run autonomy simulation."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.autonomy_simulator import run_autonomy_simulation

from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_capacity import SharedCapacity
from local_cli_coordinator.supervisor_scheduler import (
    FairProjectScheduler,
    forecast_project_skip_reason,
    simulate_scheduler_round,
)
from tests.helpers import init_git_repo


def _write_config(config_dir: Path, repo_path: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text(textwrap.dedent("""
        [agents.alpha]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 2
        role = "worker"
    """).strip())
    (config_dir / "repos.toml").write_text(textwrap.dedent(f"""
        [repos.test-repo]
        path = "{repo_path}"
        default_branch = "main"
        allow_push = false
        merge_policy = "no_push"
        review_policy = "tests_only"
        autonomy_enabled = true
    """).strip())
    (config_dir / "policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = false
        require_acceptance_criteria = false
        require_verification_commands = false
        require_handoff_summary = false
        max_files_touched = 20
        max_expected_minutes = 60
        max_attempts = 3
        split_if_touches_multiple_subsystems = false
        split_if_research_and_code_are_mixed = false
        max_tasks_per_day = 24

        [autonomy]
        enabled = true

        [notifications]
        allow_command_sink = false
    """).strip())


class AutonomySimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.repo = Path(self.tmp.name) / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.project_id = register_project(
            self.conn, inspect_project(self.repo), confirmed=True
        )
        self.conn.commit()
        from local_cli_coordinator.config_runtime import load_config_for_paths

        self.config = load_config_for_paths(self.paths)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _snapshot(self) -> dict[str, int]:
        return {
            "tasks": self.conn.execute("select count(*) as c from tasks").fetchone()["c"],
            "leases": self.conn.execute(
                "select count(*) as c from task_leases where released_at is null"
            ).fetchone()["c"],
            "runs": self.conn.execute("select count(*) as c from simulation_runs").fetchone()["c"],
        }

    def test_simulation_does_not_mutate_tasks_or_leases(self) -> None:
        create_task(
            self.conn,
            title="ready task",
            repo="test-repo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=[],
            project_id=self.project_id,
        )
        self.conn.commit()
        before = self._snapshot()
        result = run_autonomy_simulation(
            self.conn,
            config=self.config,
            paths=self.paths,
            scope="global",
            horizon_hours=8.0,
        )
        after = self._snapshot()
        self.assertEqual(before["tasks"], after["tasks"])
        self.assertEqual(before["leases"], after["leases"])
        self.assertEqual(after["runs"], before["runs"] + 1)
        self.assertTrue(result["forecast"])
        self.assertIn("simulation_run_id", result)

    def test_scheduler_forecast_schedules_runnable_project(self) -> None:
        create_task(
            self.conn,
            title="ready",
            repo="test-repo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=[],
            project_id=self.project_id,
        )
        self.conn.commit()
        scheduler = FairProjectScheduler([self.project_id])

        def _runnable(pid: str) -> bool:
            return pid == self.project_id

        outcomes = simulate_scheduler_round(scheduler, _runnable, rounds=1)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].project_id, self.project_id)

    def test_scheduler_forecast_skips_paused_project(self) -> None:
        reason = forecast_project_skip_reason(
            project_id=self.project_id,
            paused_projects={self.project_id},
            has_claimable_task=True,
        )
        self.assertEqual(reason, "project is paused")

    def test_simulation_forecasts_agent_usage(self) -> None:
        create_task(
            self.conn,
            title="ready",
            repo="test-repo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=[],
            project_id=self.project_id,
        )
        self.conn.commit()
        result = run_autonomy_simulation(
            self.conn,
            config=self.config,
            paths=self.paths,
            scope="project",
            project_id=self.project_id,
        )
        usage = result["report"]["expected_agent_usage"]
        self.assertTrue(usage)
        self.assertEqual(usage[0]["agent_id"], "alpha")

    def test_capacity_forecast_pressure(self) -> None:
        capacity = SharedCapacity(max_global_running=1, max_per_project=1)
        capacity.try_acquire(
            self.conn,
            project_id=self.project_id,
            task_id="t1",
            agent_id="alpha",
        )
        forecast = capacity.forecast_pressure(
            project_id=self.project_id,
            additional_tasks=1,
        )
        self.assertIn(forecast["pressure"], {"high", "exhausted"})
        self.assertTrue(forecast["forecast"])


if __name__ == "__main__":
    unittest.main()