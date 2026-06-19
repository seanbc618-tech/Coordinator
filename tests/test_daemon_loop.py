import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import init_git_repo, run_cli
from local_cli_coordinator.cli import _cmd_daemon
from local_cli_coordinator.config import DiscoverySourceConfig, load_config
from local_cli_coordinator.db import connect, get_task, init_db, list_tasks
from local_cli_coordinator.discovery import list_findings, save_finding
from local_cli_coordinator.engine import (
    ContinuousDaemonResult,
    run_continuous_daemon,
    run_daemon_cycle,
)
from local_cli_coordinator.models import Finding


TASK_MARKDOWN = """
# Task: Loop import

repo: demo
priority: normal
capabilities: [code]
verification: [python -m unittest]

## Goal

Ship loop import.

## Acceptance Criteria

- Works.
"""


def write_config(root: Path, *, max_daemon_runtime_seconds: int = 3600) -> None:
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
    (root / "config" / "policy.toml").write_text(textwrap.dedent(f"""
        [task_policy]
        require_single_repo = true
        require_acceptance_criteria = true
        require_verification_commands = true
        require_handoff_summary = false
        max_files_touched = 3
        max_expected_minutes = 30
        max_attempts = 3
        max_daemon_runtime_seconds = {max_daemon_runtime_seconds}
        split_if_touches_multiple_subsystems = true
        split_if_research_and_code_are_mixed = true

        [daemon_policy]
        loop_interval_seconds = 30
        idle_sleep_seconds = 5
        run_discovery_before_tasks = true
    """).strip())


class DaemonLoopTests(unittest.TestCase):
    def test_daemon_once_imports_inbox_before_processing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            inbox = root / "tasks" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "loop.md").write_text(textwrap.dedent(TASK_MARKDOWN).strip())

            result = run_cli("--root", str(root), "daemon", "--once")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("imported task", result.stdout)
            self.assertFalse((inbox / "loop.md").exists())
            self.assertTrue((root / "tasks" / "accepted" / "loop.md").exists())

    def test_cycle_runs_configured_command_discovery_before_processing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            write_config(root)
            (root / "config" / "policy.toml").write_text(textwrap.dedent("""
                [task_policy]
                require_single_repo = true
                require_acceptance_criteria = true
                require_verification_commands = false
                require_handoff_summary = false
                max_files_touched = 3
                max_expected_minutes = 30
                max_attempts = 3
                max_daemon_runtime_seconds = 3600
                split_if_touches_multiple_subsystems = true
                split_if_research_and_code_are_mixed = true

                [daemon_policy]
                loop_interval_seconds = 30
                idle_sleep_seconds = 5
                run_discovery_before_tasks = true
            """).strip())
            (root / "config" / "repos.toml").write_text(textwrap.dedent(f"""
                [repos.demo]
                path = "{repo}"
                default_branch = "main"
                remote = "origin"
                branch_prefix = "coord/"
                allow_push = false
                merge_policy = "no_push"
                verify_commands = ["{sys.executable} -c 'raise SystemExit(0)'"]
                review_policy = "tests_only"
            """).strip())

            emit_script = root / "emit_finding.py"
            emit_script.write_text(textwrap.dedent("""
                import json

                print(json.dumps({
                    "id": "finding-daemon-001",
                    "repo": "demo",
                    "source": "command",
                    "title": "Daemon discovery task",
                    "body": "- Create feature.txt with done.\\n- Verification passes.",
                    "severity": "info",
                    "evidence": "source=daemon-cycle",
                    "discovered_at": "2026-06-19T15:00:00Z",
                }))
            """).strip(), encoding="utf-8")
            (root / "config" / "discovery.toml").write_text(textwrap.dedent(f"""
                [sources.command]
                type = "command"
                command = "{sys.executable} {emit_script}"
                [sources.command.repos]
                demo = true
            """).strip())
            (root / "config" / "agents.toml").write_text(textwrap.dedent(f"""
                [agents.fake]
                command = '''{sys.executable} -c "from pathlib import Path; Path('feature.txt').write_text('done')"'''
                capabilities = ["code"]
                max_concurrency = 1
            """).strip())

            conn = connect(root / "coordinator.db")
            init_db(conn)
            try:
                config = load_config(root)
                result = run_daemon_cycle(conn, config, root)
                tasks = list_tasks(conn)
                task = get_task(conn, tasks[0]["id"])
            finally:
                conn.close()

            self.assertEqual(len(list_findings(root)), 1)
            self.assertGreaterEqual(result.planned_tasks, 1)
            self.assertGreaterEqual(result.imported_tasks, 1)
            self.assertEqual(result.tasks_processed, 1)
            self.assertEqual(task["state"], "done")

    def test_cycle_plans_persisted_findings_into_generated_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            save_finding(
                root,
                Finding(
                    id="finding-demo-001",
                    repo="demo",
                    source="git_recent_commits",
                    title="Fix parser edge case",
                    body="- Handle empty titles.\n- Keep tests green.",
                    severity="info",
                    evidence="commit=abc123",
                ),
            )

            conn = connect(root / "coordinator.db")
            init_db(conn)
            try:
                config = load_config(root)
                result = run_daemon_cycle(conn, config, root)
            finally:
                conn.close()

            self.assertEqual(result.planned_tasks, 1)
            generated = list((root / "tasks" / "generated").glob("*.md"))
            self.assertEqual(len(generated), 1)
            self.assertIn("Fix parser edge case", generated[0].read_text())

    def test_continuous_daemon_stops_at_max_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root, max_daemon_runtime_seconds=1)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            config = load_config(root)
            sleeps: list[float] = []
            timeline = [0.0, 0.0, 0.5, 0.5, 2.0]

            def fake_sleep(seconds: float) -> None:
                sleeps.append(seconds)

            try:
                with patch(
                    "local_cli_coordinator.engine.time.monotonic",
                    side_effect=timeline,
                ):
                    result = run_continuous_daemon(
                        conn,
                        config,
                        root,
                        sleep_fn=fake_sleep,
                    )
            finally:
                conn.close()

            self.assertIn("max daemon runtime reached", result.message)
            self.assertTrue(sleeps)
            self.assertEqual(sleeps[0], config.daemon_policy.idle_sleep_seconds)

    def test_continuous_daemon_cli_runs_without_once_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root, max_daemon_runtime_seconds=1)

            args = type("Args", (), {
                "root": str(root),
                "db": "coordinator.db",
                "once": False,
                "force_lock": False,
            })()

            timeline = [0.0, 0.0, 0.5, 0.5, 2.0]
            with patch("local_cli_coordinator.cli.time.sleep"), patch(
                "local_cli_coordinator.cli.time.monotonic",
                side_effect=timeline,
            ), patch(
                "local_cli_coordinator.cli.run_continuous_daemon",
                return_value=ContinuousDaemonResult(
                    message="stopped: max daemon runtime reached",
                    tasks_processed=0,
                    failures=0,
                    stop_reason="max daemon runtime reached",
                ),
            ) as mocked:
                code = _cmd_daemon(args)

            self.assertEqual(code, 0)
            mocked.assert_called_once()

    def test_daemon_once_still_reports_no_ready_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)

            result = run_cli("--root", str(root), "daemon", "--once")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no ready tasks", result.stdout)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            try:
                self.assertEqual(list_tasks(conn), [])
            finally:
                conn.close()