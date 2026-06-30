"""Phase 11 impact and where-heuristic contract tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from tests.helpers import init_git_repo


class ImpactAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        pkg = self.repo / "src" / "local_cli_coordinator"
        pkg.mkdir(parents=True)
        (pkg / "db.py").write_text("def connect():\n    pass\n")
        (pkg / "supervisor_methods.py").write_text("from .db import connect\n")
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_db.py").write_text("from local_cli_coordinator.db import connect\n")
        self.conn = connect(self.tmp / "data.db")
        init_db(self.conn)
        self.project_id = "proj-1"
        self.conn.execute(
            "insert into projects(id, canonical_path, repo_id) values (?, ?, ?)",
            (self.project_id, str(self.repo.resolve()), "demo"),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_where_query_returns_citations_and_uncertainty(self) -> None:
        from local_cli_coordinator.impact_analysis import analyze_where

        result = analyze_where(
            self.conn,
            project_id=self.project_id,
            repo_path=self.repo,
            query="database connection",
        )
        self.assertIn("matches", result)
        self.assertTrue(result["matches"])
        first = result["matches"][0]
        self.assertIn("path", first)
        self.assertIn("reason", first)
        self.assertIn("confidence", first)

    def test_impact_analysis_lists_related_paths(self) -> None:
        from local_cli_coordinator.impact_analysis import analyze_impact

        result = analyze_impact(
            self.conn,
            project_id=self.project_id,
            repo_path=self.repo,
            target_path="src/local_cli_coordinator/db.py",
        )
        paths = {item["path"] for item in result["related"]}
        self.assertIn("src/local_cli_coordinator/supervisor_methods.py", paths)
        self.assertIn("tests/test_db.py", paths)
        self.assertIn("confidence", result)


if __name__ == "__main__":
    unittest.main()