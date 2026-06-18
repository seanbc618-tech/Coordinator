import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import init_git_repo, run
from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, get_task, init_db
from local_cli_coordinator.engine import run_one_ready_task
from local_cli_coordinator.policy import (
    SUPPORTED_REVIEW_POLICIES,
    allows_auto_merge,
    detect_risk_signals,
    should_require_human_review,
)


def repo(review_policy: str, merge_policy: str = "auto_merge_default_branch") -> RepoConfig:
    return RepoConfig(
        id="demo",
        path=__import__("pathlib").Path("/tmp/demo"),
        default_branch="main",
        remote="origin",
        branch_prefix="coord/",
        allow_push=True,
        merge_policy=merge_policy,
        verify_commands=[],
        review_policy=review_policy,
    )


class ReviewPolicyTests(unittest.TestCase):
    def test_supported_review_policies(self) -> None:
        self.assertIn("auto", SUPPORTED_REVIEW_POLICIES)
        self.assertIn("risky_human", SUPPORTED_REVIEW_POLICIES)
        self.assertIn("always_human", SUPPORTED_REVIEW_POLICIES)
        self.assertIn("tests_only", SUPPORTED_REVIEW_POLICIES)

    def test_detects_migration_and_dependency_risk(self) -> None:
        signals = detect_risk_signals(
            ["src/app.py", "migrations/005_add.sql", "pyproject.toml"],
            max_files_touched=10,
            spec_review_passed=True,
            quality_review_passed=True,
        )
        self.assertIn("migration file touched", signals)
        self.assertIn("dependency file touched", signals)

    def test_detects_too_many_files_and_failed_reviewers(self) -> None:
        signals = detect_risk_signals(
            ["a.py", "b.py", "c.py", "d.py"],
            max_files_touched=3,
            spec_review_passed=False,
            quality_review_passed=True,
        )
        self.assertIn("changed file count 4 exceeds limit 3", signals)
        self.assertIn("spec review did not pass", signals)

    def test_always_human_requires_review(self) -> None:
        required, reasons = should_require_human_review(
            repo("always_human"),
            changed_files=["README.md"],
            max_files_touched=10,
            spec_review_passed=True,
            quality_review_passed=True,
        )
        self.assertTrue(required)
        self.assertIn("review_policy requires human review", reasons)

    def test_risky_human_only_flags_risky_changes(self) -> None:
        safe_required, _ = should_require_human_review(
            repo("risky_human"),
            changed_files=["README.md"],
            max_files_touched=10,
            spec_review_passed=True,
            quality_review_passed=True,
        )
        risky_required, reasons = should_require_human_review(
            repo("risky_human"),
            changed_files=["config/policy.toml"],
            max_files_touched=10,
            spec_review_passed=True,
            quality_review_passed=True,
        )
        self.assertFalse(safe_required)
        self.assertTrue(risky_required)
        self.assertTrue(any("protected path" in reason for reason in reasons))

    def test_auto_merge_blocked_when_human_review_required(self) -> None:
        self.assertFalse(
            allows_auto_merge(
                repo("always_human"),
                changed_files=["README.md"],
                max_files_touched=10,
                spec_review_passed=True,
                quality_review_passed=True,
            )
        )
        self.assertTrue(
            allows_auto_merge(
                repo("auto"),
                changed_files=["README.md"],
                max_files_touched=10,
                spec_review_passed=True,
                quality_review_passed=True,
            )
        )

    def test_branch_only_allows_push_but_not_auto_merge(self) -> None:
        self.assertFalse(
            allows_auto_merge(
                repo("branch_only"),
                changed_files=["README.md"],
                max_files_touched=10,
                spec_review_passed=True,
                quality_review_passed=True,
            )
        )


class ReviewPolicyEngineTests(unittest.TestCase):
    def test_engine_pauses_auto_merge_for_always_human(self) -> None:
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
                    "fake": AgentConfig(
                        id="fake",
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
            conn.close()

            self.assertTrue(processed)
            self.assertEqual(task["state"], "awaiting_human")
            self.assertTrue((root / "tasks" / "review").exists())
            show_main = run(
                "git",
                f"--git-dir={remote}",
                "show",
                "main:feature.txt",
                cwd=root,
            )
            self.assertNotEqual(show_main.returncode, 0)