"""Red tests for Phase 5.4 file context requirements.

These tests capture the contract for ``@file`` context tokens in the CLI
prompt parser, project-scoped validation at both CLI and Supervisor layers,
context manifest persistence with body redaction, and JSON output.

Owner: Claude Code (Task 0)
Expected before implementation: import/attribute failures for
``context_files``, ``context_file_tokens``, and context manifest handling.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from local_cli_coordinator.cli import build_prompt_parser, normalize_prompt_args
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.fixtures.fake_supervisor import FakeSupervisor
from tests.helpers import ROOT, SRC, init_git_repo


def _run_cli_with_home(
    home: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
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
# Parser unit tests: @file token separation
# ---------------------------------------------------------------------------


class ContextFileParserTests(unittest.TestCase):
    """Unit tests for ``@file`` token parsing in ``build_prompt_parser()``."""

    def _parse(self, argv: list[str]):
        parser = build_prompt_parser()
        args = parser.parse_args(argv)
        normalize_prompt_args(args)
        return args

    def test_file_tokens_are_separated_from_prompt(self):
        args = self._parse(["@README.md", "@docs/cli.md", "-p", "compare"])
        self.assertEqual(args.context_file_tokens, ["README.md", "docs/cli.md"])
        self.assertEqual(args.prompt_text, "compare")

    def test_double_at_escapes_literal_prompt(self):
        args = self._parse(["@@owner", "-p", "notify"])
        self.assertEqual(args.context_file_tokens, [])
        self.assertEqual(args.prompt_text, "@owner notify")

    def test_mixed_at_and_plain_positional(self):
        args = self._parse(["@src/main.py", "explain", "this", "-p", "code"])
        self.assertEqual(args.context_file_tokens, ["src/main.py"])
        self.assertEqual(args.prompt_text, "code explain this")

    def test_no_context_tokens_when_absent(self):
        args = self._parse(["-p", "hello world"])
        self.assertEqual(args.context_file_tokens, [])
        self.assertEqual(args.prompt_text, "hello world")

    def test_bare_at_sign_is_literal(self):
        args = self._parse(["@", "-p", "ping"])
        self.assertEqual(args.context_file_tokens, [])
        self.assertEqual(args.prompt_text, "@ ping")

    def test_prompt_flag_not_parsed_for_at(self):
        """The explicit ``-p`` string is never parsed for ``@file``."""
        args = self._parse(["-p", "@README.md compare"])
        self.assertEqual(args.context_file_tokens, [])
        self.assertEqual(args.prompt_text, "@README.md compare")


# ---------------------------------------------------------------------------
# Validation fixtures: create test files for boundary testing
# ---------------------------------------------------------------------------


def _create_fixtures(tmp: Path) -> dict[str, Path]:
    """Create fixture files and return a name-to-path mapping."""
    fixtures: dict[str, Path] = {}

    # Valid UTF-8 file
    valid = tmp / "valid.txt"
    valid.write_text("Hello, context!\n", encoding="utf-8")
    fixtures["valid"] = valid

    # Another valid file (for duplicate canonical path test)
    subdir = tmp / "subdir"
    subdir.mkdir()
    via_subdir = subdir / ".." / "valid.txt"
    fixtures["valid_dup"] = via_subdir  # resolves to same canonical path

    # Missing file
    fixtures["missing"] = tmp / "does_not_exist.txt"

    # Directory (not a file)
    fixtures["directory"] = subdir

    # NUL / binary file
    binary = tmp / "binary.bin"
    binary.write_bytes(b"before\x00after")
    fixtures["binary"] = binary

    # Exactly 128 KiB file (at limit)
    at_limit = tmp / "at_limit.txt"
    at_limit.write_bytes(b"A" * (128 * 1024))
    fixtures["at_limit"] = at_limit

    # 128 KiB + 1 byte (overflow)
    over_limit = tmp / "over_limit.txt"
    over_limit.write_bytes(b"B" * (128 * 1024 + 1))
    fixtures["over_limit"] = over_limit

    # 17 small files (exceeds MAX_CONTEXT_FILES = 16)
    many_dir = tmp / "many"
    many_dir.mkdir()
    for i in range(17):
        p = many_dir / f"f{i:02d}.txt"
        p.write_text(f"file {i}\n", encoding="utf-8")
    fixtures["many_dir"] = many_dir

    # 512 KiB aggregate overflow: four 128 KiB files
    agg_dir = tmp / "aggregate"
    agg_dir.mkdir()
    for i in range(4):
        p = agg_dir / f"part{i}.txt"
        p.write_bytes(b"X" * (128 * 1024))
    fixtures["agg_dir"] = agg_dir

    # Symlink pointing outside repo
    outside = tmp / "outside_repo.txt"
    outside.write_text("secret\n", encoding="utf-8")
    symlink = tmp / "escape_link.txt"
    symlink.symlink_to(outside)
    fixtures["symlink_outside"] = symlink
    fixtures["outside_target"] = outside

    return fixtures


# ---------------------------------------------------------------------------
# Validation unit tests: context_files loading
# ---------------------------------------------------------------------------


class ContextFileValidationTests(unittest.TestCase):
    """Unit tests for ``context_files.load_context_files()``."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo_root = self.tmp / "repo"
        self.repo_root.mkdir()
        self.fixtures = _create_fixtures(self.repo_root)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_utf8_file_accepted(self):
        from local_cli_coordinator.context_files import load_context_files

        result = load_context_files(
            self.repo_root, self.repo_root, ["valid.txt"]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].path, "valid.txt")
        self.assertIn("Hello", result[0].content)

    def test_duplicate_canonical_paths_deduplicated(self):
        from local_cli_coordinator.context_files import load_context_files

        result = load_context_files(
            self.repo_root, self.repo_root, ["valid.txt", "subdir/../valid.txt"]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].path, "valid.txt")

    def test_missing_file_rejected(self):
        from local_cli_coordinator.context_files import (
            ContextFileError,
            load_context_files,
        )

        with self.assertRaises(ContextFileError) as ctx:
            load_context_files(
                self.repo_root, self.repo_root, ["does_not_exist.txt"]
            )
        self.assertIn("not found", str(ctx.exception).lower())

    def test_directory_rejected(self):
        from local_cli_coordinator.context_files import (
            ContextFileError,
            load_context_files,
        )

        with self.assertRaises(ContextFileError) as ctx:
            load_context_files(self.repo_root, self.repo_root, ["subdir"])
        self.assertIn("not a file", str(ctx.exception).lower())

    def test_nul_binary_rejected(self):
        from local_cli_coordinator.context_files import (
            ContextFileError,
            load_context_files,
        )

        with self.assertRaises(ContextFileError) as ctx:
            load_context_files(
                self.repo_root, self.repo_root, ["binary.bin"]
            )
        self.assertIn("binary", str(ctx.exception).lower())

    def test_single_file_128k_overflow_rejected(self):
        from local_cli_coordinator.context_files import (
            ContextFileError,
            load_context_files,
        )

        with self.assertRaises(ContextFileError) as ctx:
            load_context_files(
                self.repo_root, self.repo_root, ["over_limit.txt"]
            )
        self.assertIn("128", str(ctx.exception))

    def test_seventeen_files_rejected(self):
        from local_cli_coordinator.context_files import (
            ContextFileError,
            load_context_files,
        )

        tokens = [f"many/f{i:02d}.txt" for i in range(17)]
        with self.assertRaises(ContextFileError) as ctx:
            load_context_files(self.repo_root, self.repo_root, tokens)
        self.assertIn("16", str(ctx.exception))

    def test_aggregate_512k_overflow_rejected(self):
        from local_cli_coordinator.context_files import (
            ContextFileError,
            load_context_files,
        )

        tokens = [f"aggregate/part{i}.txt" for i in range(4)]
        with self.assertRaises(ContextFileError) as ctx:
            load_context_files(self.repo_root, self.repo_root, tokens)
        self.assertIn("512", str(ctx.exception))

    def test_parent_traversal_escape_rejected(self):
        from local_cli_coordinator.context_files import (
            ContextFileError,
            load_context_files,
        )

        with self.assertRaises(ContextFileError) as ctx:
            load_context_files(
                self.repo_root, self.repo_root, ["../outside_repo.txt"]
            )
        self.assertIn("outside", str(ctx.exception).lower())

    def test_symlink_escape_rejected(self):
        from local_cli_coordinator.context_files import (
            ContextFileError,
            load_context_files,
        )

        with self.assertRaises(ContextFileError) as ctx:
            load_context_files(
                self.repo_root, self.repo_root, ["escape_link.txt"]
            )
        self.assertIn("outside", str(ctx.exception).lower())

    def test_sha256_computed(self):
        from local_cli_coordinator.context_files import load_context_files

        result = load_context_files(
            self.repo_root, self.repo_root, ["valid.txt"]
        )
        expected = hashlib.sha256(b"Hello, context!\n").hexdigest()
        self.assertEqual(result[0].sha256, expected)


