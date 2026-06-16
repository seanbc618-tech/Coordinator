import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.tasks import parse_task_markdown, scan_inbox


class TaskParserTests(unittest.TestCase):
    def test_parses_markdown_task(self) -> None:
        content = textwrap.dedent("""
            # Task: Add regression coverage

            repo: polymarket-weather-arb
            priority: normal
            capabilities: [tests, code]
            verification: [uv run pytest tests/test_rules.py -q]

            ## Goal

            Add focused regression coverage.

            ## Acceptance Criteria

            - Adds tests for low temperature titles.
            - Keeps the change small.
        """).strip()

        task = parse_task_markdown(content, "tasks/inbox/parser.md")

        self.assertEqual(task.title, "Add regression coverage")
        self.assertEqual(task.repo, "polymarket-weather-arb")
        self.assertEqual(task.capabilities, ["tests", "code"])
        self.assertEqual(task.verification_commands, ["uv run pytest tests/test_rules.py -q"])
        self.assertEqual(len(task.acceptance_criteria), 2)

    def test_scan_inbox_returns_markdown_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox = root / "tasks" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "one.md").write_text("# Task: One\n\nrepo: demo\npriority: normal\ncapabilities: [code]\nverification: [python -m unittest]\n\n## Goal\n\nShip one.\n\n## Acceptance Criteria\n\n- Works.")

            tasks = scan_inbox(root)

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].title, "One")
