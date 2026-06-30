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
from local_cli_coordinator.review import write_quality_review_prompt
from tests.helpers import init_git_repo


def reviewer_config(
    repo_path: Path,
    spec_command: str | None,
    quality_command: str | None,
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
    if spec_command is not None:
        agents["spec"] = AgentConfig(
            id="spec",
            command=spec_command,
            capabilities=["code"],
            max_concurrency=1,
            role="spec_reviewer",
        )
    if quality_command is not None:
        agents["quality"] = AgentConfig(
            id="quality",
            command=quality_command,
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


def quality_config(
    repo_path: Path,
    *,
    spec_command: str,
    quality_command: str,
) -> CoordinatorConfig:
    return reviewer_config(
        repo_path,
        spec_command=spec_command,
        quality_command=quality_command,
    )


def quality_without_spec_config(repo_path: Path, quality_command: str) -> CoordinatorConfig:
    return reviewer_config(
        repo_path,
        spec_command=None,
        quality_command=quality_command,
    )


def artifact_kinds(conn, task_id: str) -> list[str]:
    return [
        row["kind"]
        for row in conn.execute(
            "select kind from artifacts where task_id = ? order by id",
            (task_id,),
        ).fetchall()
    ]


def event_states(conn, task_id: str) -> list[str]:
    return [
        row["new_state"]
        for row in conn.execute(
            "select new_state from events where task_id = ? order by id",
            (task_id,),
        ).fetchall()
    ]


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


class QualityReviewTests(unittest.TestCase):
    def test_quality_review_prompt_uses_absolute_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "task"
            run_dir.mkdir(parents=True)
            diff_path = Path("runs/task/diff.patch")
            verifier_path = Path("runs/task/verifier.log")
            repo = reviewer_config(root, None, None).repos["demo"]
            task = {"title": "Review task", "repo": "demo"}
            prompt = write_quality_review_prompt(
                task, ["feature.txt"], diff_path, verifier_path, repo, run_dir,
            )
            content = prompt.read_text()
            self.assertIn(str(diff_path.resolve()), content)
            self.assertIn(str(verifier_path.resolve()), content)

    def test_quality_review_runs_after_spec_review_passes(self) -> None:
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
                    quality_config(
                        repo,
                        spec_command=f"{sys.executable} -c \"raise SystemExit(0)\"",
                        quality_command=f"{sys.executable} -c \"raise SystemExit(0)\"",
                    ),
                    root,
                )
                task = get_task(conn, task_id)
                artifacts = artifact_kinds(conn, task_id)
                states = event_states(conn, task_id)
            finally:
                conn.close()

            prompt = (root / "runs" / task_id / "quality_review_prompt.md").read_text()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "done")
        self.assertIn("## Changed Files", prompt)
        self.assertIn("feature.txt", prompt)
        self.assertIn("## Diff", prompt)
        self.assertIn("diff.patch", prompt)
        self.assertIn("## Verifier Log", prompt)
        self.assertIn("verifier.log", prompt)
        self.assertIn("## Repo Policy", prompt)
        self.assertIn("merge_policy: no_push", prompt)
        self.assertIn("quality_review_log", artifacts)
        self.assertLess(states.index("reviewing_spec"), states.index("reviewing_quality"))
        self.assertLess(states.index("reviewing_quality"), states.index("committing"))

    def test_failed_quality_review_stops_before_commit(self) -> None:
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
                    quality_config(
                        repo,
                        spec_command=f"{sys.executable} -c \"raise SystemExit(0)\"",
                        quality_command=f"{sys.executable} -c \"raise SystemExit(7)\"",
                    ),
                    root,
                )
                task = get_task(conn, task_id)
                artifacts = artifact_kinds(conn, task_id)
                states = event_states(conn, task_id)
            finally:
                conn.close()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "awaiting_human")
        self.assertIn("quality_review_log", artifacts)
        self.assertNotIn("committing", states)

    def test_quality_review_does_not_run_when_spec_review_fails(self) -> None:
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
                    quality_config(
                        repo,
                        spec_command=f"{sys.executable} -c \"raise SystemExit(7)\"",
                        quality_command=f"{sys.executable} -c \"from pathlib import Path; Path('quality-ran.txt').write_text('bad')\"",
                    ),
                    root,
                )
                task = get_task(conn, task_id)
                artifacts = artifact_kinds(conn, task_id)
                worktree = Path(task["worktree_path"])
            finally:
                conn.close()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "awaiting_human")
        self.assertFalse((worktree / "quality-ran.txt").exists())
        self.assertNotIn("quality_review_log", artifacts)

    def test_quality_review_requires_spec_review_to_have_run(self) -> None:
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
                    quality_without_spec_config(
                        repo,
                        f"{sys.executable} -c \"from pathlib import Path; Path('quality-ran.txt').write_text('bad')\"",
                    ),
                    root,
                )
                task = get_task(conn, task_id)
                artifacts = artifact_kinds(conn, task_id)
                worktree = Path(task["worktree_path"])
            finally:
                conn.close()

        self.assertTrue(processed)
        self.assertEqual(task["state"], "awaiting_human")
        self.assertFalse((worktree / "quality-ran.txt").exists())
        self.assertNotIn("quality_review_log", artifacts)
