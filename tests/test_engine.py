import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import init_git_repo, run
from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, get_task, init_db, transition_task
from local_cli_coordinator.engine import run_one_ready_task
from local_cli_coordinator.reporting import ExecutionEvent


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
                review_policy="tests_only",
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
    def test_records_merge_base_failure_without_crashing_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            self.addCleanup(conn.close)
            task_id = create_task(
                conn,
                title="Broken branch history",
                repo="demo",
                source_path="tasks/inbox/broken.md",
                priority="normal",
                capabilities=["code"],
                goal="Exercise merge-base failure handling.",
                acceptance_criteria=["Failure is recorded."],
                verification_commands=[],
            )

            with patch(
                "local_cli_coordinator.engine.merge_base",
                side_effect=RuntimeError("unrelated histories"),
            ):
                processed = run_one_ready_task(conn, test_config(repo), root)

            self.assertTrue(processed)
            self.assertEqual(get_task(conn, task_id)["state"], "failed")
            self.assertIn("merge-base lookup failed", latest_event_note(conn, task_id))

    def test_runs_agent_verifies_and_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            self.addCleanup(conn.close)
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

    def test_accepts_changes_committed_by_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            script = root / "committing_agent.py"
            script.write_text(
                "from pathlib import Path\nimport subprocess\n"
                "Path('feature.txt').write_text('done\\n')\n"
                "subprocess.run(['git', 'add', 'feature.txt'], check=True)\n"
                "subprocess.run(['git', 'commit', '-m', 'agent commit'], check=True)\n"
            )
            conn = connect(root / "coordinator.db")
            init_db(conn)
            self.addCleanup(conn.close)
            task_id = create_task(
                conn,
                title="Committed feature",
                repo="demo",
                source_path="tasks/inbox/feature.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[f"{sys.executable} -c \"from pathlib import Path; assert Path('feature.txt').read_text() == 'done\\\\n'\""],
            )
            base = test_config(repo)
            agents = {
                "committer": AgentConfig(
                    id="committer",
                    command=f"{sys.executable} {script}",
                    capabilities=["code"],
                    max_concurrency=1,
                )
            }
            config = CoordinatorConfig(agents=agents, repos=base.repos, policy=base.policy)

            self.assertTrue(run_one_ready_task(conn, config, root))
            self.assertEqual(get_task(conn, task_id)["state"], "done")

    def test_retry_reuses_existing_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            self.addCleanup(conn.close)
            task_id = create_task(
                conn,
                title="Retry feature",
                repo="demo",
                source_path="tasks/inbox/retry.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt exists"],
                verification_commands=[],
            )
            base = test_config(repo)
            failing_agents = {
                "worker": AgentConfig(
                    id="worker",
                    command=f"{sys.executable} -c \"raise SystemExit(1)\"",
                    capabilities=["code"],
                    max_concurrency=1,
                )
            }
            failing = CoordinatorConfig(agents=failing_agents, repos=base.repos, policy=base.policy)
            self.assertTrue(run_one_ready_task(conn, failing, root))
            existing = Path(get_task(conn, task_id)["worktree_path"])
            self.assertTrue(existing.exists())

            transition_task(conn, task_id, "ready", "manual retry")
            succeeding_agents = {
                "worker": AgentConfig(
                    id="worker",
                    command=f"{sys.executable} -c \"from pathlib import Path; Path('feature.txt').write_text('done')\"",
                    capabilities=["code"],
                    max_concurrency=1,
                )
            }
            succeeding = CoordinatorConfig(agents=succeeding_agents, repos=base.repos, policy=base.policy)

            self.assertTrue(run_one_ready_task(conn, succeeding, root))
            self.assertEqual(get_task(conn, task_id)["state"], "done")
            self.assertEqual(Path(get_task(conn, task_id)["worktree_path"]), existing)

    def test_blocks_task_when_repo_is_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            self.addCleanup(conn.close)
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
            self.addCleanup(conn.close)
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
            self.addCleanup(conn.close)
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
                "lacks capabilities: code",
                latest_event_note(conn, task_id),
            )

    def test_blocks_task_when_required_capabilities_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            self.addCleanup(conn.close)
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
            self.addCleanup(conn.close)
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
            self.addCleanup(conn.close)
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
                    review_policy="tests_only",
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
            self.addCleanup(conn.close)
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
                    review_policy="tests_only",
                )
            }
            config = CoordinatorConfig(agents=agents, repos=repos, policy=config.policy)

            processed = run_one_ready_task(conn, config, root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "failed")
            self.assertIn("no changed files", latest_event_note(conn, task_id))

    def test_forwards_reporter_to_git_pipeline(self) -> None:
        class RecordingReporter:
            def __init__(self) -> None:
                self.events: list[ExecutionEvent] = []

            def emit(self, event: ExecutionEvent) -> None:
                self.events.append(event)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            self.addCleanup(conn.close)
            create_task(
                conn,
                title="Reporter forwarding",
                repo="demo",
                source_path="tasks/inbox/reporter.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[],
            )
            reporter = RecordingReporter()

            with patch("local_cli_coordinator.engine.create_worktree") as mock_create:
                mock_create.side_effect = lambda **kwargs: (
                    kwargs["worktrees_root"] / kwargs["task_id"]
                )
                run_one_ready_task(conn, test_config(repo), root, reporter=reporter)

            self.assertEqual(mock_create.call_args.kwargs["reporter"], reporter)

    def test_report_only_task_passes_without_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            self.addCleanup(conn.close)
            verify_cmd = f'{sys.executable} -c "print(\'ok\')"'
            task_id = create_task(
                conn,
                title="Run baseline acceptance checks",
                repo="demo",
                source_path="tasks/inbox/baseline.md",
                priority="normal",
                capabilities=["tests"],
                goal="Run the repo verification commands without changing code.",
                acceptance_criteria=["Results are recorded."],
                verification_commands=[verify_cmd],
            )
            agents = {
                "noop": AgentConfig(
                    id="noop",
                    command=f"{sys.executable} -c \"print('noop')\"",
                    capabilities=["tests"],
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
                    verify_commands=[verify_cmd],
                    review_policy="tests_only",
                )
            }
            config = CoordinatorConfig(agents=agents, repos=repos, policy=test_config(repo).policy)

            processed = run_one_ready_task(conn, config, root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "done")
            self.assertIn("report-only verification passed", latest_event_note(conn, task_id))

    def test_worker_prompt_is_written_inside_worktree(self) -> None:
        captured: list[Path] = []

        def fake_run_agent(agent, prompt, worktree, attempt_dir, **kwargs):
            captured.append(prompt)
            from local_cli_coordinator.agent import AgentRunResult
            log_path = attempt_dir / "agent.log"
            log_path.write_text("noop\n")
            return AgentRunResult(
                agent_id=agent.id,
                command=agent.command,
                exit_code=0,
                log_path=log_path,
                timed_out=False,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            self.addCleanup(conn.close)
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
            config = CoordinatorConfig(agents=agents, repos=config.repos, policy=config.policy)

            with patch("local_cli_coordinator.engine.run_agent", side_effect=fake_run_agent):
                run_one_ready_task(conn, config, root)

            self.assertTrue(captured)
            prompt_path = captured[0]
            self.assertIn(".coordinator", str(prompt_path))
            self.assertTrue(prompt_path.name == "prompt.md")
