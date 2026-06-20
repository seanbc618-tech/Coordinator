import sys
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, create_task, get_task, init_db
from local_cli_coordinator.engine import run_one_ready_task
from local_cli_coordinator.review import write_spec_review_prompt
from tests.helpers import init_git_repo


def review_config(repo_path: Path, reviewer_command: str) -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "worker": AgentConfig(
                id="worker",
                command=f"{sys.executable} -c \"from pathlib import Path; Path('feature.txt').write_text('done')\"",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
            "spec": AgentConfig(
                id="spec",
                command=reviewer_command,
                capabilities=["code"],
                max_concurrency=1,
                role="spec_reviewer",
            ),
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


class SpecReviewTests(unittest.TestCase):
    def test_spec_review_prompt_uses_absolute_diff_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "task"
            run_dir.mkdir(parents=True)
            diff_path = Path("runs/task/diff.patch")
            task = {
                "title": "Review task",
                "repo": "demo",
                "goal": "Review it",
                "acceptance_criteria": "It passes",
            }
            prompt = write_spec_review_prompt(task, ["feature.txt"], diff_path, run_dir)
            self.assertIn(str(diff_path.resolve()), prompt.read_text())

    def test_engine_runs_spec_reviewer_after_verification_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_feature_task(conn)
            try:
                processed = run_one_ready_task(
                    conn,
                    review_config(repo, f"{sys.executable} -c \"raise SystemExit(0)\""),
                    root,
                )
                task = get_task(conn, task_id)
                artifacts = conn.execute(
                    "select kind, path from artifacts where task_id = ? order by id",
                    (task_id,),
                ).fetchall()
            finally:
                conn.close()

            prompt = (root / "runs" / task_id / "spec_review_prompt.md").read_text()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "done")
        self.assertIn("## Goal", prompt)
        self.assertIn("Create feature.txt.", prompt)
        self.assertIn("## Acceptance Criteria", prompt)
        self.assertIn("feature.txt contains done", prompt)
        self.assertIn("## Changed Files", prompt)
        self.assertIn("feature.txt", prompt)
        self.assertIn("## Diff", prompt)
        self.assertIn("diff.patch", prompt)
        self.assertIn("spec_review_log", [artifact["kind"] for artifact in artifacts])

    def test_failed_spec_review_moves_task_to_awaiting_human_for_reviewed_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_feature_task(conn)
            try:
                processed = run_one_ready_task(
                    conn,
                    review_config(repo, f"{sys.executable} -c \"raise SystemExit(7)\""),
                    root,
                )
                task = get_task(conn, task_id)
                artifacts = conn.execute(
                    "select kind, path from artifacts where task_id = ? order by id",
                    (task_id,),
                ).fetchall()
            finally:
                conn.close()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "awaiting_human")
        self.assertIn("spec_review_log", [artifact["kind"] for artifact in artifacts])

    def test_failed_spec_review_rejects_auto_merge_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_feature_task(conn)
            config = review_config(repo, f"{sys.executable} -c \"raise SystemExit(7)\"")
            repo_config = config.repos["demo"]
            config = CoordinatorConfig(
                agents=config.agents,
                repos={
                    "demo": RepoConfig(
                        id=repo_config.id,
                        path=repo_config.path,
                        default_branch=repo_config.default_branch,
                        remote=repo_config.remote,
                        branch_prefix=repo_config.branch_prefix,
                        allow_push=repo_config.allow_push,
                        merge_policy="auto_merge_default_branch",
                        verify_commands=repo_config.verify_commands,
                        review_policy=repo_config.review_policy,
                    )
                },
                policy=config.policy,
            )
            try:
                processed = run_one_ready_task(conn, config, root)
                task = get_task(conn, task_id)
            finally:
                conn.close()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "rejected")
