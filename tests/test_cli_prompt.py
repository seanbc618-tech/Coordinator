"""Red tests for Phase 5.3 CLI prompt UX (Pi-inspired).

These tests describe **not-yet-implemented** CLI flags and subcommands.
They must fail today for the right reason (argparse unknown flag / missing
subcommand) and pass once Grok implements Task 1+.

Scope: tests only — do NOT edit src/ to make these pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from local_cli_coordinator.cli import build_prompt_parser, build_parser, normalize_prompt_args
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.fixtures.fake_supervisor import FakeSupervisor
from tests.helpers import ROOT, SRC, init_git_repo

_PYTHON = sys.executable

_ADMISSION_LEAK_TOKENS = [
    "duplicate title",
    "linked task",
    "admission",
    "no duplicate",
]


def _run_cli_with_home(home: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["COORDINATOR_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "local_cli_coordinator", *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


# ---------------------------------------------------------------------------
# 1. Parser red tests — flags not implemented yet
# ---------------------------------------------------------------------------

class CliPromptParserTests(unittest.TestCase):
    """Pi-inspired prompt flags parse via build_prompt_parser."""

    def _parse_prompt(self, argv: list[str]):
        parser = build_prompt_parser()
        args = parser.parse_args(argv)
        normalize_prompt_args(args)
        return args

    def test_print_prompt_flag_parses(self) -> None:
        args = self._parse_prompt(["-p", "你好", "--print"])
        self.assertEqual(args.prompt_text, "你好")
        self.assertTrue(args.print_mode)
        self.assertTrue(args.no_tui)

    def test_mode_json_flag_parses(self) -> None:
        args = self._parse_prompt(["--mode", "json", "-p", "hello", "--print"])
        self.assertEqual(args.mode, "json")
        self.assertEqual(args.prompt_text, "hello")
        self.assertTrue(args.print_mode)

    def test_continue_flag_parses(self) -> None:
        args = self._parse_prompt(["--continue", "-p", "next", "--print"])
        self.assertTrue(args.continue_goal)
        self.assertEqual(args.prompt_text, "next")

    def test_positional_prompt_parses(self) -> None:
        args = self._parse_prompt(["检查项目状态", "--print"])
        self.assertEqual(args.prompt_text, "检查项目状态")
        self.assertTrue(args.print_mode)

    def test_print_implies_no_tui(self) -> None:
        args = self._parse_prompt(["-p", "hello", "--print"])
        self.assertTrue(args.no_tui)
        args = self._parse_prompt(["-p", "hello"])
        self.assertFalse(args.no_tui)

    def test_mode_json_implies_no_tui(self) -> None:
        args = self._parse_prompt(["--mode", "json", "-p", "hello"])
        self.assertTrue(args.no_tui)
        self.assertFalse(args.print_mode)

    def test_existing_supervisor_subcommand_unaffected(self) -> None:
        """supervisor status must still parse and run (guard: not broken by new flags)."""
        result = _run_cli_with_home(Path(tempfile.mkdtemp()), "supervisor", "status")
        # exit 1 because no socket exists, but NOT exit 2 (argparse error)
        self.assertEqual(result.returncode, 1,
                         "supervisor status should exit 1 (no socket), got %d" % result.returncode)
        self.assertNotIn("error: argument", result.stderr.lower(),
                         "supervisor status hit argparse error — existing command broken")


# ---------------------------------------------------------------------------
# 2. Print red tests — headless chat.send path
# ---------------------------------------------------------------------------

class CliPromptPrintRedTests(unittest.TestCase):
    """--print should send chat.send via Supervisor RPC and print user_reply."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.repo = self.home / "repo"
        init_git_repo(self.repo)

        self.paths = RuntimePaths(self.home / "config", self.home / "data", self.home / "state")
        self.paths.create()

        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()

        self.project_id = self.conn.execute(
            "select id from projects where canonical_path = ?",
            (str(self.repo.resolve()),),
        ).fetchone()["id"]

        goal_id = create_goal(self.conn, "Test goal", "test acceptance", project_id=self.project_id)
        self.conn.execute("update goals set status = 'active' where id = ?", (goal_id,))
        self.conn.commit()

        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        self.tmp.cleanup()

    def test_print_sends_chat_rpc(self) -> None:
        """--print -p '你好' must send chat.send to Supervisor (fails today: flag unknown)."""
        result = _run_cli_with_home(self.home, "--print", "-p", "你好", cwd=self.repo)
        # Today: exit 2 (argparse). After: exit 0.
        requests = self.server.drain_requests()
        methods = [m for m, _ in requests]
        self.assertIn("chat.send", methods,
                      "chat.send not sent; got methods=%s, returncode=%d, stderr=%s"
                      % (methods, result.returncode, result.stderr[:200]))

    def test_print_output_contains_user_reply(self) -> None:
        """stdout should contain Commander user_reply text (fails today)."""
        result = _run_cli_with_home(self.home, "--print", "-p", "你好", cwd=self.repo)
        self.assertEqual(result.returncode, 0,
                         "expected exit 0, got %d; stderr=%s" % (result.returncode, result.stderr[:200]))
        self.assertIn("你好", result.stdout,
                      "user_reply not in stdout: %s" % result.stdout[:300])

    def test_print_no_admission_leak(self) -> None:
        """stdout must not contain admission-leak tokens (fails today)."""
        result = _run_cli_with_home(self.home, "--print", "-p", "你好", cwd=self.repo)
        for token in _ADMISSION_LEAK_TOKENS:
            self.assertNotIn(token, result.stdout,
                             "admission leak token %r in stdout" % token)

    def test_print_does_not_launch_tui(self) -> None:
        """--print must not call launch_tui (fails today: flag unknown)."""
        with mock.patch(
            "local_cli_coordinator.tui_launcher.launch_tui",
        ) as launch_mock:
            result = _run_cli_with_home(self.home, "--print", "-p", "你好", cwd=self.repo)
        launch_mock.assert_not_called()

    def test_json_mode_without_print_does_not_launch_tui(self) -> None:
        """--mode json without --print must stay headless for scriptability."""
        with mock.patch(
            "local_cli_coordinator.tui_launcher.launch_tui",
        ) as launch_mock:
            result = _run_cli_with_home(
                self.home, "--mode", "json", "-p", "/status", cwd=self.repo,
            )
        self.assertEqual(result.returncode, 0,
                         "expected exit 0, got %d; stderr=%s" % (result.returncode, result.stderr[:200]))
        launch_mock.assert_not_called()
        data = json.loads(result.stdout)
        self.assertTrue(data.get("ok"))


