"""Red tests for Phase 12 CI failure classification.

Owner: Grok (Phase 12 Task 0)
Expected before implementation: missing ci_failure_classifier module.
"""

from __future__ import annotations

import unittest

from local_cli_coordinator.ci_failure_classifier import (
    classify_check_failure,
    summarize_check_failure,
)


class CiFailureClassifierTests(unittest.TestCase):
    def test_classifies_test_failure(self) -> None:
        result = classify_check_failure(
            check_name="unit",
            state="FAILURE",
            bucket="fail",
            log_excerpt="FAILED tests/test_foo.py::test_bar - AssertionError",
        )
        self.assertEqual(result.failure_class, "test_failure")
        self.assertTrue(result.summary)

    def test_classifies_lint_failure(self) -> None:
        result = classify_check_failure(
            check_name="lint",
            state="FAILURE",
            bucket="fail",
            log_excerpt="error: E501 line too long (flake8)",
        )
        self.assertEqual(result.failure_class, "lint_failure")

    def test_classifies_typecheck_failure(self) -> None:
        result = classify_check_failure(
            check_name="typecheck",
            state="FAILURE",
            bucket="fail",
            log_excerpt="error TS2345: Argument of type 'string'",
        )
        self.assertEqual(result.failure_class, "typecheck_failure")

    def test_classifies_flaky_failure(self) -> None:
        result = classify_check_failure(
            check_name="unit",
            state="FAILURE",
            bucket="fail",
            log_excerpt="Flaky test detected; passed on retry 2/3",
        )
        self.assertEqual(result.failure_class, "flaky")

    def test_unknown_for_unrecognized_output(self) -> None:
        result = classify_check_failure(
            check_name="custom",
            state="FAILURE",
            bucket="fail",
            log_excerpt="something weird happened",
        )
        self.assertEqual(result.failure_class, "unknown")

    def test_summarize_includes_check_name_and_class(self) -> None:
        classified = classify_check_failure(
            check_name="build",
            state="FAILURE",
            bucket="fail",
            log_excerpt="npm ERR! build failed",
        )
        summary = summarize_check_failure(classified)
        self.assertIn("build", summary)
        self.assertIn(classified.failure_class, summary)


if __name__ == "__main__":
    unittest.main()