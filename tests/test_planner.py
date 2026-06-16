import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.models import TaskDraft
from local_cli_coordinator.tasks import write_generated_task


class PlannerTests(unittest.TestCase):
    def test_write_generated_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = TaskDraft(
                title="Generated small task",
                repo="demo",
                priority="normal",
                capabilities=["code"],
                goal="Change one file.",
                acceptance_criteria=["Verification passes."],
                verification_commands=["python -m unittest"],
            )

            path = write_generated_task(root, task)

            self.assertTrue(path.exists())
            content = path.read_text()
            self.assertIn("# Task: Generated small task", content)
            self.assertIn("repo: demo", content)
            self.assertIn("## Acceptance Criteria", content)
