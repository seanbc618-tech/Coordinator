"""Red tests for Phase 7 overnight run windows and summaries.

These tests capture the contract for ``overnight.py``:
quiet-hour scheduling, pause behavior, and persisted morning summaries.

Owner: Grok (Phase 7 Task 0)
Expected before implementation: ``ModuleNotFoundError`` for ``overnight``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from local_cli_coordinator.config import (
    AgentConfig,
    AutonomyConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    OvernightConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class OvernightModuleTests(unittest.TestCase):
    """overnight module exports schedule helpers."""

    def test_overnight_module_import(self) -> None:
        from local_cli_coordinator.overnight import (
            OvernightWindow,
            get_latest_overnight_summary,
            is_within_quiet_hours,
            parse_overnight_until,
            persist_overnight_summary,
            should_pause_for_quiet_hours,
        )
        self.assertTrue(callable(is_within_quiet_hours))
        self.assertTrue(callable(should_pause_for_quiet_hours))
        self.assertTrue(callable(persist_overnight_summary))
        self.assertTrue(callable(get_latest_overnight_summary))
        self.assertTrue(callable(parse_overnight_until))
        self.assertIn("quiet_start", OvernightWindow.__annotations__)


class QuietHoursTests(unittest.TestCase):
    """Quiet-hour detection respects configured windows."""

    def test_is_within_quiet_hours_overnight_span(self) -> None:
        from local_cli_coordinator.overnight import OvernightWindow, is_within_quiet_hours

        window = OvernightWindow(quiet_start="22:00", quiet_end="08:00")
        late_night = datetime(2026, 6, 29, 23, 30, tzinfo=timezone.utc)
        morning = datetime(2026, 6, 29, 7, 0, tzinfo=timezone.utc)
        afternoon = datetime(2026, 6, 29, 14, 0, tzinfo=timezone.utc)
        self.assertTrue(is_within_quiet_hours(late_night, window))
        self.assertTrue(is_within_quiet_hours(morning, window))
        self.assertFalse(is_within_quiet_hours(afternoon, window))

    def test_parse_overnight_until_extracts_time(self) -> None:
        from local_cli_coordinator.overnight import parse_overnight_until

        parsed = parse_overnight_until("start --until 08:00")
        self.assertEqual(parsed.until_time, "08:00")
        self.assertTrue(parsed.enabled)


class OvernightSummaryTests(unittest.TestCase):
    """Overnight summaries persist redacted project-scoped progress."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_persist_overnight_summary_round_trips(self) -> None:
        from local_cli_coordinator.overnight import (
            get_latest_overnight_summary,
            persist_overnight_summary,
        )

        summary = {
            "tasks_completed": 2,
            "tasks_failed": 1,
            "milestones_touched": ["Ship login"],
            "notes": "steady progress",
        }
        summary_id = persist_overnight_summary(
            self.conn,
            project_id=self.project_id,
            run_session_id=None,
            window_started_at="2026-06-29T22:00:00+00:00",
            window_ended_at="2026-06-29T08:00:00+00:00",
            summary=summary,
        )
        self.assertIsInstance(summary_id, int)
        latest = get_latest_overnight_summary(self.conn, project_id=self.project_id)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest["tasks_completed"], 2)
        self.assertEqual(latest["milestones_touched"], ["Ship login"])

    def test_summary_does_not_leak_other_project_titles(self) -> None:
        from local_cli_coordinator.overnight import (
            get_latest_overnight_summary,
            persist_overnight_summary,
        )

        repo_b = self.tmp / "repo-b"
        init_git_repo(repo_b)
        draft_b = inspect_project(repo_b)
        register_project(self.conn, draft_b, confirmed=True)
        self.conn.commit()
        project_b = self.conn.execute(
            "select id from projects where id != ? limit 1",
            (self.project_id,),
        ).fetchone()["id"]

        persist_overnight_summary(
            self.conn,
            project_id=project_b,
            run_session_id=None,
            window_started_at="2026-06-29T22:00:00+00:00",
            window_ended_at="2026-06-29T08:00:00+00:00",
            summary={"secret_task": "Project B secret task title"},
        )
        persist_overnight_summary(
            self.conn,
            project_id=self.project_id,
            run_session_id=None,
            window_started_at="2026-06-29T22:00:00+00:00",
            window_ended_at="2026-06-29T08:00:00+00:00",
            summary={"tasks_completed": 1},
        )

        latest = get_latest_overnight_summary(self.conn, project_id=self.project_id)
        assert latest is not None
        serialized = json.dumps(latest)
        self.assertNotIn("Project B secret task title", serialized)


