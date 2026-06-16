import unittest

from local_cli_coordinator.config import PolicyConfig
from local_cli_coordinator.models import TaskDraft
from local_cli_coordinator.policy import check_changed_files, check_task_draft


def policy() -> PolicyConfig:
    return PolicyConfig(
        require_single_repo=True,
        require_acceptance_criteria=True,
        require_verification_commands=True,
        require_handoff_summary=True,
        max_files_touched=3,
        max_expected_minutes=30,
        max_attempts=3,
        split_if_touches_multiple_subsystems=True,
        split_if_research_and_code_are_mixed=True,
    )


class PolicyTests(unittest.TestCase):
    def test_accepts_small_task(self) -> None:
        task = TaskDraft(
            title="Small",
            repo="demo",
            priority="normal",
            capabilities=["code"],
            goal="Change one thing.",
            acceptance_criteria=["Test passes."],
            verification_commands=["python -m unittest"],
        )
        result = check_task_draft(task, policy())
        self.assertTrue(result.accepted)
        self.assertEqual(result.reasons, [])

    def test_rejects_task_without_acceptance_criteria(self) -> None:
        task = TaskDraft(
            title="Vague",
            repo="demo",
            priority="normal",
            capabilities=["code"],
            goal="Improve the project.",
            acceptance_criteria=[],
            verification_commands=["python -m unittest"],
        )
        result = check_task_draft(task, policy())
        self.assertFalse(result.accepted)
        self.assertIn("missing acceptance criteria", result.reasons)

    def test_rejects_task_with_blank_acceptance_criteria(self) -> None:
        task = TaskDraft(
            title="Vague",
            repo="demo",
            priority="normal",
            capabilities=["code"],
            goal="Improve the project.",
            acceptance_criteria=[""],
            verification_commands=["python -m unittest"],
        )
        result = check_task_draft(task, policy())
        self.assertFalse(result.accepted)
        self.assertIn("missing acceptance criteria", result.reasons)

    def test_rejects_task_with_blank_verification_commands(self) -> None:
        task = TaskDraft(
            title="Vague",
            repo="demo",
            priority="normal",
            capabilities=["code"],
            goal="Improve the project.",
            acceptance_criteria=["Test passes."],
            verification_commands=[""],
        )
        result = check_task_draft(task, policy())
        self.assertFalse(result.accepted)
        self.assertIn("missing verification commands", result.reasons)

    def test_rejects_task_with_blank_repo(self) -> None:
        task = TaskDraft(
            title="Vague",
            repo="   ",
            priority="normal",
            capabilities=["code"],
            goal="Improve the project.",
            acceptance_criteria=["Test passes."],
            verification_commands=["python -m unittest"],
        )
        result = check_task_draft(task, policy())
        self.assertFalse(result.accepted)
        self.assertIn("missing repo", result.reasons)

    def test_rejects_too_many_changed_files(self) -> None:
        result = check_changed_files(
            ["a.py", "b.py", "c.py", "d.py"],
            policy(),
        )
        self.assertFalse(result.accepted)
        self.assertIn("changed file count 4 exceeds limit 3", result.reasons)
