import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.engine import run_daemon_cycle


def test_config(repo_path: Path, *, max_tasks_per_run: int = 2) -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "fake": AgentConfig(
                id="fake",
                command=f"{sys.executable} -c \"from pathlib import Path; Path('feature.txt').write_text('done')\"",
                capabilities=["code"],
                max_concurrency=2,
            )
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=repo_path,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[
                    f"{sys.executable} -c \"from pathlib import Path; assert Path('feature.txt').exists()\""
                ],
                review_policy="tests_only",
            )
        },
        policy=PolicyConfig(
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
        ),
    )


class MultiTaskRunTests(unittest.TestCase):
    def test_daemon_cycle_processes_up_to_max_tasks_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            conn = connect(root / "coordinator.db")
            init_db(conn)
            config = test_config(repo, max_tasks_per_run=2)
            for index in range(3):
                create_task(
                    conn,
                    title=f"Task {index}",
                    repo="demo",
                    source_path=f"tasks/inbox/task-{index}.md",
                    priority="normal",
                    capabilities=["code"],
                    goal="Do work",
                    acceptance_criteria=["Works"],
                    verification_commands=config.repos["demo"].verify_commands,
                )

            calls: list[int] = []

            def counting_process_task(conn, cfg, root_path, task, agent_id, **kwargs):
                calls.append(1)
                return True

            with patch(
                "local_cli_coordinator.engine._process_task",
                side_effect=counting_process_task,
            ):
                result = run_daemon_cycle(conn, config, root)

            self.assertEqual(result.tasks_processed, 2)
            self.assertEqual(len(calls), 2)

    def test_daemon_cycle_stops_on_circuit_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            conn = connect(root / "coordinator.db")
            init_db(conn)
            config = test_config(repo, max_tasks_per_run=5)
            config = CoordinatorConfig(
                agents=config.agents,
                repos=config.repos,
                policy=PolicyConfig(
                    require_single_repo=True,
                    require_acceptance_criteria=True,
                    require_verification_commands=True,
                    require_handoff_summary=False,
                    max_files_touched=3,
                    max_expected_minutes=30,
                    max_attempts=3,
                    split_if_touches_multiple_subsystems=True,
                    split_if_research_and_code_are_mixed=True,
                    max_tasks_per_run=5,
                    max_tasks_per_day=0,
                ),
            )
            create_task(
                conn,
                title="Blocked by cap",
                repo="demo",
                source_path="tasks/inbox/one.md",
                priority="normal",
                capabilities=["code"],
                goal="Do work",
                acceptance_criteria=["Works"],
                verification_commands=[],
            )

            result = run_daemon_cycle(conn, config, root)

            self.assertEqual(result.tasks_processed, 0)
            self.assertIsNotNone(result.stop_reason)
            self.assertIn("daily task limit reached", result.stop_reason)