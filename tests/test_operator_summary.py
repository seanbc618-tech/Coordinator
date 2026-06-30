"""Phase 10 operator summary tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.operator_inbox import upsert_operator_item
from tests.helpers import init_git_repo


class OperatorSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.conn = connect(self.tmp / "data.db")
        init_db(self.conn)
        self.project_id = "proj-1"
        self.conn.execute(
            "insert into projects(id, canonical_path, repo_id) values (?, ?, ?)",
            (self.project_id, str(self.repo.resolve()), "demo"),
        )
        upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="delivery",
            source_id="12",
            severity="error",
            title="CI failed",
            dedupe_key="delivery:12:ci_failed",
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_project_summary_counts_by_severity(self) -> None:
        from local_cli_coordinator.operator_summary import build_project_summary

        summary = build_project_summary(self.conn, project_id=self.project_id)
        self.assertEqual(summary["project_id"], self.project_id)
        counts = summary["counts"]
        self.assertGreaterEqual(counts.get("error", 0), 1)

    def test_summary_highlights_are_redacted(self) -> None:
        from local_cli_coordinator.operator_summary import build_project_summary

        upsert_operator_item(
            self.conn,
            project_id=self.project_id,
            source_type="task",
            source_id="task-1",
            severity="warning",
            title="Task with token=api_secret_value",
            summary="env: SUPER_SECRET=abc123",
            dedupe_key="task:task-1:warn",
        )
        self.conn.commit()
        summary = build_project_summary(self.conn, project_id=self.project_id)
        blob = json.dumps(summary)
        self.assertNotIn("api_secret_value", blob)
        self.assertNotIn("SUPER_SECRET=abc123", blob)

    def test_morning_summary_kind(self) -> None:
        from local_cli_coordinator.operator_summary import build_morning_summary

        summary = build_morning_summary(self.conn, project_id=self.project_id)
        self.assertEqual(summary["summary_kind"], "morning")


if __name__ == "__main__":
    unittest.main()