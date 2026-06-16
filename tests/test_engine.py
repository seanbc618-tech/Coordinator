import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import init_git_repo
from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, get_task, init_db
from local_cli_coordinator.engine import run_one_ready_task


def test_config(repo_path: Path) -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "fake": AgentConfig(
                id="fake",
                command=f"{sys.executable} -c \"from pathlib import Path; Path('feature.txt').write_text('done')\"",
                capabilities=["code", "tests"],
                max_concurrency=1,
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
                    f"{sys.executable} -c \"from pathlib import Path; assert Path('feature.txt').read_text() == 'done'\""
                ],
            )
        },
        policy=PolicyConfig(
            require_single_repo=True,
            require_acceptance_criteria=True,
            require_verification_commands=True,
            require_handoff_summary=True,
            max_files_touched=3,
            max_expected_minutes=30,
            max_attempts=3,
            split_if_touches_multiple_subsystems=True,
            split_if_research_and_code_are_mixed=True,
        ),
    )


class EngineTests(unittest.TestCase):
    def test_runs_agent_verifies_and_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Create feature file",
                repo="demo",
                source_path="tasks/inbox/feature.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[],
            )

            processed = run_one_ready_task(conn, test_config(repo), root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "done")
            self.assertTrue(task["branch"].startswith("coord/"))
            self.assertTrue(Path(task["worktree_path"]).exists())
