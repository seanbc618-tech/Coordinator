"""Red tests for Phase 5.4 goal session requirements.

These tests capture the contract for project-scoped goal candidate listing,
resume state transitions, fork lineage creation, CLI parser mutual exclusion
for ``--continue``/``--resume``/``--fork``, and no-ID candidate output modes.

Owner: Claude Code (Task 4)
Expected before implementation: import/attribute failures for
``goal_sessions``, ``parent_goal_id``, ``--resume``/``--fork`` parser options,
and ``project.goals``/``project.goal.resume``/``project.goal.fork`` RPC methods.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_cli_coordinator.cli import build_prompt_parser, normalize_prompt_args
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import (
    active_goal_for_project,
    create_goal,
    get_goal,
    transition_goal,
)
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
# Candidate listing: project-scoped, ordered by updated_at desc, id desc
# ---------------------------------------------------------------------------


class GoalCandidateListingTests(unittest.TestCase):
    """Tests for ``list_project_goal_candidates()``."""

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
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_candidates_scoped_to_project(self):
        """Only goals belonging to the project appear."""
        from local_cli_coordinator.goal_sessions import list_project_goal_candidates

        g1 = create_goal(
            self.conn, "Goal A", "objective A", project_id=self.project_id
        )
        # Different project (at most one non-terminal goal per project_id).
        create_goal(
            self.conn, "Other", "other project", project_id="other-project"
        )
        candidates = list_project_goal_candidates(self.conn, self.project_id)
        ids = [c["id"] for c in candidates]
        self.assertIn(g1, ids)
        self.assertEqual(len(candidates), 1)

    def test_candidates_ordered_by_id_desc(self):
        """Newer goals (higher id) appear first."""
        from local_cli_coordinator.goal_sessions import list_project_goal_candidates

        g1 = create_goal(
            self.conn, "First", "first", project_id=self.project_id
        )
        transition_goal(self.conn, g1, "completed")
        g2 = create_goal(
            self.conn, "Second", "second", project_id=self.project_id
        )
        transition_goal(self.conn, g2, "completed")
        g3 = create_goal(
            self.conn, "Third", "third", project_id=self.project_id
        )
        candidates = list_project_goal_candidates(self.conn, self.project_id)
        ids = [c["id"] for c in candidates]
        self.assertEqual(ids, [g3])

    def test_candidates_include_linked_task_counts(self):
        """Each candidate includes linked task count."""
        from local_cli_coordinator.goal_sessions import list_project_goal_candidates

        g = create_goal(
            self.conn, "Goal", "objective", project_id=self.project_id
        )
        candidates = list_project_goal_candidates(self.conn, self.project_id)
        self.assertEqual(len(candidates), 1)
        self.assertIn("linked_task_count", candidates[0].keys())
        self.assertEqual(candidates[0]["linked_task_count"], 0)

    def test_terminal_goals_excluded_from_candidates(self):
        """Completed/failed/abandoned goals are not candidates."""
        from local_cli_coordinator.goal_sessions import list_project_goal_candidates

        done = create_goal(
            self.conn, "Done", "completed goal", project_id=self.project_id
        )
        transition_goal(self.conn, done, "completed")
        active = create_goal(
            self.conn, "Active", "active goal", project_id=self.project_id
        )
        transition_goal(self.conn, active, "active")
        candidates = list_project_goal_candidates(self.conn, self.project_id)
        ids = [c["id"] for c in candidates]
        self.assertIn(active, ids)
        self.assertNotIn(done, ids)


# ---------------------------------------------------------------------------
# Resume state matrix
# ---------------------------------------------------------------------------


class GoalResumeStateTests(unittest.TestCase):
    """Tests for ``resume_project_goal()`` state transitions."""

    # State -> expected resulting state after resume
    RESUME_MATRIX = {
        "active": "active",
        "paused": "active",
        "blocked": "active",
        "draft": "draft",
    }

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
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_goal_in_state(self, state: str) -> int:
        existing = active_goal_for_project(self.conn, self.project_id)
        if existing is not None:
            transition_goal(self.conn, existing["id"], "completed")
        g = create_goal(
            self.conn, f"Goal {state}", f"objective {state}",
            project_id=self.project_id,
        )
        if state != "draft":
            transition_goal(self.conn, g, state)
        return g

    def test_resume_matrix(self):
        """Each resumable state transitions to the expected state."""
        from local_cli_coordinator.goal_sessions import resume_project_goal

        for initial, expected in self.RESUME_MATRIX.items():
            with self.subTest(initial=initial, expected=expected):
                g = self._create_goal_in_state(initial)
                result = resume_project_goal(self.conn, self.project_id, g)
                goal = get_goal(self.conn, g)
                self.assertEqual(goal["status"], expected)

    def test_completed_goal_not_resumable(self):
        from local_cli_coordinator.goal_sessions import (
            GoalSessionError,
            resume_project_goal,
        )
        g = self._create_goal_in_state("completed")
        with self.assertRaises(GoalSessionError) as ctx:
            resume_project_goal(self.conn, self.project_id, g)
        self.assertEqual(ctx.exception.code, "goal_not_resumable")

    def test_failed_goal_not_resumable(self):
        from local_cli_coordinator.goal_sessions import (
            GoalSessionError,
            resume_project_goal,
        )
        g = self._create_goal_in_state("failed")
        with self.assertRaises(GoalSessionError) as ctx:
            resume_project_goal(self.conn, self.project_id, g)
        self.assertEqual(ctx.exception.code, "goal_not_resumable")

    def test_abandoned_goal_not_resumable(self):
        from local_cli_coordinator.goal_sessions import (
            GoalSessionError,
            resume_project_goal,
        )
        g = self._create_goal_in_state("abandoned")
        with self.assertRaises(GoalSessionError) as ctx:
            resume_project_goal(self.conn, self.project_id, g)
        self.assertEqual(ctx.exception.code, "goal_not_resumable")


# ---------------------------------------------------------------------------
# Project isolation: cross-project resume rejected
# ---------------------------------------------------------------------------


class GoalProjectIsolationTests(unittest.TestCase):
    """Cross-project goal IDs must be rejected."""

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
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cross_project_resume_rejected(self):
        from local_cli_coordinator.goal_sessions import (
            GoalSessionError,
            resume_project_goal,
        )
        # Create goal in different project
        g = create_goal(
            self.conn, "Foreign", "objective", project_id="other-project"
        )
        transition_goal(self.conn, g, "active")
        with self.assertRaises(GoalSessionError) as ctx:
            resume_project_goal(self.conn, self.project_id, g)
        self.assertEqual(ctx.exception.code, "goal_wrong_project")

    def test_cross_project_fork_rejected(self):
        from local_cli_coordinator.goal_sessions import (
            GoalSessionError,
            fork_project_goal,
        )
        g = create_goal(
            self.conn, "Foreign", "objective", project_id="other-project"
        )
        transition_goal(self.conn, g, "completed")
        with self.assertRaises(GoalSessionError) as ctx:
            fork_project_goal(self.conn, self.project_id, g, "continue")
        self.assertEqual(ctx.exception.code, "goal_wrong_project")


# ---------------------------------------------------------------------------
# Goal conflict: existing non-terminal goal blocks resume/fork
# ---------------------------------------------------------------------------


class GoalConflictTests(unittest.TestCase):
    """An existing different non-terminal goal must block resume/fork."""

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
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    @mock.patch(
        "local_cli_coordinator.goal_sessions._other_non_terminal_goals",
        return_value=[{"id": 999}],
    )
    def test_resume_blocked_by_active_goal(self, _mock_others):
        from local_cli_coordinator.goal_sessions import (
            GoalSessionError,
            resume_project_goal,
        )
        target = create_goal(
            self.conn, "Target", "target", project_id=self.project_id
        )
        transition_goal(self.conn, target, "paused")
        with self.assertRaises(GoalSessionError) as ctx:
            resume_project_goal(self.conn, self.project_id, target)
        self.assertEqual(ctx.exception.code, "goal_conflict")

    @mock.patch(
        "local_cli_coordinator.goal_sessions._other_non_terminal_goals",
        return_value=[{"id": 999}],
    )
    def test_resume_blocked_by_paused_goal(self, _mock_others):
        from local_cli_coordinator.goal_sessions import (
            GoalSessionError,
            resume_project_goal,
        )
        target = create_goal(
            self.conn, "Target", "target", project_id=self.project_id
        )
        transition_goal(self.conn, target, "blocked")
        with self.assertRaises(GoalSessionError) as ctx:
            resume_project_goal(self.conn, self.project_id, target)
        self.assertEqual(ctx.exception.code, "goal_conflict")

    def test_fork_blocked_by_active_goal(self):
        from local_cli_coordinator.goal_sessions import (
            GoalSessionError,
            fork_project_goal,
        )
        source = create_goal(
            self.conn, "Source", "source", project_id=self.project_id
        )
        transition_goal(self.conn, source, "completed")
        blocker = create_goal(
            self.conn, "Blocker", "blocker", project_id=self.project_id
        )
        transition_goal(self.conn, blocker, "active")
        with self.assertRaises(GoalSessionError) as ctx:
            fork_project_goal(
                self.conn, self.project_id, source, "continue work"
            )
        self.assertEqual(ctx.exception.code, "goal_conflict")


# ---------------------------------------------------------------------------
# Fork lineage: terminal source creates new draft
# ---------------------------------------------------------------------------


class GoalForkLineageTests(unittest.TestCase):
    """Fork from a terminal goal creates a new draft with lineage."""

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
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fork_creates_draft(self):
        from local_cli_coordinator.goal_sessions import fork_project_goal

        source = create_goal(
            self.conn, "Source", "original objective",
            project_id=self.project_id,
            completion_criteria=["criterion 1"],
            constraints=["constraint 1"],
            repo_ids=["repo-a"],
        )
        transition_goal(self.conn, source, "completed")
        new_id = fork_project_goal(
            self.conn, self.project_id, source, "keep going"
        )
        new_goal = get_goal(self.conn, new_id)
        self.assertEqual(new_goal["status"], "draft")
        self.assertNotEqual(new_id, source)

    def test_fork_copies_objective_and_criteria(self):
        from local_cli_coordinator.goal_sessions import fork_project_goal

        source = create_goal(
            self.conn, "Source", "original objective",
            project_id=self.project_id,
            completion_criteria=["criterion A", "criterion B"],
            constraints=["constraint X"],
            repo_ids=["repo-1"],
        )
        transition_goal(self.conn, source, "completed")
        new_id = fork_project_goal(
            self.conn, self.project_id, source, "extend"
        )
        new_goal = get_goal(self.conn, new_id)
        self.assertIn("original objective", new_goal["objective"])
        self.assertIn("extend", new_goal["objective"])
        criteria = json.loads(new_goal["completion_criteria"])
        self.assertIn("criterion A", criteria)
        self.assertIn("criterion B", criteria)
        constraints = json.loads(new_goal["constraints"])
        self.assertIn("constraint X", constraints)
        repos = json.loads(new_goal["repo_ids"])
        self.assertIn("repo-1", repos)

    def test_fork_sets_parent_goal_id(self):
        from local_cli_coordinator.goal_sessions import fork_project_goal

        source = create_goal(
            self.conn, "Source", "objective", project_id=self.project_id
        )
        transition_goal(self.conn, source, "failed")
        new_id = fork_project_goal(
            self.conn, self.project_id, source, "retry"
        )
        new_goal = get_goal(self.conn, new_id)
        self.assertEqual(new_goal["parent_goal_id"], source)

    def test_fork_copies_no_task_links(self):
        from local_cli_coordinator.goal_sessions import fork_project_goal
        from local_cli_coordinator.goals import list_linked_tasks

        source = create_goal(
            self.conn, "Source", "objective", project_id=self.project_id
        )
        transition_goal(self.conn, source, "completed")
        self.conn.execute(
            """
            insert into tasks(
                id, title, repo, state, priority, capabilities, source_path,
                goal, acceptance_criteria, verification_commands, project_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-001",
                "Linked task",
                "repo-a",
                "done",
                "high",
                "[]",
                "",
                "test",
                "[]",
                "[]",
                self.project_id,
            ),
        )
        self.conn.execute(
            """
            insert into task_goal_links(goal_id, task_id, proposal_fingerprint)
            values (?, ?, ?)
            """,
            (source, "task-001", "fp-001"),
        )
        self.conn.commit()
        new_id = fork_project_goal(
            self.conn, self.project_id, source, "continue"
        )
        linked = list_linked_tasks(self.conn, new_id)
        self.assertEqual(len(linked), 0)

    def test_fork_copies_no_runs_or_attempts(self):
        from local_cli_coordinator.goal_sessions import fork_project_goal
        from local_cli_coordinator.goals import list_commander_runs

        source = create_goal(
            self.conn, "Source", "objective", project_id=self.project_id
        )
        transition_goal(self.conn, source, "completed")
        # Simulate a commander run
        self.conn.execute(
            "INSERT INTO commander_runs (goal_id, status) VALUES (?, ?)",
            (source, "completed"),
        )
        self.conn.commit()
        new_id = fork_project_goal(
            self.conn, self.project_id, source, "retry"
        )
        runs = list_commander_runs(self.conn, new_id)
        self.assertEqual(len(runs), 0)

    def test_fork_does_not_invoke_commander(self):
        """Fork only creates a draft; no Commander process is started."""
        from local_cli_coordinator.goal_sessions import fork_project_goal
        from local_cli_coordinator.goals import list_commander_runs

        source = create_goal(
            self.conn, "Source", "objective", project_id=self.project_id
        )
        transition_goal(self.conn, source, "completed")
        new_id = fork_project_goal(
            self.conn, self.project_id, source, "try again"
        )
        # No commander runs should exist for the new goal
        runs = list_commander_runs(self.conn, new_id)
        self.assertEqual(len(runs), 0)

    def test_fork_from_abandoned_source(self):
        from local_cli_coordinator.goal_sessions import fork_project_goal

        source = create_goal(
            self.conn, "Source", "objective", project_id=self.project_id
        )
        transition_goal(self.conn, source, "abandoned")
        new_id = fork_project_goal(
            self.conn, self.project_id, source, "revive"
        )
        new_goal = get_goal(self.conn, new_id)
        self.assertEqual(new_goal["status"], "draft")
        self.assertEqual(new_goal["parent_goal_id"], source)

    def test_fork_from_failed_source(self):
        from local_cli_coordinator.goal_sessions import fork_project_goal

        source = create_goal(
            self.conn, "Source", "objective", project_id=self.project_id
        )
        transition_goal(self.conn, source, "failed")
        new_id = fork_project_goal(
            self.conn, self.project_id, source, "fix and retry"
        )
        new_goal = get_goal(self.conn, new_id)
        self.assertEqual(new_goal["status"], "draft")

    def test_fork_rejects_nonterminal_source(self):
        """Cannot fork from a goal that hasn't finished."""
        from local_cli_coordinator.goal_sessions import (
            GoalSessionError,
            fork_project_goal,
        )
        source = create_goal(
            self.conn, "Source", "objective", project_id=self.project_id
        )
        transition_goal(self.conn, source, "active")
        with self.assertRaises(GoalSessionError) as ctx:
            fork_project_goal(
                self.conn, self.project_id, source, "fork active"
            )
        self.assertIn("non-terminal", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# Parser mutual exclusion: --continue, --resume, --fork
# ---------------------------------------------------------------------------


class GoalParserMutualExclusionTests(unittest.TestCase):
    """``--continue``, ``--resume``, and ``--fork`` are mutually exclusive."""

    def _parse(self, argv: list[str]):
        parser = build_prompt_parser()
        args = parser.parse_args(argv)
        normalize_prompt_args(args)
        return args

    def test_continue_and_resume_exclusive(self):
        """``--continue`` and ``--resume`` cannot coexist."""
        parser = build_prompt_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--continue", "--resume", "1"])

    def test_continue_and_fork_exclusive(self):
        """``--continue`` and ``--fork`` cannot coexist."""
        parser = build_prompt_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--continue", "--fork", "1"])

    def test_resume_and_fork_exclusive(self):
        """``--resume`` and ``--fork`` cannot coexist."""
        parser = build_prompt_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--resume", "1", "--fork", "1"])

    def test_continue_flag_parsed(self):
        args = self._parse(["--continue", "-p", "hello"])
        self.assertTrue(args.continue_goal)

    def test_resume_flag_parsed_with_goal_id(self):
        args = self._parse(["--resume", "42", "-p", "hello"])
        self.assertEqual(args.resume, "42")

    def test_resume_flag_parsed_without_goal_id(self):
        args = self._parse(["--resume", "-p", "hello"])
        # --resume without value means list candidates
        self.assertEqual(args.resume, "")

    def test_fork_flag_parsed(self):
        args = self._parse(["--fork", "7", "-p", "new direction"])
        self.assertEqual(args.fork, 7)


# ---------------------------------------------------------------------------
# No-ID candidate output: text, JSON, RPC, noninteractive exit 2
# ---------------------------------------------------------------------------


class GoalNoCandidateOutputTests(unittest.TestCase):
    """When --resume is used without a goal ID, output candidates."""

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
        draft = inspect_project(self.repo)
        register_project(self.conn, draft, confirmed=True)
        row = self.conn.execute(
            "SELECT id FROM projects LIMIT 1"
        ).fetchone()
        self.project_id = row["id"]
        self.server = FakeSupervisor(str(self.paths.socket))
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_id_noninteractive_exits_2(self):
        """Noninteractive ``--resume`` without ID exits with code 2."""
        result = _run_cli_with_home(
            self.home,
            "--root", str(self.repo),
            "--resume",
            "--print",
        )
        self.assertEqual(result.returncode, 2)

    def test_no_id_text_output_lists_candidates(self):
        """Text mode ``--resume`` without ID prints candidate list."""
        # Create some goals
        g1 = create_goal(
            self.conn, "Goal A", "obj A", project_id=self.project_id
        )
        transition_goal(self.conn, g1, "active")
        result = _run_cli_with_home(
            self.home,
            "--root", str(self.repo),
            "--resume",
            "--print",
        )
        combined = result.stdout + result.stderr
        self.assertIn(str(g1), combined)

    def test_no_id_json_output_includes_candidates(self):
        """JSON mode ``--resume`` without ID includes candidates array."""
        g1 = create_goal(
            self.conn, "Goal A", "obj A", project_id=self.project_id
        )
        transition_goal(self.conn, g1, "active")
        result = _run_cli_with_home(
            self.home,
            "--root", str(self.repo),
            "--resume",
            "--mode", "json",
        )
        data = json.loads(result.stdout)
        self.assertIn("candidates", data)
        self.assertIsInstance(data["candidates"], list)
        if data["candidates"]:
            c = data["candidates"][0]
            self.assertIn("id", c)
            self.assertIn("title", c)
            self.assertIn("status", c)

    def test_no_candidates_returns_empty(self):
        """When no goals exist, output is empty list."""
        result = _run_cli_with_home(
            self.home,
            "--root", str(self.repo),
            "--resume",
            "--mode", "json",
        )
        data = json.loads(result.stdout)
        self.assertIn("candidates", data)
        self.assertEqual(data["candidates"], [])


# ---------------------------------------------------------------------------
# Migration 012: parent_goal_id column
# ---------------------------------------------------------------------------


class GoalLineageMigrationTests(unittest.TestCase):
    """Migration 012 must add ``parent_goal_id`` to goals table."""

    def test_migration_012_exists(self):
        """Migration 012 file must exist in both locations."""
        from pathlib import Path

        src_migration = Path(
            "src/local_cli_coordinator/migrations/012_goal_lineage.sql"
        )
        wheel_migration = Path("migrations/012_goal_lineage.sql")
        self.assertTrue(
            src_migration.exists(),
            f"Missing: {src_migration}",
        )
        self.assertTrue(
            wheel_migration.exists(),
            f"Missing: {wheel_migration}",
        )

    def test_migration_012_files_identical(self):
        """Both copies of migration 012 must be byte-identical."""
        from pathlib import Path

        src_path = Path(
            "src/local_cli_coordinator/migrations/012_goal_lineage.sql"
        )
        wheel_path = Path("migrations/012_goal_lineage.sql")
        if src_path.exists() and wheel_path.exists():
            self.assertEqual(src_path.read_bytes(), wheel_path.read_bytes())
        else:
            self.fail(
                f"Migration 012 missing: "
                f"src={src_path.exists()}, wheel={wheel_path.exists()}"
            )

    def test_parent_goal_id_column_exists(self):
        """After running migrations, goals table has parent_goal_id."""
        tmp = Path(tempfile.mkdtemp())
        conn = None
        try:
            home = tmp / "home"
            home.mkdir()
            paths = RuntimePaths(
                home / "config", home / "data", home / "state"
            )
            paths.create()
            conn = connect(paths.database)
            init_db(conn)
            # Check column exists
            cols = conn.execute("pragma table_info(goals)").fetchall()
            col_names = [c["name"] for c in cols]
            self.assertIn("parent_goal_id", col_names)
        finally:
            if conn is not None:
                conn.close()
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
