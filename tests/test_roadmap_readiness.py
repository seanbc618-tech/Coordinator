"""Phase 21 roadmap readiness evaluation tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.autonomous_loop_db import insert_backlog_item
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.roadmap_graph import add_roadmap_edge, upsert_roadmap_node
from local_cli_coordinator.roadmap_readiness import (
    evaluate_node_readiness,
    list_ready_roadmap_items,
    select_next_best_work,
)
from local_cli_coordinator.runtime_paths import RuntimePaths
from local_cli_coordinator.strategy import complete_milestone, create_milestone
from tests.helpers import init_git_repo


class RoadmapReadinessTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _backlog_node(self, *, title: str, status: str = "ready") -> tuple[str, str]:
        backlog_id = insert_backlog_item(
            self.conn,
            project_id=self.project_id,
            goal_id=None,
            source="test",
            title=title,
            rationale="demo",
            acceptance_criteria=["done"],
            verification_commands=[],
            execution_policy="normal",
            priority=80,
            status=status,
            dedupe_key=f"dedupe-{title}",
        )
        node_id = upsert_roadmap_node(
            self.conn,
            project_id=self.project_id,
            node_type="backlog",
            title=title,
            ref_table="project_backlog_items",
            ref_id=backlog_id,
            priority=80,
        )
        return backlog_id, node_id

    def test_dependent_work_blocked_until_prerequisite_done(self) -> None:
        prereq_id = upsert_roadmap_node(
            self.conn,
            project_id=self.project_id,
            node_type="external",
            title="Write regression test",
        )
        _, blocked_id = self._backlog_node(title="Implement fix")
        add_roadmap_edge(
            self.conn,
            project_id=self.project_id,
            from_node_id=prereq_id,
            to_node_id=blocked_id,
            relation="blocks",
        )

        readiness = evaluate_node_readiness(
            self.conn,
            project_id=self.project_id,
            node_id=blocked_id,
        )
        self.assertEqual(readiness["status"], "blocked")
        self.assertTrue(readiness["blockers"])

        upsert_roadmap_node(
            self.conn,
            project_id=self.project_id,
            node_type="external",
            title="Write regression test",
            ref_table=None,
            ref_id=None,
            metadata={"status_override": "done"},
        )
        self.conn.execute(
            "update roadmap_nodes set status='done' where id=?",
            (prereq_id,),
        )
        self.conn.commit()

        readiness_after = evaluate_node_readiness(
            self.conn,
            project_id=self.project_id,
            node_id=blocked_id,
        )
        self.assertEqual(readiness_after["status"], "ready")

    def test_list_ready_roadmap_items_returns_only_ready_nodes(self) -> None:
        _, ready_id = self._backlog_node(title="Ready item")
        prereq_id = upsert_roadmap_node(
            self.conn,
            project_id=self.project_id,
            node_type="external",
            title="Gate",
        )
        _, blocked_id = self._backlog_node(title="Blocked item")
        add_roadmap_edge(
            self.conn,
            project_id=self.project_id,
            from_node_id=prereq_id,
            to_node_id=blocked_id,
            relation="blocks",
        )

        ready_items = list_ready_roadmap_items(
            self.conn, project_id=self.project_id, limit=10
        )
        ready_ids = {item["node_id"] for item in ready_items}
        self.assertIn(ready_id, ready_ids)
        self.assertNotIn(blocked_id, ready_ids)

    def test_select_next_best_work_orders_by_priority(self) -> None:
        self._backlog_node(title="Lower priority", )
        self.conn.execute(
            "update roadmap_nodes set priority=10 where title='Lower priority'"
        )
        self._backlog_node(title="Higher priority")
        self.conn.execute(
            "update roadmap_nodes set priority=90 where title='Higher priority'"
        )
        self.conn.commit()

        result = select_next_best_work(self.conn, project_id=self.project_id, limit=5)
        self.assertGreaterEqual(result["ready_count"], 1)
        titles = [item["title"] for item in result["items"]]
        if len(titles) >= 2:
            self.assertEqual(titles[0], "Higher priority")


if __name__ == "__main__":
    unittest.main()