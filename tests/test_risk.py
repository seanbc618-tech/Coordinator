"""Red tests for Phase 8 task risk assessment.

Owner: Grok (Phase 8 Task 0)
Expected before implementation: ModuleNotFoundError for risk.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class RiskModuleTests(unittest.TestCase):
    def test_risk_module_import(self) -> None:
        from local_cli_coordinator.risk import (
            RiskAssessment,
            assess_task_risk,
            get_latest_risk_assessment,
        )
        self.assertTrue(callable(assess_task_risk))
        self.assertTrue(callable(get_latest_risk_assessment))
        self.assertIn("risk_level", RiskAssessment.__annotations__)


class RiskAssessmentTests(unittest.TestCase):
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
        self.task_id = create_task(
            self.conn,
            title="Risky task",
            repo="test-repo",
            source_path="task.md",
            priority="normal",
            capabilities=["code"],
            goal="goal",
            acceptance_criteria=["done"],
            verification_commands=["true"],
            project_id=self.project_id,
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migration_file_triggers_high_risk(self) -> None:
        from local_cli_coordinator.risk import assess_task_risk, get_latest_risk_assessment

        assessment = assess_task_risk(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            changed_files=["migrations/018_evidence.sql", "src/app.py"],
            diff_text="ALTER TABLE tasks ADD COLUMN x INTEGER",
        )
        self.assertIn(assessment.risk_level, ("medium", "high"))
        self.assertTrue(assessment.requires_human_review)
        latest = get_latest_risk_assessment(
            self.conn, project_id=self.project_id, task_id=self.task_id
        )
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertTrue(any("migration" in r.lower() for r in latest.reasons))

    def test_secret_looking_diff_triggers_human_review(self) -> None:
        from local_cli_coordinator.risk import assess_task_risk

        assessment = assess_task_risk(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            changed_files=["config/settings.py"],
            diff_text="+API_KEY=super-secret-token-value",
        )
        self.assertTrue(assessment.requires_human_review)
        self.assertTrue(
            any("secret" in r.lower() or "credential" in r.lower() for r in assessment.reasons)
        )

    def test_no_change_code_task_is_risky(self) -> None:
        from local_cli_coordinator.risk import assess_task_risk

        assessment = assess_task_risk(
            self.conn,
            project_id=self.project_id,
            task_id=self.task_id,
            changed_files=[],
            diff_text="",
            capabilities=["code"],
        )
        self.assertTrue(assessment.requires_human_review or assessment.risk_level != "low")


if __name__ == "__main__":
    unittest.main()