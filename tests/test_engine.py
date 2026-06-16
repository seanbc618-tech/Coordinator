import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import init_git_repo, run
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


def latest_event_note(conn, task_id: str) -> str:
    row = conn.execute(
        "select note from events where task_id = ? order by id desc limit 1",
        (task_id,),
    ).fetchone()
    return row["note"]


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

    def test_blocks_task_when_repo_is_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Create feature file",
                repo="missing",
                source_path="tasks/inbox/feature.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[],
            )
            config = test_config(repo)
            config = CoordinatorConfig(agents=config.agents, repos={}, policy=config.policy)

            processed = run_one_ready_task(conn, config, root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "blocked")
            self.assertIn("repo is not configured: missing", latest_event_note(conn, task_id))

    def test_blocks_task_when_no_agents_are_configured(self) -> None:
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
            config = test_config(repo)
            config = CoordinatorConfig(agents={}, repos=config.repos, policy=config.policy)

            processed = run_one_ready_task(conn, config, root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "blocked")
            self.assertIn("no configured agents", latest_event_note(conn, task_id))

    def test_blocks_task_when_no_agent_matches_capabilities(self) -> None:
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
            config = test_config(repo)
            agents = {
                "docs": AgentConfig(
                    id="docs",
                    command=f"{sys.executable} -c \"print('docs')\"",
                    capabilities=["docs"],
                    max_concurrency=1,
                )
            }
            config = CoordinatorConfig(agents=agents, repos=config.repos, policy=config.policy)

            processed = run_one_ready_task(conn, config, root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "blocked")
            self.assertIn(
                "no matching agent for capabilities: code",
                latest_event_note(conn, task_id),
            )

    def test_blocks_task_when_required_capabilities_are_empty(self) -> None:
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
                capabilities=[],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[],
            )

            processed = run_one_ready_task(conn, test_config(repo), root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "blocked")
            self.assertIn(
                "no matching agent for capabilities: (none)",
                latest_event_note(conn, task_id),
            )

    def test_fails_task_when_worktree_creation_fails(self) -> None:
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
            branch = f"coord/{task_id}-create-feature-file"
            branch_result = run("git", "branch", branch, cwd=repo)
            self.assertEqual(branch_result.returncode, 0, branch_result.stderr)

            processed = run_one_ready_task(conn, test_config(repo), root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "failed")
            self.assertIn("worktree creation failed", latest_event_note(conn, task_id))

    def test_fails_task_when_configured_repo_path_is_missing(self) -> None:
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
            config = test_config(repo)
            repos = {
                "demo": RepoConfig(
                    id="demo",
                    path=root / "missing-repo",
                    default_branch="main",
                    remote="origin",
                    branch_prefix="coord/",
                    allow_push=False,
                    merge_policy="no_push",
                    verify_commands=[
                        f"{sys.executable} -c \"from pathlib import Path; assert Path('feature.txt').read_text() == 'done'\""
                    ],
                )
            }
            config = CoordinatorConfig(agents=config.agents, repos=repos, policy=config.policy)

            processed = run_one_ready_task(conn, config, root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "failed")
            self.assertIn("worktree creation failed", latest_event_note(conn, task_id))

    def test_fails_task_when_agent_makes_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Noop task",
                repo="demo",
                source_path="tasks/inbox/noop.md",
                priority="normal",
                capabilities=["code"],
                goal="Make no changes.",
                acceptance_criteria=["No changes are made."],
                verification_commands=[],
            )
            config = test_config(repo)
            agents = {
                "noop": AgentConfig(
                    id="noop",
                    command=f"{sys.executable} -c \"print('noop')\"",
                    capabilities=["code"],
                    max_concurrency=1,
                )
            }
            repos = {
                "demo": RepoConfig(
                    id="demo",
                    path=repo,
                    default_branch="main",
                    remote="origin",
                    branch_prefix="coord/",
                    allow_push=False,
                    merge_policy="no_push",
                    verify_commands=[f"{sys.executable} -c \"print('ok')\""],
                )
            }
            config = CoordinatorConfig(agents=agents, repos=repos, policy=config.policy)

            processed = run_one_ready_task(conn, config, root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "failed")
            self.assertIn("no changed files", latest_event_note(conn, task_id))
