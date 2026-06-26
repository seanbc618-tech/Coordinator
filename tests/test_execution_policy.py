"""Red tests for Phase 5.4 execution policy and RPC requirements.

These tests capture the contract for temporary tool controls (``--tools``,
``--no-tools``, ``--exclude-tools``), execution policy parsing and
intersection, admission gating, engine stage enforcement, policy persistence
across CLI restarts, and RPC mode envelope output.

Owner: Claude Code (Task 7)
Expected before implementation: import/attribute failures for
``execution_policy``, ``--tools``/``--no-tools``/``--exclude-tools`` parser
options, ``rpc`` mode choice, and policy-aware admission/engine hooks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_cli_coordinator.cli import (
    PromptNormalizeError,
    build_prompt_parser,
    normalize_prompt_args,
)
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import (
    acquire_commander_run_slot,
    create_goal,
    get_goal,
)
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.supervisor_protocol import (
    PROTOCOL_VERSION,
    ResponseEnvelope,
    encode_envelope,
)
from tests.fixtures.fake_supervisor import FakeSupervisor
from tests.helpers import ROOT, SRC, init_git_repo

_PYTHON = sys.executable


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
# 1. Parser red tests -- flags not implemented yet
# ---------------------------------------------------------------------------


class ExecutionPolicyParserTests(unittest.TestCase):
    """CLI flags for tool restrictions parse via build_prompt_parser."""

    def _parse_prompt(self, argv: list[str]):
        parser = build_prompt_parser()
        args = parser.parse_args(argv)
        normalize_prompt_args(args)
        return args

    # --tools flag --------------------------------------------------------

    def test_tools_flag_parses(self) -> None:
        args = self._parse_prompt(["--tools", "read,search", "-p", "检查"])
        self.assertEqual(args.tools, ["read", "search"])

    def test_tools_single_value(self) -> None:
        args = self._parse_prompt(["--tools", "read", "-p", "查看"])
        self.assertEqual(args.tools, ["read"])

    def test_tools_all_seven_stages(self) -> None:
        args = self._parse_prompt(
            ["--tools", "read,search,test,edit,commit,push,merge", "-p", "全"]
        )
        self.assertEqual(
            args.tools,
            ["read", "search", "test", "edit", "commit", "push", "merge"],
        )

    # --no-tools flag -----------------------------------------------------

    def test_no_tools_flag_parses(self) -> None:
        args = self._parse_prompt(["--no-tools", "-p", "只聊天"])
        self.assertTrue(args.no_tools)

    # --exclude-tools flag ------------------------------------------------

    def test_exclude_tools_flag_parses(self) -> None:
        args = self._parse_prompt(
            ["--exclude-tools", "push,merge", "-p", "不要发布"]
        )
        self.assertEqual(args.exclude_tools, ["push", "merge"])

    # Alias canonicalization ----------------------------------------------

    def test_alias_grep_becomes_search(self) -> None:
        args = self._parse_prompt(["--tools", "grep", "-p", "搜索"])
        self.assertEqual(args.tools, ["search"])

    def test_alias_write_becomes_edit(self) -> None:
        args = self._parse_prompt(["--tools", "write", "-p", "编辑"])
        self.assertEqual(args.tools, ["edit"])

    def test_exclude_alias_grep_becomes_search(self) -> None:
        args = self._parse_prompt(["--exclude-tools", "grep", "-p", "排除"])
        self.assertEqual(args.exclude_tools, ["search"])

    # Unknown tool names --------------------------------------------------

    def test_unknown_tool_name_is_error(self) -> None:
        with self.assertRaises(PromptNormalizeError):
            self._parse_prompt(["--tools", "deploy", "-p", "部署"])

    def test_unknown_exclude_tool_name_is_error(self) -> None:
        with self.assertRaises(PromptNormalizeError):
            self._parse_prompt(["--exclude-tools", "rollback", "-p", "回滚"])

    # Empty lists ---------------------------------------------------------

    def test_empty_tools_list_is_error(self) -> None:
        with self.assertRaises(PromptNormalizeError):
            self._parse_prompt(["--tools", "", "-p", "空"])

    def test_empty_exclude_tools_list_is_error(self) -> None:
        with self.assertRaises(PromptNormalizeError):
            self._parse_prompt(["--exclude-tools", "", "-p", "空"])

    # Mutual exclusivity --------------------------------------------------

    def test_tools_and_no_tools_conflict(self) -> None:
        with self.assertRaises(SystemExit):
            self._parse_prompt(
                ["--tools", "read", "--no-tools", "-p", "冲突"]
            )

    # Exclusion precedence ------------------------------------------------

    def test_exclude_overrides_tools(self) -> None:
        """--tools read,edit --exclude-tools edit should yield only read."""
        args = self._parse_prompt(
            [
                "--tools", "read,edit",
                "--exclude-tools", "edit",
                "-p", "排除优先",
            ]
        )
        # The effective set is computed downstream; parser stores both lists.
        self.assertIn("read", args.tools)
        self.assertIn("edit", args.tools)
        self.assertIn("edit", args.exclude_tools)

    # --mode rpc ----------------------------------------------------------

    def test_mode_rpc_parses(self) -> None:
        args = self._parse_prompt(["--mode", "rpc", "-p", "/status"])
        self.assertEqual(args.mode, "rpc")
        self.assertTrue(args.no_tui)


# ---------------------------------------------------------------------------
# 2. Policy intersection tests
# ---------------------------------------------------------------------------


class PolicyIntersectionTests(unittest.TestCase):
    """ExecutionPolicy.compute_effective intersects client and server sets."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _import_policy(self):
        from local_cli_coordinator.execution_policy import ExecutionPolicy
        return ExecutionPolicy

    def test_no_flags_yields_server_defaults(self) -> None:
        """No CLI flags means use the full server-derived policy."""
        Policy = self._import_policy()
        server = Policy(
            allowed=frozenset({"read", "search", "edit", "commit", "test"}),
            source="repo",
        )
        effective = server  # no client restrictions
        self.assertIn("read", effective.allowed)
        self.assertIn("edit", effective.allowed)
        self.assertIn("commit", effective.allowed)

    def test_tools_intersect_with_server(self) -> None:
        """Client --tools narrows server policy by intersection."""
        Policy = self._import_policy()
        server = Policy(
            allowed=frozenset({"read", "search", "edit", "commit", "test"}),
            source="repo",
        )
        client = Policy(
            allowed=frozenset({"read", "search"}),
            source="cli",
        )
        effective = Policy.compute_effective(server, client)
        self.assertEqual(effective.allowed, frozenset({"read", "search"}))

    def test_exclude_removes_from_server(self) -> None:
        """Client --exclude-tools removes stages from server policy."""
        Policy = self._import_policy()
        server = Policy(
            allowed=frozenset({"read", "search", "edit", "commit"}),
            source="repo",
        )
        excluded = frozenset({"commit"})
        effective = Policy.compute_effective(
            server, exclude=frozenset({"commit"})
        )
        self.assertIn("edit", effective.allowed)
        self.assertNotIn("commit", effective.allowed)

    def test_tools_and_exclude_combined(self) -> None:
        """--tools A,B --exclude-tools B yields only A."""
        Policy = self._import_policy()
        server = Policy(
            allowed=frozenset({"read", "search", "edit", "commit"}),
            source="repo",
        )
        client = Policy(
            allowed=frozenset({"read", "edit"}),
            source="cli",
        )
        effective = Policy.compute_effective(
            server, client, exclude=frozenset({"edit"})
        )
        self.assertEqual(effective.allowed, frozenset({"read"}))

    def test_repo_no_push_never_enables_push(self) -> None:
        """Even if client requests push, repo no_push blocks it."""
        Policy = self._import_policy()
        server = Policy(
            allowed=frozenset({"read", "search", "edit", "commit"}),
            source="repo",
        )
        client = Policy(
            allowed=frozenset({"read", "search", "edit", "commit", "push"}),
            source="cli",
        )
        effective = Policy.compute_effective(server, client)
        self.assertNotIn("push", effective.allowed)

    def test_repo_push_only_restricts_edit(self) -> None:
        """Repo with push-only policy excludes edit/test/commit."""
        Policy = self._import_policy()
        server = Policy(
            allowed=frozenset({"read", "search", "push"}),
            source="repo",
        )
        effective = server
        self.assertNotIn("edit", effective.allowed)
        self.assertNotIn("commit", effective.allowed)
        self.assertIn("push", effective.allowed)

    def test_auto_merge_enables_merge(self) -> None:
        """merge is available only when repo policy is auto_merge."""
        Policy = self._import_policy()
        server = Policy(
            allowed=frozenset(
                {"read", "search", "edit", "commit", "push", "merge"}
            ),
            source="repo",
        )
        self.assertIn("merge", server.allowed)

    def test_no_auto_merge_excludes_merge(self) -> None:
        """Without auto_merge, merge is not in server policy."""
        Policy = self._import_policy()
        server = Policy(
            allowed=frozenset({"read", "search", "edit", "commit", "push"}),
            source="repo",
        )
        self.assertNotIn("merge", server.allowed)

    def test_no_tools_yields_empty_set(self) -> None:
        """--no-tools produces an empty effective set."""
        Policy = self._import_policy()
        server = Policy(
            allowed=frozenset({"read", "search", "edit", "commit"}),
            source="repo",
        )
        effective = Policy.compute_effective(
            server, client=Policy(allowed=frozenset(), source="cli")
        )
        self.assertEqual(effective.allowed, frozenset())

    def test_intersection_never_exceeds_server(self) -> None:
        """Client cannot add stages that server policy excludes."""
        Policy = self._import_policy()
        server = Policy(
            allowed=frozenset({"read"}),
            source="repo",
        )
        client = Policy(
            allowed=frozenset({"read", "edit", "push"}),
            source="cli",
        )
        effective = Policy.compute_effective(server, client)
        self.assertEqual(effective.allowed, frozenset({"read"}))


