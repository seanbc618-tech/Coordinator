"""Red tests for Phase 6C autonomous run session persistence.

Owner: Grok (Phase 6C Task 0)
Expected before implementation: ``ModuleNotFoundError`` for ``autonomous_runs``
or missing ``autonomous_run_sessions`` table.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.goals import create_goal, transition_goal
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class AutonomousRunSessionTests(unittest.TestCase):
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
            "select id from projects limit 1"
        ).fetchone()["id"]
        self.goal_id = create_goal(
            self.conn, "Run goal", "test", project_id=self.project_id
        )
        transition_goal(self.conn, self.goal_id, "active")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_start_run_session_creates_one_active_session_per_project(self) -> None:
        from local_cli_coordinator.autonomous_runs import (
            AutonomousRunOptions,
            get_active_run_session,
            start_run_session,
        )

        options = AutonomousRunOptions(max_iterations=5)
        first = start_run_session(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            options=options,
        )
        second = start_run_session(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            options=options,
        )
        self.conn.commit()

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.status, "running")
        active = get_active_run_session(self.conn, project_id=self.project_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active.id, first.id)

    def test_pause_resume_stop_update_session_status(self) -> None:
        from local_cli_coordinator.autonomous_runs import (
            AutonomousRunOptions,
            get_active_run_session,
            pause_run_session,
            resume_run_session,
            start_run_session,
            stop_run_session,
        )

        start_run_session(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            options=AutonomousRunOptions(),
        )
        self.conn.commit()

        paused = pause_run_session(self.conn, project_id=self.project_id)
        self.conn.commit()
        self.assertEqual(paused.status, "paused")

        resumed = resume_run_session(self.conn, project_id=self.project_id)
        self.conn.commit()
        self.assertEqual(resumed.status, "running")

        stopped = stop_run_session(
            self.conn, project_id=self.project_id, reason="operator stop"
        )
        self.conn.commit()
        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(stopped.stop_reason, "operator stop")
        self.assertIsNone(
            get_active_run_session(self.conn, project_id=self.project_id)
        )

    def test_record_run_step_applies_idle_backoff(self) -> None:
        from local_cli_coordinator.autonomous_runs import (
            AutonomousRunOptions,
            project_has_runnable_run_session,
            record_run_step,
            start_run_session,
        )

        session = start_run_session(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            options=AutonomousRunOptions(idle_backoff_seconds=30),
        )
        self.conn.commit()

        updated = record_run_step(
            self.conn,
            run_id=session.id,
            decision="wait",
            loop_iteration_id="iter-1",
            idle_backoff_seconds=30,
            reason="no backlog ready",
            evaluated_count=0,
            admitted_count=0,
            generated_count=0,
        )
        self.conn.commit()

        self.assertEqual(updated.idle_iteration_count, 1)
        self.assertIsNotNone(updated.next_tick_after)
        now = datetime.now(timezone.utc)
        next_tick = datetime.fromisoformat(updated.next_tick_after)
        self.assertGreater(next_tick, now)
        self.assertFalse(
            project_has_runnable_run_session(
                self.conn, project_id=self.project_id, now=now.isoformat()
            )
        )

    def test_record_run_step_stops_after_max_iterations(self) -> None:
        from local_cli_coordinator.autonomous_runs import (
            AutonomousRunOptions,
            record_run_step,
            start_run_session,
        )

        session = start_run_session(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            options=AutonomousRunOptions(max_iterations=2),
        )
        self.conn.commit()

        record_run_step(
            self.conn,
            run_id=session.id,
            decision="generate",
            loop_iteration_id="iter-1",
            idle_backoff_seconds=30,
            reason="generated backlog",
            evaluated_count=0,
            admitted_count=0,
            generated_count=1,
        )
        final = record_run_step(
            self.conn,
            run_id=session.id,
            decision="wait",
            loop_iteration_id="iter-2",
            idle_backoff_seconds=30,
            reason="no backlog ready",
            evaluated_count=0,
            admitted_count=0,
            generated_count=0,
        )
        self.conn.commit()

        self.assertEqual(final.iteration_count, 2)
        self.assertEqual(final.status, "completed")
        self.assertEqual(final.stop_reason, "max iterations reached")

    def test_record_run_step_stops_after_idle_limit(self) -> None:
        from local_cli_coordinator.autonomous_runs import (
            AutonomousRunOptions,
            record_run_step,
            start_run_session,
        )

        session = start_run_session(
            self.conn,
            project_id=self.project_id,
            goal_id=self.goal_id,
            options=AutonomousRunOptions(max_idle_iterations=2, max_iterations=100),
        )
        self.conn.commit()

        record_run_step(
            self.conn,
            run_id=session.id,
            decision="wait",
            loop_iteration_id="iter-1",
            idle_backoff_seconds=30,
            reason="no active goal",
            evaluated_count=0,
            admitted_count=0,
            generated_count=0,
        )
        final = record_run_step(
            self.conn,
            run_id=session.id,
            decision="wait",
            loop_iteration_id="iter-2",
            idle_backoff_seconds=30,
            reason="running task",
            evaluated_count=0,
            admitted_count=0,
            generated_count=0,
        )
        self.conn.commit()

        self.assertEqual(final.idle_iteration_count, 2)
        self.assertEqual(final.status, "completed")
        self.assertEqual(final.stop_reason, "idle limit reached")


if __name__ == "__main__":
    unittest.main()