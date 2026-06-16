import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.helpers import run_cli


TASK_MARKDOWN = """
# Task: One

repo: demo
priority: normal
capabilities: [code]
verification: [python -m unittest]

## Goal

Ship one.

## Acceptance Criteria

- Works.
"""


def write_config(root: Path) -> None:
    (root / "config").mkdir()
    (root / "config" / "agents.toml").write_text(textwrap.dedent("""
        [agents.fake]
        command = "python -c 'print(1)'"
        capabilities = ["code"]
        max_concurrency = 1
    """).strip())
    (root / "config" / "repos.toml").write_text(textwrap.dedent("""
        [repos.demo]
        path = "/tmp/demo"
        default_branch = "main"
        remote = "origin"
        branch_prefix = "coord/"
        allow_push = false
        merge_policy = "no_push"
        verify_commands = ["python -m unittest"]
    """).strip())
    (root / "config" / "policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = true
        require_acceptance_criteria = true
        require_verification_commands = true
        require_handoff_summary = false
        max_files_touched = 3
        max_expected_minutes = 30
        max_attempts = 3
        split_if_touches_multiple_subsystems = true
        split_if_research_and_code_are_mixed = true
    """).strip())


class CliCommandTests(unittest.TestCase):
    def test_inbox_scan_imports_markdown_and_status_counts_ready_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            inbox = root / "tasks" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "one.md").write_text(textwrap.dedent(TASK_MARKDOWN).strip())

            result = run_cli("--root", str(root), "inbox", "scan")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("imported 1 task", result.stdout)
            self.assertFalse((root / "tasks" / "inbox" / "one.md").exists())
            self.assertTrue((root / "tasks" / "accepted" / "one.md").exists())

            status = run_cli("--root", str(root), "status")

            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("ready: 1", status.stdout)

    def test_status_reports_no_tasks_for_empty_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = run_cli("--root", str(root), "status")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no tasks", result.stdout)

    def test_task_commands_list_show_retry_and_block_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            inbox = root / "tasks" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "one.md").write_text(textwrap.dedent(TASK_MARKDOWN).strip())
            scan = run_cli("--root", str(root), "inbox", "scan")
            self.assertEqual(scan.returncode, 0, scan.stderr)

            listed = run_cli("--root", str(root), "task", "list")

            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn("ready One", listed.stdout)
            task_id = listed.stdout.split()[0]

            shown = run_cli("--root", str(root), "task", "show", task_id)
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertIn(f"id: {task_id}", shown.stdout)
            self.assertIn("state: ready", shown.stdout)
            self.assertIn("title: One", shown.stdout)
            self.assertIn("repo: demo", shown.stdout)

            blocked = run_cli("--root", str(root), "task", "block", task_id)
            self.assertEqual(blocked.returncode, 0, blocked.stderr)
            self.assertIn("blocked", blocked.stdout)

            retried = run_cli("--root", str(root), "task", "retry", task_id)
            self.assertEqual(retried.returncode, 0, retried.stderr)
            self.assertIn("ready", retried.stdout)

    def test_daemon_once_reports_no_ready_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)

            result = run_cli("--root", str(root), "daemon", "--once")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no ready tasks", result.stdout)

    def test_bare_nested_commands_return_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            inbox = run_cli("--root", str(root), "inbox")
            task = run_cli("--root", str(root), "task")

            self.assertEqual(inbox.returncode, 2, inbox.stdout + inbox.stderr)
            self.assertIn("usage:", inbox.stderr)
            self.assertEqual(task.returncode, 2, task.stdout + task.stderr)
            self.assertIn("usage:", task.stderr)

    def test_inbox_scan_reports_rejected_task_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            inbox = root / "tasks" / "inbox"
            inbox.mkdir(parents=True)
            bad = textwrap.dedent("""
                # Task: Bad

                repo: missing
                priority: normal
                capabilities: [code]
                verification: []

                ## Goal

                Ship bad.

                ## Acceptance Criteria

                - Works.
            """).strip()
            (inbox / "bad.md").write_text(bad)

            result = run_cli("--root", str(root), "inbox", "scan")

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("rejected tasks/inbox/bad.md", result.stderr)
            self.assertIn("missing verification commands", result.stderr)
            self.assertIn("repo is not allowlisted: missing", result.stderr)
            self.assertTrue((root / "tasks" / "inbox" / "bad.md").exists())