# ---------------------------------------------------------------------------
# 3. JSON envelope red tests
# ---------------------------------------------------------------------------

class CliPromptJsonRedTests(unittest.TestCase):
    """--mode json must produce a valid JSON envelope with required keys."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.repo = self.home / "repo"
        init_git_repo(self.repo)

        self.paths = RuntimePaths(self.home / "config", self.home / "data", self.home / "state")
        self.paths.create()

        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()

        self.project_id = self.conn.execute(
            "select id from projects where canonical_path = ?",
            (str(self.repo.resolve()),),
        ).fetchone()["id"]

        goal_id = create_goal(self.conn, "Test goal", "test acceptance", project_id=self.project_id)
        self.conn.execute("update goals set status = 'active' where id = ?", (goal_id,))
        self.conn.commit()

        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        self.tmp.cleanup()

    def test_json_envelope_has_required_keys(self) -> None:
        """--mode json stdout must parse as JSON with all required keys (fails today)."""
        result = _run_cli_with_home(self.home, "--print", "--mode", "json", "-p", "现在有什么任务？", cwd=self.repo)
        self.assertEqual(result.returncode, 0,
                         "expected exit 0, got %d; stderr=%s" % (result.returncode, result.stderr[:200]))
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail("stdout is not valid JSON: %r" % result.stdout[:500])
        required_keys = {
            "ok", "project_id", "goal_id", "user_reply", "intent",
            "admitted", "rejected", "accepted_task_ids", "error",
        }
        missing = required_keys - data.keys()
        self.assertFalse(missing, "missing JSON keys: %s" % missing)
        self.assertTrue(data.get("ok"), "ok should be True")

    def test_json_envelope_ok_true(self) -> None:
        """ok field must be True on success (fails today)."""
        result = _run_cli_with_home(self.home, "--print", "--mode", "json", "-p", "你好", cwd=self.repo)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIs(data["ok"], True)


# ---------------------------------------------------------------------------
# 4. Project red tests — unknown / unregistered repo
# ---------------------------------------------------------------------------

class CliPromptProjectRedTests(unittest.TestCase):
    """--print from unregistered repo must fail with clear error."""

    def test_unregistered_repo_fails(self) -> None:
        """--print from a non-git dir should fail (fails today: argparse)."""
        home = Path(tempfile.mkdtemp())
        not_a_repo = home / "not-a-repo"
        not_a_repo.mkdir()
        result = _run_cli_with_home(home, "--print", "-p", "hello", cwd=not_a_repo)
        self.assertNotEqual(result.returncode, 0,
                            "should fail for unregistered repo, got exit 0")

    def test_text_mode_error_emits_stderr(self) -> None:
        """Text mode failures must print error: <message> to stderr."""
        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name)
        repo = home / "repo"
        init_git_repo(repo)
        paths = RuntimePaths(home / "config", home / "data", home / "state")
        paths.create()
        try:
            result = _run_cli_with_home(home, "-p", "hello", cwd=repo)
            self.assertEqual(result.returncode, 1,
                             "expected exit 1, got %d; stderr=%s" % (result.returncode, result.stderr[:200]))
            self.assertEqual(result.stdout, "")
            self.assertTrue(
                result.stderr.startswith("error: "),
                "stderr should start with 'error: ', got %r" % result.stderr[:200],
            )
            self.assertIn("project not registered", result.stderr)
        finally:
            tmp.cleanup()


# ---------------------------------------------------------------------------
# 5. --continue red tests — goal binding
# ---------------------------------------------------------------------------

class CliContinueRedTests(unittest.TestCase):
    """--continue should bind to the latest non-terminal goal."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.repo = self.home / "repo"
        init_git_repo(self.repo)

        self.paths = RuntimePaths(self.home / "config", self.home / "data", self.home / "state")
        self.paths.create()

        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()

        self.project_id = self.conn.execute(
            "select id from projects where canonical_path = ?",
            (str(self.repo.resolve()),),
        ).fetchone()["id"]

        # Create two goals: one completed, one active
        old_goal = create_goal(self.conn, "Old goal", "done", project_id=self.project_id)
        self.conn.execute("update goals set status = 'completed' where id = ?", (old_goal,))
        self.active_goal = create_goal(self.conn, "Active goal", "current work", project_id=self.project_id)
        self.conn.execute("update goals set status = 'active' where id = ?", (self.active_goal,))
        self.conn.commit()

        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        self.tmp.cleanup()

    def test_continue_binds_to_active_goal(self) -> None:
        """--continue --print -p '下一步' should reference active goal (fails today)."""
        result = _run_cli_with_home(self.home, "--continue", "--print", "-p", "下一步", cwd=self.repo)
        # Today: exit 2 (argparse unknown --continue). After: exit 0.
        self.assertEqual(result.returncode, 0,
                         "expected exit 0, got %d; stderr=%s" % (result.returncode, result.stderr[:200]))

        requests = self.server.drain_requests()
        chat_requests = [p for m, p in requests if m == "chat.send"]
        self.assertTrue(chat_requests, "no chat.send request found")
        # goal_id in params should reference the active goal, not the completed one
        params = chat_requests[0]
        self.assertEqual(params.get("goal_id"), self.active_goal,
                         "should bind to active goal, got goal_id=%s" % params.get("goal_id"))


