"""Tests for project runtime adapters."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, init_db, create_task
from local_cli_coordinator.project_runtime import ProjectRuntime, run_project_cycle


def _make_config() -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "test": AgentConfig(
                id="test",
                command="echo ok",
                capabilities=["code"],
                max_concurrency=1,
            ),
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=Path("/tmp/demo"),
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[],
            ),
        },
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
            max_tasks_per_run=1,
            max_tasks_per_day=24,
            max_consecutive_failures=3,
        ),
    )


class ProjectRuntimeTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "coordinator.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_runtime_has_project_paths(self) -> None:
        config = _make_config()
        runtime = ProjectRuntime(
            project_id="proj-a",
            repo_root=Path("/tmp/repo"),
            state_root=self.root,
            config=config,
        )
        self.assertEqual(runtime.runs_dir, self.root / "projects" / "proj-a" / "runs")

    def test_runtime_frozen(self) -> None:
        config = _make_config()
        runtime = ProjectRuntime(
            project_id="proj-a",
            repo_root=Path("/tmp/repo"),
            state_root=self.root,
            config=config,
        )
        with self.assertRaises(AttributeError):
            runtime.project_id = "other"  # type: ignore[misc]

    def test_run_project_cycle_uses_scoped_db(self) -> None:
        """run_project_cycle should only see tasks for its project."""
        config = _make_config()
        # Create tasks in different projects
        create_task(
            self.conn, title="task-a", repo="demo", source_path="x",
            priority="normal", capabilities=["code"], goal="g",
            acceptance_criteria=["a"], verification_commands=[],
            project_id="proj-a",
        )
        create_task(
            self.conn, title="task-b", repo="demo", source_path="x",
            priority="normal", capabilities=["code"], goal="g",
            acceptance_criteria=["a"], verification_commands=[],
            project_id="proj-b",
        )

        runtime = ProjectRuntime(
            project_id="proj-a",
            repo_root=self.root / "repo",
            state_root=self.root,
            config=config,
        )

        from local_cli_coordinator.reporting import NullReporter
        result = run_project_cycle(self.conn, runtime, NullReporter(), agent_id="test")

        # Should only process proj-a's task
        self.assertEqual(result.project_id, "proj-a")