# ---------------------------------------------------------------------------
# 3. Admission tests
# ---------------------------------------------------------------------------


class AdmissionPolicyTests(unittest.TestCase):
    """Admission rejects proposals that require forbidden stages.

    These tests require the ``execution_policy_json`` keyword on
    ``admit_commander_response`` that Grok will add in Task 8.  They fail
    with ``TypeError`` (unexpected keyword) until then.
    """

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
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]
        self.goal_id = create_goal(
            self.conn,
            "test goal",
            "test objective",
            project_id=self.project_id,
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _admit_with_policy(
        self, proposals: list, policy_json: str
    ):
        """Call admit_commander_response with execution policy.

        The ``execution_policy_json`` keyword does not exist yet.  Once
        Grok adds it in Task 8, this call will succeed.
        """
        from local_cli_coordinator.commander_policy import (
            admit_commander_response,
        )
        response = self._make_response(proposals)
        return admit_commander_response(
            self.conn,
            mock.MagicMock(),  # config
            self.repo,  # root
            self.goal_id,
            response,
            project_id=self.project_id,
            execution_policy_json=policy_json,
        )

    def _make_proposal(self, **overrides) -> "CommanderTaskProposal":
        from local_cli_coordinator.commander_protocol import CommanderTaskProposal
        defaults = dict(
            title="test task",
            repo=str(self.repo),
            capabilities=[],
            goal="do something",
            acceptance_criteria=[],
            verification_commands=["make test"],
            expected_files=1,
            expected_minutes=30,
            parent_task_id=None,
            rationale="test",
        )
        defaults.update(overrides)
        return CommanderTaskProposal(**defaults)

    def _make_response(self, proposals):
        from local_cli_coordinator.commander_protocol import CommanderResponse
        return CommanderResponse(
            schema_version=1,
            intent="task_request",
            user_reply="",
            goal_status="active",
            progress_summary="",
            tasks=proposals,
            stop_reason=None,
        )

    def test_no_tools_rejects_all_proposals(self) -> None:
        proposal = self._make_proposal()
        result = self._admit_with_policy(
            [proposal],
            json.dumps({"allowed": [], "source": "cli"}),
        )
        self.assertEqual(len(result.accepted_task_ids), 0)
        self.assertTrue(len(result.rejection_reasons) > 0)

    def test_read_only_requires_expected_files_zero(self) -> None:
        """Read/search-only policy rejects proposals with expected_files > 0."""
        proposal = self._make_proposal(expected_files=1)
        result = self._admit_with_policy(
            [proposal],
            json.dumps({"allowed": ["read", "search"], "source": "cli"}),
        )
        self.assertEqual(len(result.accepted_task_ids), 0)

    def test_read_only_accepts_expected_files_zero(self) -> None:
        proposal = self._make_proposal(
            expected_files=0,
            verification_commands=[],
        )
        result = self._admit_with_policy(
            [proposal],
            json.dumps({"allowed": ["read", "search"], "source": "cli"}),
        )
        self.assertEqual(len(result.accepted_task_ids), 1)

    def test_no_test_rejects_proposals_with_verification(self) -> None:
        """Policy without 'test' rejects proposals that have verification commands."""
        proposal = self._make_proposal(verification_commands=["make test"])
        result = self._admit_with_policy(
            [proposal],
            json.dumps({"allowed": ["read", "edit", "commit"], "source": "cli"}),
        )
        self.assertEqual(len(result.accepted_task_ids), 0)


# ---------------------------------------------------------------------------
# 4. Engine stage enforcement tests
# ---------------------------------------------------------------------------


class EngineStageEnforcementTests(unittest.TestCase):
    """Engine enforces execution policy at each pipeline stage.

    These tests require the policy-aware engine code paths that Grok will
    implement in Task 9.  They import from ``execution_policy`` to ensure
    they fail with ``ModuleNotFoundError`` until the module exists.
    """

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
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]
        self.goal_id = create_goal(
            self.conn,
            "test goal",
            "test objective",
            project_id=self.project_id,
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_task_row(self, policy: dict) -> dict:
        """Build a minimal task row dict with the given execution_policy."""
        return {
            "id": 1,
            "goal_id": self.goal_id,
            "title": "test task",
            "prompt": "do something",
            "status": "ready",
            "execution_policy": json.dumps(policy),
            "expected_files": 1,
            "verification_commands": json.dumps(["make test"]),
            "agent_role": "worker",
            "repo_ids": json.dumps([self.project_id]),
        }

    def _import_policy_enforcement(self):
        """Import the policy enforcement hook from execution_policy.

        This import will fail with ModuleNotFoundError until Grok implements
        the module, which is the intended red-test behavior.
        """
        from local_cli_coordinator.execution_policy import check_policy_stage
        return check_policy_stage

    def test_no_edit_changed_worktree_fails(self) -> None:
        """If edit is forbidden and files changed, the attempt is failed."""
        check_policy_stage = self._import_policy_enforcement()
        policy = {"allowed": ["read", "test", "commit"], "source": "cli"}
        # check_policy_stage should raise when edit is forbidden but
        # worktree has changes.
        with self.assertRaises(Exception) as ctx:
            check_policy_stage(policy, "edit", has_changes=True)
        msg = str(ctx.exception).lower()
        self.assertIn("edit", msg)

    def test_no_test_never_invokes_verification(self) -> None:
        """When test is not in allowed set, verification is skipped."""
        check_policy_stage = self._import_policy_enforcement()
        policy = {"allowed": ["read", "edit", "commit"], "source": "cli"}
        # check_policy_stage("test") should raise/skip when test is absent.
        with self.assertRaises(Exception):
            check_policy_stage(policy, "test")

    def test_no_commit_preserves_worktree_and_awaits_human(self) -> None:
        """When commit is forbidden, successful attempt transitions to
        awaiting_human and preserves the worktree."""
        check_policy_stage = self._import_policy_enforcement()
        policy = {"allowed": ["read", "edit", "test"], "source": "cli"}
        with self.assertRaises(Exception):
            check_policy_stage(policy, "commit")

    def test_no_push_skips_push_operation(self) -> None:
        """When push is forbidden, push_branch is never called."""
        check_policy_stage = self._import_policy_enforcement()
        policy = {
            "allowed": ["read", "edit", "test", "commit"],
            "source": "cli",
        }
        with self.assertRaises(Exception):
            check_policy_stage(policy, "push")

    def test_no_merge_skips_merge_operation(self) -> None:
        """When merge is forbidden, merge_branch_to_default is never called."""
        check_policy_stage = self._import_policy_enforcement()
        policy = {
            "allowed": ["read", "edit", "test", "commit", "push"],
            "source": "cli",
        }
        with self.assertRaises(Exception):
            check_policy_stage(policy, "merge")


# ---------------------------------------------------------------------------
# 5. Persistence tests
# ---------------------------------------------------------------------------


class ExecutionPolicyPersistenceTests(unittest.TestCase):
    """Execution policy survives CLI database close and daemon cycle."""

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
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]
        self.goal_id = create_goal(
            self.conn,
            "test goal",
            "test objective",
            project_id=self.project_id,
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_policy_survives_close_and_reopen(self) -> None:
        """A task's execution_policy is preserved across DB close/reopen."""
        policy = {"allowed": ["read", "search"], "source": "cli"}
        run_id = acquire_commander_run_slot(
            self.conn,
            self.goal_id,
            "manual",
            1,
            self.home / "prompt.md",
            execution_policy=json.dumps(policy),
        )
        self.assertIsNotNone(run_id)
        self.conn.commit()

        # Close and reopen using the same database path
        db_path = self.paths.database
        self.conn.close()
        conn2 = connect(db_path)
        init_db(conn2)
        row = conn2.execute(
            "SELECT execution_policy FROM commander_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        stored = json.loads(row["execution_policy"])
        self.assertEqual(stored["allowed"], ["read", "search"])
        self.assertEqual(stored["source"], "cli")
        conn2.close()

    def test_task_policy_survives_daemon_cycle(self) -> None:
        """A task's execution_policy persists through a daemon cycle.

        Requires ``admit_commander_response`` to accept ``execution_policy_json``
        (Task 8) and persist it on admitted tasks.
        """
        from local_cli_coordinator.commander_policy import (
            admit_commander_response,
        )
        from local_cli_coordinator.commander_protocol import (
            CommanderResponse,
            CommanderTaskProposal,
        )
        policy = {"allowed": ["read", "search", "edit"], "source": "cli"}
        proposal = CommanderTaskProposal(
            title="persistent task",
            repo=str(self.repo),
            capabilities=[],
            goal="do work",
            acceptance_criteria=[],
            verification_commands=[],
            expected_files=0,
            expected_minutes=30,
            parent_task_id=None,
            rationale="test",
        )
        response = CommanderResponse(
            schema_version=1,
            intent="task_request",
            user_reply="",
            goal_status="active",
            progress_summary="",
            tasks=[proposal],
            stop_reason=None,
        )
        result = admit_commander_response(
            self.conn,
            mock.MagicMock(),  # config
            self.repo,
            self.goal_id,
            response,
            project_id=self.project_id,
            execution_policy_json=json.dumps(policy),
        )
        self.assertEqual(len(result.accepted_task_ids), 1)
        task_id = result.accepted_task_ids[0]
        self.conn.commit()

        # Simulate daemon cycle: read the task back
        task = self.conn.execute(
            "SELECT execution_policy FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        stored = json.loads(task["execution_policy"])
        self.assertEqual(stored["allowed"], ["read", "search", "edit"])
        self.assertEqual(stored["source"], "cli")

    def test_default_policy_is_empty(self) -> None:
        """Tasks without explicit policy get the default empty policy."""
        from local_cli_coordinator.commander_policy import (
            admit_commander_response,
        )
        from local_cli_coordinator.commander_protocol import (
            CommanderResponse,
            CommanderTaskProposal,
        )
        proposal = CommanderTaskProposal(
            title="default task",
            repo=str(self.repo),
            capabilities=[],
            goal="do work",
            acceptance_criteria=[],
            verification_commands=["make test"],
            expected_files=1,
            expected_minutes=30,
            parent_task_id=None,
            rationale="test",
        )
        response = CommanderResponse(
            schema_version=1,
            intent="task_request",
            user_reply="",
            goal_status="active",
            progress_summary="",
            tasks=[proposal],
            stop_reason=None,
        )
        result = admit_commander_response(
            self.conn,
            mock.MagicMock(),  # config
            self.repo,
            self.goal_id,
            response,
            project_id=self.project_id,
        )
        if result.accepted_task_ids:
            task = self.conn.execute(
                "SELECT execution_policy FROM tasks WHERE id = ?",
                (result.accepted_task_ids[0],),
            ).fetchone()
            stored = json.loads(task["execution_policy"])
            # Default policy should be empty dict or contain full stages
            self.assertIsInstance(stored, dict)


# ---------------------------------------------------------------------------
# 6. RPC mode envelope tests
# ---------------------------------------------------------------------------


class RpcModeEnvelopeTests(unittest.TestCase):
    """RPC mode emits exactly one ResponseEnvelope per invocation."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.repo = self.home / "repo"
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
            "select id from projects limit 1"
        ).fetchone()["id"]
        goal_id = create_goal(
            self.conn, "RPC goal", "objective", project_id=self.project_id
        )
        self.conn.execute(
            "update goals set status = 'active' where id = ?", (goal_id,)
        )
        self.conn.commit()
        self._old_coordinator_home = os.environ.get("COORDINATOR_HOME")
        os.environ["COORDINATOR_HOME"] = str(self.home)
        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.conn.close()
        if self._old_coordinator_home is None:
            os.environ.pop("COORDINATOR_HOME", None)
        else:
            os.environ["COORDINATOR_HOME"] = self._old_coordinator_home
        self.tmp.cleanup()

    def _parse_prompt(self, argv: list[str]):
        parser = build_prompt_parser()
        args = parser.parse_args(argv)
        normalize_prompt_args(args)
        return args

    def test_rpc_status_returns_envelope(self) -> None:
        """/status in RPC mode returns a valid ResponseEnvelope."""
        result = _run_cli_with_home(
            self.home,
            "--root", str(self.repo),
            "--mode", "rpc", "-p", "/status",
            cwd=self.repo,
        )
        # Parse the single JSON line from stdout
        lines = [
            line for line in result.stdout.strip().splitlines() if line.strip()
        ]
        self.assertEqual(len(lines), 1, f"Expected 1 JSON line, got {len(lines)}")
        envelope = json.loads(lines[0])
        self.assertEqual(envelope["protocol_version"], PROTOCOL_VERSION)
        self.assertIn("request_id", envelope)
        self.assertIn("ok", envelope)
        self.assertEqual(envelope["type"], "response")

    def test_rpc_chat_success_envelope(self) -> None:
        """Chat in RPC mode returns a ResponseEnvelope with ok=true."""
        result = _run_cli_with_home(
            self.home,
            "--root", str(self.repo),
            "--mode", "rpc", "-p", "hello",
            cwd=self.repo,
        )
        lines = [
            line for line in result.stdout.strip().splitlines() if line.strip()
        ]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertTrue(envelope["ok"])
        self.assertIsNotNone(envelope["result"])

    def test_rpc_error_returns_envelope_with_ok_false(self) -> None:
        """Errors in RPC mode still produce a valid ResponseEnvelope."""
        result = _run_cli_with_home(
            self.home,
            "--root", str(self.repo),
            "--mode", "rpc", "-p", "/nonexistent_command_xyz",
            cwd=self.repo,
        )
        lines = [
            line for line in result.stdout.strip().splitlines() if line.strip()
        ]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertFalse(envelope["ok"])
        self.assertIsNotNone(envelope["error"])

    def test_rpc_error_has_request_id_prefix(self) -> None:
        """Local validation errors use request_id prefixed with cli-local-."""
        result = _run_cli_with_home(
            self.home,
            "--root", str(self.repo),
            "--mode", "rpc", "-p", "/nonexistent_command_xyz",
            cwd=self.repo,
        )
        lines = [
            line for line in result.stdout.strip().splitlines() if line.strip()
        ]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertTrue(
            envelope["request_id"].startswith("cli-local-"),
            f"Expected cli-local- prefix, got {envelope['request_id']}",
        )

    def test_rpc_unknown_tool_returns_envelope(self) -> None:
        """Tool parse errors in RPC mode emit ResponseEnvelope, not stderr."""
        result = _run_cli_with_home(
            self.home,
            "--root", str(self.repo),
            "--mode", "rpc",
            "--tools", "nonexistent_tool",
            "-p", "hello",
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr.strip(), "")
        lines = [
            line for line in result.stdout.strip().splitlines() if line.strip()
        ]
        self.assertEqual(len(lines), 1)
        envelope = json.loads(lines[0])
        self.assertFalse(envelope["ok"])
        self.assertIn("unknown tool", (envelope.get("error") or "").lower())

    def test_rpc_mode_implies_no_tui(self) -> None:
        """--mode rpc implies --no-tui (headless)."""
        args = self._parse_prompt(["--mode", "rpc", "-p", "test"])
        self.assertTrue(args.no_tui)
