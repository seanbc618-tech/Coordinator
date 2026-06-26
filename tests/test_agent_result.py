"""Tests for interactive worker block classification."""

import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.agent_result import (
    AgentResultClass,
    ClassifiedResult,
    classify_agent_output,
)


# Real Claude plan-mode approval captured 2026-06-20
CLAUDE_PLAN_APPROVAL = (
    "I'd like to use the EnterPlanMode tool to explore the codebase and design "
    "an implementation approach for user approval. May I proceed?"
)

# Real Claude implementation approval captured 2026-06-20
CLAUDE_IMPLEMENTATION_APPROVAL = (
    "Before I implement this change, I want to confirm: should I proceed with "
    "modifying the database schema as described above?"
)


class ClassifyInteractiveBlocksTest(TestCase):
    """Verify the classifier detects approval-seeking patterns."""

    def test_plan_mode_approval(self) -> None:
        result = classify_agent_output(CLAUDE_PLAN_APPROVAL, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.INTERACTIVE_BLOCKED)
        # Matches "may I proceed" pattern
        self.assertIn("approval", result.reason)

    def test_implementation_approval(self) -> None:
        result = classify_agent_output(CLAUDE_IMPLEMENTATION_APPROVAL, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.INTERACTIVE_BLOCKED)
        self.assertIn("approval", result.reason)

    def test_case_insensitive(self) -> None:
        text = "SHOULD I PROCEED WITH THIS CHANGE?"
        result = classify_agent_output(text, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.INTERACTIVE_BLOCKED)

    def test_exit_plan_mode_approval(self) -> None:
        text = (
            "I've designed the approach. Would you like me to exit plan mode "
            "and begin implementation?"
        )
        result = classify_agent_output(text, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.INTERACTIVE_BLOCKED)
        # Matches "would you like me to" pattern
        self.assertIn("approval", result.reason)

    def test_tell_me_to_proceed(self) -> None:
        text = "Tell me to proceed and I'll start implementing."
        result = classify_agent_output(text, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.INTERACTIVE_BLOCKED)

    def test_ordinary_implementation_summary(self) -> None:
        text = (
            "I've implemented the requested changes:\n"
            "- Added the new function to utils.py\n"
            "- Updated tests to cover edge cases\n"
            "All tests pass."
        )
        result = classify_agent_output(text, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.COMPLETED)

    def test_generic_question_not_blocked(self) -> None:
        text = "What file should I modify?"
        result = classify_agent_output(text, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.COMPLETED)

    def test_non_zero_exit_overrides_text(self) -> None:
        text = "I'd like to proceed with the implementation."
        result = classify_agent_output(text, exit_code=1)
        self.assertEqual(result.classification, AgentResultClass.COMMAND_FAILED)
        self.assertIn("exit", result.reason)

    def test_timeout_overrides_text(self) -> None:
        text = "I've completed all the changes successfully."
        result = classify_agent_output(text, exit_code=0, timed_out=True)
        self.assertEqual(result.classification, AgentResultClass.TIMED_OUT)

    def test_timeout_takes_precedence_over_non_zero_exit(self) -> None:
        text = "Done."
        result = classify_agent_output(text, exit_code=1, timed_out=True)
        self.assertEqual(result.classification, AgentResultClass.TIMED_OUT)

    def test_empty_output_completed(self) -> None:
        result = classify_agent_output("", exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.COMPLETED)

    def test_approval_before_implementation(self) -> None:
        text = (
            "Before I make any changes, do you approve this approach?\n"
            "1. Create a new migration file\n"
            "2. Update the schema\n"
            "3. Run tests"
        )
        result = classify_agent_output(text, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.INTERACTIVE_BLOCKED)

    def test_may_i_continue(self) -> None:
        text = "May I continue with the next step?"
        result = classify_agent_output(text, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.INTERACTIVE_BLOCKED)

    def test_would_you_like_me_to(self) -> None:
        text = "Would you like me to implement the changes?"
        result = classify_agent_output(text, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.INTERACTIVE_BLOCKED)

    def test_do_you_want_me_to(self) -> None:
        text = "Do you want me to proceed?"
        result = classify_agent_output(text, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.INTERACTIVE_BLOCKED)

    def test_shall_i(self) -> None:
        text = "Shall I go ahead and make the changes?"
        result = classify_agent_output(text, exit_code=0)
        self.assertEqual(result.classification, AgentResultClass.INTERACTIVE_BLOCKED)


class ClassifyFromLogFileTest(TestCase):
    """Verify classification reads from log files."""

    def test_classify_from_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "agent.log"
            log_path.write_text(
                "command: test\n"
                "stdout:\n"
                + CLAUDE_PLAN_APPROVAL
                + "\nexit_code: 0\ntimed_out: False\n"
            )
            result = classify_agent_output(log_path.read_text(), exit_code=0)
            self.assertEqual(
                result.classification, AgentResultClass.INTERACTIVE_BLOCKED
            )


class ClassifiedResultTest(TestCase):
    """Verify the dataclass contract."""

    def test_frozen(self) -> None:
        result = ClassifiedResult(
            classification=AgentResultClass.COMPLETED,
            reason="ok",
        )
        with self.assertRaises(AttributeError):
            result.classification = AgentResultClass.COMMAND_FAILED  # type: ignore[misc]

    def test_reason_code_is_string(self) -> None:
        result = ClassifiedResult(
            classification=AgentResultClass.COMPLETED,
            reason="ok",
        )
        self.assertIsInstance(result.reason, str)
