"""Phase 21 roadmap markdown import tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.roadmap_import import import_roadmap_markdown
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class RoadmapImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config",
            self.home / "data",
            self.home / "state",
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.project_id = register_project(
            self.conn, inspect_project(self.repo), confirmed=True
        )
        self.conn.commit()
        self.roadmap_path = self.repo / "roadmap.md"
        self.roadmap_path.write_text(
            "# Milestone 1\n\n- [ ] First backlog item\n- [ ] Second backlog item\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_import_dry_run_writes_no_db_rows(self) -> None:
        before_nodes = self.conn.execute(
            "select count(*) as c from roadmap_nodes"
        ).fetchone()["c"]
        before_edges = self.conn.execute(
            "select count(*) as c from roadmap_edges"
        ).fetchone()["c"]

        result = import_roadmap_markdown(
            self.conn,
            project_id=self.project_id,
            repo_root=self.repo,
            path=self.roadmap_path,
            apply=False,
        )

        self.assertFalse(result["applied"])
        self.assertGreater(len(result["proposed_nodes"]), 0)
        after_nodes = self.conn.execute(
            "select count(*) as c from roadmap_nodes"
        ).fetchone()["c"]
        after_edges = self.conn.execute(
            "select count(*) as c from roadmap_edges"
        ).fetchone()["c"]
        self.assertEqual(before_nodes, after_nodes)
        self.assertEqual(before_edges, after_edges)

    def test_import_rejects_path_outside_repo_root(self) -> None:
        outside = self.tmp / "outside.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "roadmap_import_outside_repo"):
            import_roadmap_markdown(
                self.conn,
                project_id=self.project_id,
                repo_root=self.repo,
                path=outside,
                apply=False,
            )

    def test_import_apply_writes_nodes_and_edges(self) -> None:
        result = import_roadmap_markdown(
            self.conn,
            project_id=self.project_id,
            repo_root=self.repo,
            path=self.roadmap_path,
            apply=True,
        )
        self.assertTrue(result["applied"])
        node_count = self.conn.execute(
            "select count(*) as c from roadmap_nodes where project_id=?",
            (self.project_id,),
        ).fetchone()["c"]
        self.assertGreater(node_count, 0)


if __name__ == "__main__":
    unittest.main()