class OvernightPauseTests(unittest.TestCase):
    """Quiet hours request pause without unsafe worker termination."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
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
        self.goal_id = create_goal(
            self.conn, "Overnight goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, self.goal_id, "active")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_should_pause_for_quiet_hours_when_window_active(self) -> None:
        from local_cli_coordinator.overnight import (
            OvernightWindow,
            should_pause_for_quiet_hours,
        )

        window = OvernightWindow(quiet_start="22:00", quiet_end="08:00")
        now = datetime(2026, 6, 29, 23, 0, tzinfo=timezone.utc)
        decision = should_pause_for_quiet_hours(
            self.conn,
            project_id=self.project_id,
            window=window,
            now=now,
        )
        self.assertTrue(decision.should_pause)
        self.assertFalse(decision.kill_workers)

    def _default_config(self, *, overnight_enabled: bool) -> CoordinatorConfig:
        return CoordinatorConfig(
            agents={
                "worker": AgentConfig(
                    id="worker",
                    command="true",
                    capabilities=["code"],
                    max_concurrency=1,
                )
            },
            repos={
                "demo": RepoConfig(
                    id="demo",
                    path=self.repo,
                    default_branch="main",
                    remote="origin",
                    branch_prefix="coord/",
                    allow_push=False,
                    merge_policy="no_push",
                    verify_commands=["true"],
                )
            },
            policy=PolicyConfig(
                require_single_repo=False,
                require_acceptance_criteria=False,
                require_verification_commands=False,
                require_handoff_summary=False,
                max_files_touched=20,
                max_expected_minutes=60,
                max_attempts=3,
                split_if_touches_multiple_subsystems=False,
                split_if_research_and_code_are_mixed=False,
            ),
            overnight=OvernightConfig(
                quiet_start="22:00",
                quiet_end="08:00",
                enabled=overnight_enabled,
            ),
        )

    def test_maybe_pause_skips_when_overnight_disabled_during_quiet_hours(self) -> None:
        from local_cli_coordinator.autonomous_runs import (
            AutonomousRunOptions,
            get_active_run_session,
            start_run_session,
        )
        from local_cli_coordinator.overnight import maybe_pause_for_quiet_hours

        start_run_session(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            options=AutonomousRunOptions(max_iterations=3),
        )
        self.conn.commit()

        quiet_night = datetime(2026, 6, 29, 23, 0, tzinfo=timezone.utc)
        decision = maybe_pause_for_quiet_hours(
            self.conn,
            project_id=self.project_id,
            config=self._default_config(overnight_enabled=False),
            now=quiet_night,
        )
        self.assertFalse(decision.should_pause)

        active = get_active_run_session(self.conn, project_id=self.project_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active.status, "running")

    def test_maybe_pause_pauses_when_overnight_enabled_during_quiet_hours(self) -> None:
        from local_cli_coordinator.autonomous_runs import (
            AutonomousRunOptions,
            get_active_run_session,
            start_run_session,
        )
        from local_cli_coordinator.overnight import maybe_pause_for_quiet_hours

        start_run_session(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            options=AutonomousRunOptions(max_iterations=3),
        )
        self.conn.commit()

        quiet_night = datetime(2026, 6, 29, 23, 0, tzinfo=timezone.utc)
        decision = maybe_pause_for_quiet_hours(
            self.conn,
            project_id=self.project_id,
            config=self._default_config(overnight_enabled=True),
            now=quiet_night,
        )
        self.assertTrue(decision.should_pause)

        active = get_active_run_session(self.conn, project_id=self.project_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active.status, "paused")


if __name__ == "__main__":
    unittest.main()