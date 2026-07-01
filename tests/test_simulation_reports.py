"""Phase 17: simulation report persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.simulation_reports import (
    create_simulation_run,
    finish_simulation_run,
    get_simulation_report,
    list_simulation_runs,
    record_simulation_event,
)


class SimulationReportsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "test.db")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_migration_creates_simulation_tables(self) -> None:
        tables = {
            row["name"]
            for row in self.conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertIn("simulation_runs", tables)
        self.assertIn("simulation_events", tables)

    def test_create_and_finish_simulation_run(self) -> None:
        run_id = create_simulation_run(
            self.conn,
            scope="global",
            horizon_hours=8.0,
            inputs={"source": "test"},
            commit=True,
        )
        finish_simulation_run(
            self.conn,
            simulation_run_id=run_id,
            status="completed",
            report={"forecast": True, "scheduled_projects": []},
            commit=True,
        )
        report = get_simulation_report(self.conn, simulation_run_id=run_id)
        assert report is not None
        self.assertTrue(report["forecast"])
        self.assertEqual(report["run"]["status"], "completed")
        self.assertEqual(report["report"]["forecast"], True)

    def test_record_simulation_event(self) -> None:
        run_id = create_simulation_run(
            self.conn, scope="project", project_id="proj-a", commit=True
        )
        record_simulation_event(
            self.conn,
            simulation_run_id=run_id,
            event_type="would_schedule",
            project_id="proj-a",
            data={"reason": "ready"},
            commit=True,
        )
        payload = get_simulation_report(self.conn, simulation_run_id=run_id)
        assert payload is not None
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["event_type"], "would_schedule")

    def test_list_simulation_runs(self) -> None:
        create_simulation_run(self.conn, scope="global", commit=True)
        create_simulation_run(
            self.conn, scope="project", project_id="proj-a", commit=True
        )
        runs = list_simulation_runs(self.conn, limit=10)
        self.assertEqual(len(runs), 2)


if __name__ == "__main__":
    unittest.main()