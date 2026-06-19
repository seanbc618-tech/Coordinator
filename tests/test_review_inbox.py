import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.helpers import init_git_repo, run
from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, get_task, init_db
from local_cli_coordinator.engine import run_one_ready_task
from local_cli_coordinator.review_inbox import (
    review_packet_path,
    write_review_packet,
)


def _task(**overrides) -> dict:
    defaults = dict(
        id="task-001",
        title="Fix login timeout",
        repo="demo",
        branch="coord/task-001-fix-login-timeout",
        state="awaiting_human",
    )
    defaults.update(overrides)
    return defaults


class ReviewPacketPathTests(unittest.TestCase):
    def test_path_points_to_tasks_review(self) -> None:
        root = Path("/tmp/test-root")
        path = review_packet_path(root, "task-001")
        self.assertEqual(path, root / "tasks" / "review" / "task-001.md")


class WriteReviewPacketTests(unittest.TestCase):
    def test_packet_contains_task_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_review_packet(root, _task())
            content = path.read_text()

        self.assertIn("# Review: Fix login timeout", content)
        self.assertIn("task-001", content)
        self.assertIn("demo", content)
        self.assertIn("coord/task-001-fix-login-timeout", content)
        self.assertIn("awaiting_human", content)

    def test_packet_contains_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_review_packet(
                root, _task(), changed_files=["src/auth.py", "tests/test_auth.py"]
            )
            content = path.read_text()

        self.assertIn("- src/auth.py", content)
        self.assertIn("- tests/test_auth.py", content)

    def test_packet_contains_review_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_review_packet(
                root,
                _task(),
                verifier_result="passed",
                spec_review_result="failed: missing error handling",
                quality_review_result="(not run)",
                suggested_action="add error handling for timeout",
            )
            content = path.read_text()

        self.assertIn("passed", content)
        self.assertIn("missing error handling", content)
        self.assertIn("add error handling for timeout", content)

    def test_packet_with_no_optional_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_review_packet(root, _task())
            content = path.read_text()

        self.assertIn("(none)", content)
        self.assertIn("(not available)", content)

    def test_packet_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse((root / "tasks" / "review").exists())
            write_review_packet(root, _task())
            self.assertTrue((root / "tasks" / "review").exists())

    def test_packet_file_is_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_review_packet(root, _task())
            self.assertTrue(path.name.endswith(".md"))


class EngineReviewPacketTests(unittest.TestCase):
    def test_packet_contains_persisted_branch_after_worktree_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_path = root / "repo"
            init_git_repo(repo_path)
            remote = root / "remote.git"
            run("git", "init", "--bare", remote, cwd=root)
            run("git", "remote", "add", "origin", remote, cwd=repo_path)
            run("git", "push", "origin", "main", cwd=repo_path)
            pass_review = f'{sys.executable} -c "raise SystemExit(0)"'
            config = CoordinatorConfig(
                agents={
                    "worker": AgentConfig(
                        id="worker",
                        command=(
                            f'{sys.executable} -c "from pathlib import Path; '
                            "Path('feature.txt').write_text('done')\""
                        ),
                        capabilities=["code"],
                        max_concurrency=1,
                        role="worker",
                    ),
                    "spec": AgentConfig(
                        id="spec",
                        command=pass_review,
                        capabilities=["code"],
                        max_concurrency=1,
                        role="spec_reviewer",
                    ),
                    "quality": AgentConfig(
                        id="quality",
                        command=pass_review,
                        capabilities=["code"],
                        max_concurrency=1,
                        role="quality_reviewer",
                    ),
                },
                repos={
                    "demo": RepoConfig(
                        id="demo",
                        path=repo_path,
                        default_branch="main",
                        remote="origin",
                        branch_prefix="coord/",
                        allow_push=True,
                        merge_policy="auto_merge_default_branch",
                        verify_commands=[
                            f'{sys.executable} -c "from pathlib import Path; '
                            "assert Path('feature.txt').read_text() == 'done'\""
                        ],
                        review_policy="always_human",
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
                ),
            )

            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Human review gate",
                repo="demo",
                source_path="tasks/inbox/human.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[],
            )

            processed = run_one_ready_task(conn, config, root)
            task = get_task(conn, task_id)
            packet = review_packet_path(root, task_id).read_text()
            conn.close()

            self.assertTrue(processed)
            self.assertEqual(task["state"], "awaiting_human")
            self.assertTrue(task["branch"].startswith("coord/"))
            self.assertIn(task["branch"], packet)
            self.assertNotIn("**Branch:** (not set)", packet)
