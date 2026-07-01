"""Phase 17: policy and approval forecasts."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.approval_callbacks import forecast_method_approval
from local_cli_coordinator.config import (
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
)
from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.policy_forecast import (
    classify_task_risk_forecast,
    forecast_approval_requirements,
    forecast_budget_pressure,
    forecast_policy_blocks,
)
from local_cli_coordinator.projects import inspect_project, register_project
from tests.helpers import init_git_repo


def _config() -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={},
        repos={},
        policy=PolicyConfig(
            require_single_repo=False,
            require_acceptance_criteria=False,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=5,
            max_expected_minutes=60,
            max_attempts=3,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
            max_tasks_per_day=4,
        ),
        daemon_policy=DaemonPolicyConfig(),
    )


class PolicyForecastTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "test.db")
        init_db(self.conn)
        self.repo = Path(self.tmp.name) / "repo"
        init_git_repo(self.repo)
        migrations_dir = self.repo / "migrations"
        migrations_dir.mkdir(parents=True, exist_ok=True)
        (migrations_dir / "001.sql").write_text("alter table users;\n")
        self.project_id = register_project(
            self.conn, inspect_project(self.repo), confirmed=True
        )
        self.config = _config()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_classify_task_risk_forecast_is_read_only(self) -> None:
        before = self.conn.execute(
            "select count(*) as c from task_risk_assessments"
        ).fetchone()["c"]
        forecast = classify_task_risk_forecast(
            changed_files=["migrations/001.sql"],
            capabilities=["code"],
            max_files_touched=5,
        )
        after = self.conn.execute(
            "select count(*) as c from task_risk_assessments"
        ).fetchone()["c"]
        self.assertEqual(before, after)
        self.assertTrue(forecast["forecast"])
        self.assertIn("migration", " ".join(forecast["reasons"]).lower())

    def test_forecast_method_approval_for_cancel(self) -> None:
        forecast = forecast_method_approval(
            "project.task.cancel",
            project_id=self.project_id,
            task_id="task-1",
        )
        assert forecast is not None
        self.assertTrue(forecast["forecast"])
        self.assertEqual(forecast["action_method"], "project.task.cancel")

    def test_forecast_budget_pressure(self) -> None:
        self.conn.execute(
            """
            insert into daemon_runs(started_at, tasks_processed, failures)
            values (datetime('now'), 3, 0)
            """
        )
        self.conn.commit()
        budget = forecast_budget_pressure(self.conn, self.config)
        self.assertEqual(budget["used"], 3)
        self.assertEqual(budget["limit"], 4)
        self.assertEqual(budget["remaining"], 1)
        self.assertTrue(budget["forecast"])

    def test_forecast_policy_blocks_daily_limit(self) -> None:
        self.conn.executescript(
            textwrap.dedent("""
                insert into daemon_runs(started_at, tasks_processed, failures)
                values (datetime('now'), 4, 0);
                insert into daemon_runs(started_at, tasks_processed, failures)
                values (datetime('now'), 1, 0);
            """)
        )
        self.conn.commit()
        blocks = forecast_policy_blocks(
            self.conn, self.config, project_id=self.project_id
        )
        self.assertTrue(any("daily task limit" in b["reason"] for b in blocks))

    def test_forecast_approval_requirements_includes_policy_gates(self) -> None:
        forecasts = forecast_approval_requirements(
            self.conn, project_id=self.project_id
        )
        methods = {item["action_method"] for item in forecasts if "action_method" in item}
        self.assertIn("project.task.cancel", methods)
        self.assertIn("project.deliver", methods)


if __name__ == "__main__":
    unittest.main()