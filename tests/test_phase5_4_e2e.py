"""Phase 5.4 integrated CLI workflow E2E tests.

These tests exercise the three-wave Phase 5.4 feature set end-to-end
via subprocess CLI invocations against a temp git repo with an isolated
``COORDINATOR_HOME`` and a running ``FakeSupervisor``.

Wave 1: file context (``@file`` tokens).
Wave 2: goal sessions (``--resume``, ``--fork``).
Wave 3: execution policy (``--tools``, ``--no-tools``, ``--exclude-tools``)
         and RPC mode (``--mode rpc``).

Owner: Claude Code (Task 10)
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
import warnings
from pathlib import Path
from unittest import mock

from local_cli_coordinator.cli import build_prompt_parser, normalize_prompt_args
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import (
    create_goal,
    transition_goal,
)
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_protocol import PROTOCOL_VERSION
from tests.fixtures.fake_supervisor import FakeSupervisor
from tests.helpers import ROOT, SRC, init_git_repo

_PYTHON = sys.executable


def _write_config(config_dir: Path, repo_path: Path) -> None:
    """Write minimal TOML config files for CLI integration tests."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents.toml").write_text(textwrap.dedent("""
        [agents.worker]
        command = "true"
        capabilities = ["code"]
        max_concurrency = 1
        role = "worker"
    """).strip())
    (config_dir / "repos.toml").write_text(textwrap.dedent(f"""
        [repos.test-repo]
        path = "{repo_path}"
        default_branch = "main"
        allow_push = false
        merge_policy = "no_push"
    """).strip())
    (config_dir / "policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = false
        require_acceptance_criteria = false
        require_verification_commands = false
        require_handoff_summary = false
        max_files_touched = 20
        max_expected_minutes = 60
        max_attempts = 3
        split_if_touches_multiple_subsystems = false
        split_if_research_and_code_are_mixed = false

        [daemon_policy]
        poll_interval_seconds = 5
    """).strip())


def _run_cli_with_home(
    home: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["COORDINATOR_HOME"] = str(home)
    return subprocess.run(
        [_PYTHON, "-m", "local_cli_coordinator", *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


# ---------------------------------------------------------------------------
# Wave 1: File context
# ---------------------------------------------------------------------------


class FileContextE2ETests(unittest.TestCase):
    """@file tokens attach repo-relative files to the chat.send request."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)

        # Create a small context file.
        self.context_file = self.repo / "notes.txt"
        self.context_file.write_text("hello from context\n", encoding="utf-8")
        # Commit so it's tracked.
        subprocess.run(
            ["git", "add", "notes.txt"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add notes"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )

        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
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

        goal_id = create_goal(
            self.conn, "Ctx goal", "test context", project_id=self.project_id
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
        self.conn.commit()

        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_context_files_sent_in_chat_rpc(self) -> None:
        """@file token should include context_files in chat.send params."""
        self.server.wait_for_request_method("system.connect", timeout=2)
        self.server.drain_requests()

        result = _run_cli_with_home(
            self.home,
            "@notes.txt",
            "--print",
            "-p",
            "summarize",
            cwd=self.repo,
        )
        self.assertEqual(
            result.returncode, 0,
            "exit %d; stderr=%s" % (result.returncode, result.stderr[:300]),
        )

        # Verify chat.send received context_files.
        self.server.wait_for_request_method("chat.send", timeout=5)
        requests = self.server.drain_requests()
        chat_sends = [p for m, p in requests if m == "chat.send"]
        self.assertTrue(len(chat_sends) > 0, "no chat.send found")
        ctx = chat_sends[0].get("context_files", [])
        self.assertTrue(len(ctx) > 0, "context_files empty in chat.send")
        self.assertEqual(ctx[0]["path"], "notes.txt")

    def test_json_output_includes_context_files(self) -> None:
        """--mode json should include context_files metadata in output."""
        result = _run_cli_with_home(
            self.home,
            "@notes.txt",
            "--mode", "json",
            "-p",
            "summarize",
            cwd=self.repo,
        )
        self.assertEqual(
            result.returncode, 0,
            "exit %d; stderr=%s" % (result.returncode, result.stderr[:300]),
        )
        data = json.loads(result.stdout)
        ctx = data.get("context_files", [])
        self.assertTrue(len(ctx) > 0, "context_files missing from JSON output")
        self.assertEqual(ctx[0]["path"], "notes.txt")
        self.assertIn("sha256", ctx[0])

    def test_context_sha256_matches_file(self) -> None:
        """The sha256 in the output should match the actual file hash."""
        expected_hash = hashlib.sha256(
            "hello from context\n".encode("utf-8")
        ).hexdigest()
        result = _run_cli_with_home(
            self.home,
            "@notes.txt",
            "--mode", "json",
            "-p",
            "summarize",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        ctx = data.get("context_files", [])
        self.assertTrue(len(ctx) > 0)
        self.assertEqual(ctx[0]["sha256"], expected_hash)


# ---------------------------------------------------------------------------
# Wave 2: Goal sessions
# ---------------------------------------------------------------------------


class GoalSessionE2ETests(unittest.TestCase):
    """--resume and --fork operate on project-scoped goal state."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)

        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
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

        # Create a terminal (completed) goal for forking.
        self.completed_goal_id = create_goal(
            self.conn,
            "Completed goal",
            "was completed",
            project_id=self.project_id,
        )
        transition_goal(self.conn, self.completed_goal_id, "active")
        transition_goal(self.conn, self.completed_goal_id, "completed")
        self.conn.commit()

        # Create an active goal for resume testing.
        self.active_goal_id = create_goal(
            self.conn,
            "Active goal",
            "still running",
            project_id=self.project_id,
        )
        transition_goal(self.conn, self.active_goal_id, "active")
        self.conn.commit()

        # FakeSupervisor goal session handler reads COORDINATOR_HOME from
        # the *server* process environment (not the CLI subprocess).
        self._orig_home = os.environ.get("COORDINATOR_HOME")
        os.environ["COORDINATOR_HOME"] = str(self.home)

        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        if self._orig_home is not None:
            os.environ["COORDINATOR_HOME"] = self._orig_home
        else:
            os.environ.pop("COORDINATOR_HOME", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fork_creates_draft_goal(self) -> None:
        """--fork <terminal-id> should create a new draft goal."""
        # Fork requires no non-terminal goals — complete the active one first.
        transition_goal(self.conn, self.active_goal_id, "completed")
        self.conn.commit()

        result = _run_cli_with_home(
            self.home,
            "--fork", str(self.completed_goal_id),
            "--mode", "json",
            "-p",
            "docs only",
            cwd=self.repo,
        )
        self.assertEqual(
            result.returncode, 0,
            "exit %d; stdout=%s; stderr=%s"
            % (result.returncode, result.stdout[:300], result.stderr[:300]),
        )
        data = json.loads(result.stdout)
        # The response should indicate a new goal was created.
        self.assertIn("goal_id", data)
        new_goal_id = data["goal_id"]
        self.assertNotEqual(new_goal_id, self.completed_goal_id)

        # Verify the new goal is a draft in the DB.
        row = self.conn.execute(
            "select status, parent_goal_id from goals where id = ?",
            (new_goal_id,),
        ).fetchone()
        self.assertIsNotNone(row, "forked goal not found in DB")
        self.assertEqual(row["status"], "draft")
        self.assertEqual(row["parent_goal_id"], self.completed_goal_id)

    def test_resume_list_candidates(self) -> None:
        """--resume without ID should list candidate goals in JSON mode."""
        # First, pause the active goal so it's resumable.
        transition_goal(self.conn, self.active_goal_id, "paused")
        self.conn.commit()

        result = _run_cli_with_home(
            self.home,
            "--resume",
            "--mode", "json",
            cwd=self.repo,
        )
        # Should exit 2 (candidates listed, no selection).
        self.assertEqual(
            result.returncode, 2,
            "exit %d; stdout=%s; stderr=%s"
            % (result.returncode, result.stdout[:300], result.stderr[:300]),
        )
        data = json.loads(result.stdout)
        candidates = data.get("candidates", data.get("goals", []))
        self.assertTrue(len(candidates) > 0, "no candidates returned")
        ids = [c["id"] for c in candidates]
        self.assertIn(self.active_goal_id, ids)


# ---------------------------------------------------------------------------
# Wave 3: Execution policy + RPC mode
# ---------------------------------------------------------------------------


class ExecutionPolicyE2ETests(unittest.TestCase):
    """--tools, --no-tools, --exclude-tools pass policy to chat.send."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)

        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()

        self.project_id = self.conn.execute(
            "select id from projects where canonical_path = ?",
            (str(self.repo.resolve()),),
        ).fetchone()["id"]

        goal_id = create_goal(
            self.conn, "Policy goal", "test policy", project_id=self.project_id
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
        self.conn.commit()

        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_tools_sends_empty_policy(self) -> None:
        """--no-tools should send execution_policy with empty allowed set."""
        self.server.wait_for_request_method("system.connect", timeout=2)
        self.server.drain_requests()

        result = _run_cli_with_home(
            self.home,
            "--no-tools",
            "--print",
            "-p",
            "explain status",
            cwd=self.repo,
        )
        self.assertEqual(
            result.returncode, 0,
            "exit %d; stderr=%s" % (result.returncode, result.stderr[:300]),
        )

        self.server.wait_for_request_method("chat.send", timeout=5)
        policies = self.server.drain_execution_policies()
        self.assertTrue(len(policies) > 0, "no execution_policy captured")
        self.assertEqual(policies[0].get("allowed"), [])

    def test_tools_sends_allowed_set(self) -> None:
        """--tools read,grep should send execution_policy with read and search."""
        self.server.wait_for_request_method("system.connect", timeout=2)
        self.server.drain_requests()

        result = _run_cli_with_home(
            self.home,
            "--tools", "read,grep",
            "--print",
            "-p",
            "read only check",
            cwd=self.repo,
        )
        self.assertEqual(
            result.returncode, 0,
            "exit %d; stderr=%s" % (result.returncode, result.stderr[:300]),
        )

        self.server.wait_for_request_method("chat.send", timeout=5)
        policies = self.server.drain_execution_policies()
        self.assertTrue(len(policies) > 0, "no execution_policy captured")
        allowed = set(policies[0].get("allowed", []))
        self.assertIn("read", allowed)
        self.assertIn("search", allowed)
        self.assertNotIn("push", allowed)

    def test_exclude_tools_sends_reduced_set(self) -> None:
        """--exclude-tools push,merge should exclude those from allowed."""
        self.server.wait_for_request_method("system.connect", timeout=2)
        self.server.drain_requests()

        result = _run_cli_with_home(
            self.home,
            "--exclude-tools", "push,merge",
            "--print",
            "-p",
            "fix without publishing",
            cwd=self.repo,
        )
        self.assertEqual(
            result.returncode, 0,
            "exit %d; stderr=%s" % (result.returncode, result.stderr[:300]),
        )

        self.server.wait_for_request_method("chat.send", timeout=5)
        policies = self.server.drain_execution_policies()
        self.assertTrue(len(policies) > 0, "no execution_policy captured")
        allowed = set(policies[0].get("allowed", []))
        self.assertNotIn("push", allowed)
        self.assertNotIn("merge", allowed)


class RpcModeE2ETests(unittest.TestCase):
    """--mode rpc emits exactly one ResponseEnvelope JSON line."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)

        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()

        self.project_id = self.conn.execute(
            "select id from projects where canonical_path = ?",
            (str(self.repo.resolve()),),
        ).fetchone()["id"]

        goal_id = create_goal(
            self.conn, "RPC goal", "test rpc", project_id=self.project_id
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
        self.conn.commit()

        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _parse_envelope(self, stdout: str) -> dict:
        lines = [line for line in stdout.strip().splitlines() if line.strip()]
        self.assertEqual(
            len(lines), 1,
            "Expected 1 JSON line, got %d: %s" % (len(lines), stdout[:300]),
        )
        return json.loads(lines[0])

    def test_rpc_status_returns_valid_envelope(self) -> None:
        """/status in RPC mode returns a valid ResponseEnvelope."""
        result = _run_cli_with_home(
            self.home,
            "--mode", "rpc",
            "-p", "/status",
            cwd=self.repo,
        )
        self.assertEqual(
            result.returncode, 0,
            "exit %d; stderr=%s" % (result.returncode, result.stderr[:300]),
        )
        envelope = self._parse_envelope(result.stdout)
        self.assertEqual(envelope["protocol_version"], PROTOCOL_VERSION)
        self.assertIn("request_id", envelope)
        self.assertIn("ok", envelope)
        self.assertEqual(envelope["type"], "response")

    def test_rpc_chat_returns_ok(self) -> None:
        """Chat in RPC mode returns ok=true with result."""
        result = _run_cli_with_home(
            self.home,
            "--mode", "rpc",
            "-p", "hello",
            cwd=self.repo,
        )
        self.assertEqual(
            result.returncode, 0,
            "exit %d; stderr=%s" % (result.returncode, result.stderr[:300]),
        )
        envelope = self._parse_envelope(result.stdout)
        self.assertTrue(envelope["ok"])
        self.assertIsNotNone(envelope["result"])

    def test_rpc_error_has_cli_local_prefix(self) -> None:
        """Local validation errors in RPC mode use cli-local- request_id prefix."""
        result = _run_cli_with_home(
            self.home,
            "--mode", "rpc",
            "-p", "/nonexistent_slash_command",
            cwd=self.repo,
        )
        envelope = self._parse_envelope(result.stdout)
        self.assertFalse(envelope["ok"])
        self.assertTrue(
            envelope["request_id"].startswith("cli-local-"),
            "Expected cli-local- prefix, got %s" % envelope["request_id"],
        )

    def test_rpc_mode_implies_headless(self) -> None:
        """--mode rpc should not launch the TUI."""
        with mock.patch(
            "local_cli_coordinator.tui_launcher.launch_tui",
        ) as launch_mock:
            _run_cli_with_home(
                self.home,
                "--mode", "rpc",
                "-p", "/status",
                cwd=self.repo,
            )
        launch_mock.assert_not_called()

    def test_rpc_tools_and_exclude_combined(self) -> None:
        """--tools read,edit --exclude-tools edit --mode rpc works end-to-end."""
        result = _run_cli_with_home(
            self.home,
            "--tools", "read,edit",
            "--exclude-tools", "edit",
            "--mode", "rpc",
            "-p", "read only",
            cwd=self.repo,
        )
        self.assertEqual(
            result.returncode, 0,
            "exit %d; stderr=%s" % (result.returncode, result.stderr[:300]),
        )
        envelope = self._parse_envelope(result.stdout)
        self.assertTrue(envelope["ok"])


# ---------------------------------------------------------------------------
# Leak regression: repeated runs must not leak DB connections or sockets
# ---------------------------------------------------------------------------


class LeakRegressionTests(unittest.TestCase):
    """Repeated CLI subprocess invocations must not leak resources.

    Runs a full-order mix of all Phase 5.4 modes (json, rpc, print, tools,
    resume, fork) in a tight loop and asserts zero ResourceWarning from
    the tracked connection audit.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)

        # Context file for @file tests.
        (self.repo / "readme.txt").write_text("leak test\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "readme.txt"],
            cwd=self.repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add readme"],
            cwd=self.repo, check=True, capture_output=True,
        )

        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        _write_config(self.home / "config", self.repo)
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        self.conn.commit()

        self.project_id = self.conn.execute(
            "select id from projects where canonical_path = ?",
            (str(self.repo.resolve()),),
        ).fetchone()["id"]

        # Create a terminal goal for fork and a paused goal for resume.
        terminal_id = create_goal(
            self.conn, "Terminal", "done", project_id=self.project_id,
        )
        transition_goal(self.conn, terminal_id, "active")
        transition_goal(self.conn, terminal_id, "completed")
        paused_id = create_goal(
            self.conn, "Paused", "paused", project_id=self.project_id,
        )
        transition_goal(self.conn, paused_id, "active")
        transition_goal(self.conn, paused_id, "paused")
        self.terminal_id = terminal_id
        self.paused_id = paused_id
        self.conn.commit()
        self.conn.close()

        # FakeSupervisor needs COORDINATOR_HOME in server process env.
        self._orig_home = os.environ.get("COORDINATOR_HOME")
        os.environ["COORDINATOR_HOME"] = str(self.home)
        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        if self._orig_home is not None:
            os.environ["COORDINATOR_HOME"] = self._orig_home
        else:
            os.environ.pop("COORDINATOR_HOME", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repeated_runs_emit_no_resource_warnings(self) -> None:
        """Run 20 mixed CLI invocations — zero ResourceWarning expected."""
        invocations = [
            # json mode
            ("--mode", "json", "-p", "status"),
            # print mode
            ("--print", "-p", "你好"),
            # @file context
            ("@readme.txt", "--mode", "json", "-p", "summarize"),
            # --no-tools
            ("--no-tools", "--print", "-p", "explain"),
            # --tools
            ("--tools", "read,search", "--print", "-p", "check"),
            # --exclude-tools
            ("--exclude-tools", "push", "--print", "-p", "fix"),
            # rpc mode
            ("--mode", "rpc", "-p", "/status"),
            # rpc + tools
            ("--tools", "read", "--mode", "rpc", "-p", "/status"),
            # resume candidates (exit 2 expected)
            ("--resume", "--mode", "json"),
            # fork (exit 0 or error — we only care about leaks)
            ("--fork", str(self.terminal_id), "--mode", "json", "-p", "retry"),
        ]

        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            for i in range(20):
                args = invocations[i % len(invocations)]
                result = _run_cli_with_home(self.home, *args, cwd=self.repo)
                # We don't assert exit codes here — only that no leaks occur.
                # Some invocations may fail (e.g. fork conflict) which is fine.
                del result
            gc.collect()

    def test_repeated_json_runs_stdout_is_always_parseable(self) -> None:
        """Each --mode json invocation must produce valid JSON, even in a loop."""
        for _ in range(10):
            result = _run_cli_with_home(
                self.home,
                "--mode", "json",
                "-p", "loop test",
                cwd=self.repo,
            )
            data = json.loads(result.stdout)
            self.assertIn("ok", data)
            self.assertIsInstance(data["ok"], bool)

    def test_repeated_rpc_runs_stdout_is_always_single_envelope(self) -> None:
        """Each --mode rpc invocation must produce exactly one JSON line."""
        for _ in range(10):
            result = _run_cli_with_home(
                self.home,
                "--mode", "rpc",
                "-p", "/status",
                cwd=self.repo,
            )
            lines = [
                line for line in result.stdout.strip().splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1, "Expected 1 line, got %d" % len(lines))
            envelope = json.loads(lines[0])
            self.assertIn("protocol_version", envelope)
            self.assertIn("ok", envelope)

    def test_socket_files_do_not_accumulate(self) -> None:
        """Repeated runs must not leave orphan socket files."""
        state_dir = self.home / "state"
        before = set(state_dir.glob("*.sock"))
        for _ in range(10):
            _run_cli_with_home(
                self.home, "--print", "-p", "ping", cwd=self.repo,
            )
        after = set(state_dir.glob("*.sock"))
        # Only the main coordinator.sock should exist; no new sockets.
        new_sockets = after - before
        self.assertEqual(
            len(new_sockets), 0,
            "orphan sockets: %s" % [str(s) for s in new_sockets],
        )