# ---------------------------------------------------------------------------
# Supervisor boundary test: double validation
# ---------------------------------------------------------------------------


class SupervisorBoundaryContextTests(unittest.TestCase):
    """The Supervisor must independently revalidate context files.

    Sending a crafted ``context_files`` list directly via RPC with a path
    traversal attack must be rejected before Commander invocation.
    """

    def setUp(self):
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
        proj = inspect_project(self.repo)
        register_project(self.conn, proj, confirmed=True)
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]
        goal_id = create_goal(
            self.conn, "Test goal", "test acceptance",
            project_id=self.project_id,
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
        self.goal_id = goal_id
        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_path_traversal_in_context_files_rejected(self):
        """Crafted ``context_files`` with ``../`` must be rejected."""
        # The Supervisor must validate context_files independently.
        # A direct RPC call with a traversal path should fail.
        result = _run_cli_with_home(
            self.home,
            "--root",
            str(self.repo),
            "@../etc/passwd",
            "-p",
            "read this",
            "--mode",
            "json",
        )
        # Should fail with a validation error, not pass through to Commander
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("context", combined.lower())


# ---------------------------------------------------------------------------
# Persistence tests: body redaction and manifest metadata
# ---------------------------------------------------------------------------


class ContextManifestPersistenceTests(unittest.TestCase):
    """Verify that file bodies are redacted from persisted storage.

    The Commander prompt must contain file content between delimiters during
    execution, but the persisted ``prompt.md`` must contain only a
    metadata-only manifest.  The user message stored in the chat must list
    filenames but not bodies.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)
        self.secret_body = "TOP_SECRET_CONTENT_12345"
        secret = self.repo / "secret.txt"
        secret.write_text(self.secret_body, encoding="utf-8")
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        proj = inspect_project(self.repo)
        register_project(self.conn, proj, confirmed=True)
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]
        goal_id = create_goal(
            self.conn, "Test goal", "test acceptance",
            project_id=self.project_id,
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
        self.goal_id = goal_id

    def tearDown(self):
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_secret_body_not_in_user_message(self):
        """Chat user message must list filenames, not bodies."""
        # After a chat with @file, the persisted user message should be:
        #   <operator text>\n\n[context files: secret.txt]
        # The actual file body must not appear.
        from local_cli_coordinator.goals import list_commander_messages

        messages = list_commander_messages(self.conn, self.goal_id)
        # Find the user message
        user_msgs = [m for m in messages if m["role"] == "user"]
        if user_msgs:
            self.assertNotIn(self.secret_body, user_msgs[-1]["content"])
            self.assertIn("secret.txt", user_msgs[-1]["content"])

    def test_secret_body_not_in_run_manifest(self):
        """Commander run manifest must contain metadata only, no bodies."""
        row = self.conn.execute(
            "SELECT context_manifest FROM commander_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            manifest = row["context_manifest"]
            self.assertNotIn(self.secret_body, manifest)
            parsed = json.loads(manifest)
            self.assertIsInstance(parsed, list)
            if parsed:
                self.assertIn("sha256", parsed[0])
                self.assertIn("path", parsed[0])
                self.assertNotIn("content", parsed[0])

    def test_sha256_present_in_manifest(self):
        """Each manifest entry must include a ``sha256`` field."""
        row = self.conn.execute(
            "SELECT context_manifest FROM commander_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            manifest = json.loads(row["context_manifest"])
            for entry in manifest:
                self.assertIn("sha256", entry)
                self.assertEqual(len(entry["sha256"]), 64)

    def test_prompt_md_redacted_after_finish(self):
        """The persisted ``prompt.md`` must contain the manifest, not bodies."""
        # Look for the prompt.md artifact in the run directory
        row = self.conn.execute(
            "SELECT id, goal_id FROM commander_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            run_id = row["id"]
            goal_id = row["goal_id"]
            prompt_path = (
                self.repo
                / "runs"
                / "commander"
                / str(goal_id)
                / str(run_id)
                / "prompt.md"
            )
            if prompt_path.exists():
                content = prompt_path.read_text(encoding="utf-8")
                self.assertNotIn(self.secret_body, content)


# ---------------------------------------------------------------------------
# JSON output tests
# ---------------------------------------------------------------------------


class ContextFileJsonOutputTests(unittest.TestCase):
    """Verify that ``context_files`` metadata appears in JSON output."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        init_git_repo(self.repo)
        (self.repo / "README.md").write_text("# Test\n", encoding="utf-8")
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        proj = inspect_project(self.repo)
        register_project(self.conn, proj, confirmed=True)
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]
        goal_id = create_goal(
            self.conn, "Test goal", "test acceptance",
            project_id=self.project_id,
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
        self.goal_id = goal_id
        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_json_output_includes_context_files(self):
        """``--mode json`` output must include a ``context_files`` list."""
        result = _run_cli_with_home(
            self.home,
            "--root",
            str(self.repo),
            "@README.md",
            "-p",
            "summarize",
            "--mode",
            "json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("context_files", data)
        self.assertIsInstance(data["context_files"], list)
        if data["context_files"]:
            entry = data["context_files"][0]
            self.assertIn("path", entry)
            self.assertIn("sha256", entry)
            self.assertNotIn("content", entry)


if __name__ == "__main__":
    unittest.main()
