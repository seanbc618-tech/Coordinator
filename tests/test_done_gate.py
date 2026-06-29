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
from tests.helpers import init_git_repo


def done_gate_config(
    repo_path: Path,
    *,
    review_policy: str = "full_review",
    include_spec: bool = False,
    include_quality: bool = False,
) -> CoordinatorConfig:
    agents = {
        "worker": AgentConfig(
            id="worker",
            command=f"{sys.executable} -c \"from pathlib import Path; Path('feature.txt').write_text('done')\"",
            capabilities=["code"],
            max_concurrency=1,
            role="worker",
        )
    }
    if include_spec:
        agents["spec"] = AgentConfig(
            id="spec",
            command=f"{sys.executable} -c \"raise SystemExit(0)\"",
            capabilities=["code"],
            max_concurrency=1,
            role="spec_reviewer",
        )
    if include_quality:
        agents["quality"] = AgentConfig(
            id="quality",
            command=f"{sys.executable} -c \"raise SystemExit(0)\"",
            capabilities=["code"],
            max_concurrency=1,
            role="quality_reviewer",
        )
    return CoordinatorConfig(
        agents=agents,
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
                review_policy=review_policy,
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


class DoneGateTests(unittest.TestCase):
    def test_full_review_repo_cannot_finish_without_review_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_feature_task(conn)
            try:
                processed = run_one_ready_task(conn, done_gate_config(repo), root)
                task = get_task(conn, task_id)
            finally:
                conn.close()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "awaiting_human")

    def test_tests_only_repo_can_finish_with_verifier_evidence(self) -> None:
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
                    done_gate_config(repo, review_policy="tests_only"),
                    root,
                )
                task = get_task(conn, task_id)
            finally:
                conn.close()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "done")

    def test_full_review_repo_can_finish_with_all_review_evidence(self) -> None:
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
                    done_gate_config(repo, include_spec=True, include_quality=True),
                    root,
                )
                task = get_task(conn, task_id)
            finally:
                conn.close()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "awaiting_human")