# ---------------------------------------------------------------------------
# 6. Config red tests — read-only config command
# ---------------------------------------------------------------------------

class CliConfigRedTests(unittest.TestCase):
    """config subcommand should display current configuration."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        config_dir = self.home / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "agents.toml").write_text(textwrap.dedent("""
            [agents.worker]
            command = "true"
            capabilities = ["code"]
            max_concurrency = 1
            role = "worker"
        """).strip())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_config_subcommand_exists(self) -> None:
        """coordinator config should exit 0 (fails today: unknown subcommand)."""
        result = _run_cli_with_home(self.home, "config")
        self.assertEqual(result.returncode, 0,
                         "expected exit 0, got %d; stderr=%s" % (result.returncode, result.stderr[:200]))

    def test_config_output_has_sections(self) -> None:
        """stdout should contain config sections (fails today)."""
        result = _run_cli_with_home(self.home, "config")
        self.assertEqual(result.returncode, 0)
        output = result.stdout.lower()
        # At least one of these section markers should appear
        has_section = any(kw in output for kw in ["agents", "repos", "runtime", "paths", "xdg"])
        self.assertTrue(has_section,
                        "config output missing expected sections: %s" % result.stdout[:300])


# ---------------------------------------------------------------------------
# 7. Legacy chat regression — existing path must not break
# ---------------------------------------------------------------------------

class CliLegacyChatRegressionTests(unittest.TestCase):
    """Existing 'coordinator chat' code path must remain reachable."""

    def test_chat_subcommand_parses(self) -> None:
        """'chat' subcommand must parse without error (guard — passes today)."""
        parser = build_parser()
        args = parser.parse_args(["chat"])
        self.assertEqual(args.command, "chat")

    def test_chat_subcommand_runs(self) -> None:
        """'coordinator chat' must be reachable (may prompt for input — check exit)."""
        home = Path(tempfile.mkdtemp())
        result = _run_cli_with_home(home, "chat")
        # chat may exit 0 (no input) or prompt — just verify it's not argparse error
        self.assertNotEqual(result.returncode, 2,
                            "chat subcommand hit argparse error — legacy path broken")
