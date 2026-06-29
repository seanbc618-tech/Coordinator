"""Red tests for Phase 8 review packets v2.

Owner: Grok (Phase 8 Task 0)
Expected before implementation: ModuleNotFoundError for review_packets_v2.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, create_task, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


class ReviewPacketsV2ModuleTests(unittest.TestCase):
    def test_review_packets_v2_import(self) -> None:
        from local_cli_coordinator.review_packets_v2 import (
            ReviewPacketV2,
            get_review_packet_v2,
            write_review_packet_v2,
        )
        self.assertTrue(callable(write_review_packet_v2))
        self.assertTrue(callable(get_review_packet_v2))
        self.assertIn("verdict", ReviewPacketV2.__annotations__)


class ReviewPacketV2WriteTests(unittest.TestCase):
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
            title="Packet task",
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

    def test_write_packet_creates_json_and_markdown(self) -> None:
        from local_cli_coordinator.review_packets_v2 import write_review_packet_v2

        packet = write_review_packet_v2(
            self.conn,
            repo_root=self.repo,
            project_id=self.project_id,
            task_id=self.task_id,
            verdict="needs_human",
            suggested_action="review migration changes",
            evidence_summary={"commands_passed": 1, "commands_failed": 0},
            risk_level="high",
        )
        self.assertTrue(packet.markdown_path.is_file())
        self.assertTrue(packet.json_path.is_file())
        md = packet.markdown_path.read_text()
        self.assertIn("Packet task", md)
        self.assertIn("needs_human", md)
        payload = json.loads(packet.json_path.read_text())
        self.assertEqual(payload["task_id"], self.task_id)
        self.assertEqual(payload["verdict"], "needs_human")

    def test_packet_paths_stay_under_repo_root(self) -> None:
        from local_cli_coordinator.review_packets_v2 import write_review_packet_v2

        packet = write_review_packet_v2(
            self.conn,
            repo_root=self.repo,
            project_id=self.project_id,
            task_id=self.task_id,
            verdict="reject",
            suggested_action="fix verification",
            evidence_summary={},
            risk_level="medium",
        )
        self.assertTrue(str(packet.markdown_path).startswith(str(self.repo.resolve())))
        self.assertTrue(str(packet.json_path).startswith(str(self.repo.resolve())))

    def test_packet_redacts_secret_fields(self) -> None:
        from local_cli_coordinator.review_packets_v2 import write_review_packet_v2

        packet = write_review_packet_v2(
            self.conn,
            repo_root=self.repo,
            project_id=self.project_id,
            task_id=self.task_id,
            verdict="reject",
            suggested_action="inspect",
            evidence_summary={"env": "SECRET=abc", "commands_passed": 1},
            risk_level="high",
        )
        payload = json.loads(packet.json_path.read_text())
        serialized = json.dumps(payload)
        self.assertNotIn("SECRET=abc", serialized)


if __name__ == "__main__":
    unittest.main()