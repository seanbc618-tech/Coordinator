import unittest

from local_cli_coordinator.config import RepoConfig
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