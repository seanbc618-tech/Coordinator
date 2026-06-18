import tempfile
import unittest
from pathlib import Path

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
