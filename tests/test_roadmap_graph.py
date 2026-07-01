"""Phase 21 roadmap graph model tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.autonomous_loop_db import insert_backlog_item
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.roadmap_graph import (
    add_roadmap_edge,
    list_roadmap_nodes,
    upsert_roadmap_node,
)
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.strategy import complete_milestone, create_milestone
from tests.helpers import init_git_repo


class RoadmapGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.repo_a = self.tmp / "repo-a"
        self.repo_b = self.tmp / "repo-b"
        init_git_repo(self.repo_a)
        init_git_repo(self.repo_b)
        self.paths = RuntimePaths(
            self.home / "config",
            self.home / "data",
            self.home / "state",
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.project_a = register_project(
            self.conn, inspect_project(self.repo_a), confirmed=True
        )
        self.project_b = register_project(
            self.conn, inspect_project(self.repo_b), confirmed=True
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_upsert_node_is_project_scoped_and_idempotent_by_ref(self) -> None:
        milestone_id = create_milestone(
            self.conn,
            project_id=self.project_a,
            title="Ship graph",
        )
        first = upsert_roadmap_node(
            self.conn,
            project_id=self.project_a,
            node_type="milestone",
            title="Ship graph",
            ref_table="project_milestones",
            ref_id=str(milestone_id),
        )
        second = upsert_roadmap_node(
            self.conn,
            project_id=self.project_a,
            node_type="milestone",
            title="Ship graph",
            ref_table="project_milestones",
            ref_id=str(milestone_id),
        )
        self.assertEqual(first, second)
        nodes = list_roadmap_nodes(self.conn, project_id=self.project_a)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].project_id, self.project_a)

    def test_cross_project_edge_rejected(self) -> None:
        node_a = upsert_roadmap_node(
            self.conn,
            project_id=self.project_a,
            node_type="external",
            title="Blocker A",
        )
        node_b = upsert_roadmap_node(
            self.conn,
            project_id=self.project_b,
            node_type="external",
            title="Work B",
        )
        with self.assertRaisesRegex(ValueError, "cross_project_dependency_rejected"):
            add_roadmap_edge(
                self.conn,
                project_id=self.project_a,
                from_node_id=node_a,
                to_node_id=node_b,
                relation="blocks",
            )

    def test_cycle_creation_rejected(self) -> None:
        node_a = upsert_roadmap_node(
            self.conn,
            project_id=self.project_a,
            node_type="external",
            title="A",
        )
        node_b = upsert_roadmap_node(
            self.conn,
            project_id=self.project_a,
            node_type="external",
            title="B",
        )
        node_c = upsert_roadmap_node(
            self.conn,
            project_id=self.project_a,
            node_type="external",
            title="C",
        )
        add_roadmap_edge(
            self.conn,
            project_id=self.project_a,
            from_node_id=node_a,
            to_node_id=node_b,
            relation="blocks",
        )
        add_roadmap_edge(
            self.conn,
            project_id=self.project_a,
            from_node_id=node_b,
            to_node_id=node_c,
            relation="blocks",
        )
        with self.assertRaisesRegex(ValueError, "roadmap_cycle_rejected"):
            add_roadmap_edge(
                self.conn,
                project_id=self.project_a,
                from_node_id=node_c,
                to_node_id=node_a,
                relation="blocks",
            )

    def test_migration_creates_roadmap_tables(self) -> None:
        for table in ("roadmap_nodes", "roadmap_edges", "roadmap_snapshots"):
            row = self.conn.execute(
                "select name from sqlite_master where type='table' and name=?",
                (table,),
            ).fetchone()
            self.assertIsNotNone(row, f"missing table {table}")


if __name__ == "__main__":
    unittest.main()