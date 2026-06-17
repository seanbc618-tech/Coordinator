import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.config import CoordinatorConfig, RepoConfig, load_config
from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.engine import run_one_ready_task
from tests.helpers import init_git_repo
from tests.test_engine import test_config


def create_feature_task(conn) -> str:
    return create_task(
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


class PromptContextTests(unittest.TestCase):
    def test_repo_memory_path_can_be_configured_in_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "agents.toml").write_text(
                """
                [agents.fake]
                command = "python -c 'print(1)'"
                capabilities = ["code"]
                max_concurrency = 1
                """.strip()
            )
            (root / "config" / "repos.toml").write_text(
                """
                [repos.demo]
                path = "/tmp/demo"
                default_branch = "main"
                remote = "origin"
                branch_prefix = "coord/"
                allow_push = false
                merge_policy = "no_push"
                verify_commands = ["python -m unittest"]
                memory_path = "memory/demo.md"
                review_policy = "tests_only"
                """.strip()
            )
            (root / "config" / "policy.toml").write_text(
                """
                [task_policy]
                require_single_repo = true
                require_acceptance_criteria = true
                require_verification_commands = true
                require_handoff_summary = true
                max_files_touched = 3
                max_expected_minutes = 30
                max_attempts = 3
                split_if_touches_multiple_subsystems = true
                split_if_research_and_code_are_mixed = true
                """.strip()
            )

            config = load_config(root)

        self.assertEqual(config.repos["demo"].memory_path, Path("memory") / "demo.md")
        self.assertEqual(config.repos["demo"].review_policy, "tests_only")

    def test_prompt_includes_loop_and_repo_memory_when_files_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            (root / "state").mkdir()
            (root / "state" / "loop_state.md").write_text(
                "# Loop State\n\n- task: prior\n- next action: continue\n"
            )
            repo_memory = root / "memory" / "demo.md"
            repo_memory.parent.mkdir()
            repo_memory.write_text("# Demo Repo Memory\n\nPrefer focused tests.\n")
            config = test_config(repo)
            repos = {
                "demo": RepoConfig(
                    id="demo",
                    path=repo,
                    default_branch="main",
                    remote="origin",
                    branch_prefix="coord/",
                    allow_push=False,
                    merge_policy="no_push",
                    verify_commands=[
                        f"{sys.executable} -c \"from pathlib import Path; assert Path('feature.txt').read_text() == 'done'\""
                    ],
                    memory_path=Path("memory") / "demo.md",
                    review_policy="tests_only",
                )
            }
            config = CoordinatorConfig(
                agents=config.agents,
                repos=repos,
                policy=config.policy,
            )
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_feature_task(conn)
            try:
                processed = run_one_ready_task(conn, config, root)
            finally:
                conn.close()

            prompt = (root / "runs" / task_id / "prompt.md").read_text()

        self.assertTrue(processed)
        self.assertIn("## Loop Memory", prompt)
        self.assertIn("- task: prior", prompt)
        self.assertIn("## Repo Memory", prompt)
        self.assertIn("Prefer focused tests.", prompt)

    def test_prompt_ignores_missing_memory_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            config = test_config(repo)
            repos = {
                "demo": RepoConfig(
                    id="demo",
                    path=repo,
                    default_branch="main",
                    remote="origin",
                    branch_prefix="coord/",
                    allow_push=False,
                    merge_policy="no_push",
                    verify_commands=[
                        f"{sys.executable} -c \"from pathlib import Path; assert Path('feature.txt').read_text() == 'done'\""
                    ],
                    memory_path=Path("missing") / "demo.md",
                    review_policy="tests_only",
                )
            }
            config = CoordinatorConfig(
                agents=config.agents,
                repos=repos,
                policy=config.policy,
            )
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_feature_task(conn)
            try:
                processed = run_one_ready_task(conn, config, root)
            finally:
                conn.close()

            prompt = (root / "runs" / task_id / "prompt.md").read_text()

        self.assertTrue(processed)
        self.assertNotIn("## Loop Memory", prompt)
        self.assertNotIn("## Repo Memory", prompt)

    def test_prompt_ignores_unreadable_memory_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            (root / "state" / "loop_state.md").mkdir(parents=True)
            repo_memory = root / "memory" / "demo.md"
            repo_memory.parent.mkdir()
            repo_memory.write_bytes(b"\xff\xfe\xfd")
            config = test_config(repo)
            repos = {
                "demo": RepoConfig(
                    id="demo",
                    path=repo,
                    default_branch="main",
                    remote="origin",
                    branch_prefix="coord/",
                    allow_push=False,
                    merge_policy="no_push",
                    verify_commands=[
                        f"{sys.executable} -c \"from pathlib import Path; assert Path('feature.txt').read_text() == 'done'\""
                    ],
                    memory_path=Path("memory") / "demo.md",
                    review_policy="tests_only",
                )
            }
            config = CoordinatorConfig(
                agents=config.agents,
                repos=repos,
                policy=config.policy,
            )
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_feature_task(conn)
            try:
                processed = run_one_ready_task(conn, config, root)
            finally:
                conn.close()

            prompt = (root / "runs" / task_id / "prompt.md").read_text()

        self.assertTrue(processed)
        self.assertNotIn("## Loop Memory", prompt)
        self.assertNotIn("## Repo Memory", prompt)
