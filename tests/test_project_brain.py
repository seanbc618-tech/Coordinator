"""Phase 11 project brain persistence and memory contract tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from tests.helpers import init_git_repo


class ProjectBrainPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.conn = connect(self.tmp / "data.db")
        init_db(self.conn)
        self.project_a = "proj-a"
        self.project_b = "proj-b"
        for pid, suffix in ((self.project_a, "a"), (self.project_b, "b")):
            repo = self.tmp / f"repo-{suffix}"
            init_git_repo(repo)
            self.conn.execute(
                "insert into projects(id, canonical_path, repo_id) values (?, ?, ?)",
                (pid, str(repo.resolve()), f"demo-{suffix}"),
            )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_snapshot_records_git_state(self) -> None:
        from local_cli_coordinator.project_brain import create_brain_snapshot

        snapshot = create_brain_snapshot(
            self.conn,
            project_id=self.project_a,
            repo_path=self.tmp / "repo-a",
        )
        self.conn.commit()
        self.assertEqual(snapshot.project_id, self.project_a)
        self.assertTrue(snapshot.git_head)
        self.assertGreaterEqual(snapshot.file_count, 1)
        self.assertEqual(snapshot.status, "ready")

    def test_upsert_brain_card_requires_valid_card_type(self) -> None:
        from local_cli_coordinator.project_brain import (
            create_brain_snapshot,
            upsert_brain_card,
        )

        snapshot = create_brain_snapshot(
            self.conn,
            project_id=self.project_a,
            repo_path=self.tmp / "repo-a",
        )
        with self.assertRaises(ValueError):
            upsert_brain_card(
                self.conn,
                project_id=self.project_a,
                snapshot_id=snapshot.id,
                card_type="invalid",
                title="Bad",
                summary="x",
                citations=[{"path": "README.md"}],
            )

    def test_memory_dedupes_by_source_and_type(self) -> None:
        from local_cli_coordinator.project_brain import upsert_brain_memory

        first = upsert_brain_memory(
            self.conn,
            project_id=self.project_a,
            source_type="task",
            source_id="task-1",
            memory_type="failure",
            title="pytest failed",
            summary="ImportError in db.py",
        )
        second = upsert_brain_memory(
            self.conn,
            project_id=self.project_a,
            source_type="task",
            source_id="task-1",
            memory_type="failure",
            title="pytest failed",
            summary="ImportError in db.py (updated)",
        )
        self.conn.commit()
        self.assertEqual(first.id, second.id)
        rows = self.conn.execute(
            "select count(*) as c from project_brain_memories where project_id = ?",
            (self.project_a,),
        ).fetchone()
        self.assertEqual(int(rows["c"]), 1)

    def test_list_cards_is_project_scoped(self) -> None:
        from local_cli_coordinator.project_brain import (
            create_brain_snapshot,
            list_brain_cards,
            upsert_brain_card,
        )

        snap_a = create_brain_snapshot(
            self.conn, project_id=self.project_a, repo_path=self.tmp / "repo-a"
        )
        snap_b = create_brain_snapshot(
            self.conn, project_id=self.project_b, repo_path=self.tmp / "repo-b"
        )
        upsert_brain_card(
            self.conn,
            project_id=self.project_a,
            snapshot_id=snap_a.id,
            card_type="component",
            title="db layer",
            summary="SQLite helpers",
            citations=[{"path": "src/db.py"}],
        )
        upsert_brain_card(
            self.conn,
            project_id=self.project_b,
            snapshot_id=snap_b.id,
            card_type="component",
            title="other",
            summary="secret project",
            citations=[{"path": "other.py"}],
        )
        self.conn.commit()
        cards = list_brain_cards(self.conn, project_id=self.project_a)
        titles = {c.title for c in cards}
        self.assertIn("db layer", titles)
        self.assertNotIn("other", titles)

    def test_persist_context_packet_round_trip(self) -> None:
        from local_cli_coordinator.project_brain import persist_context_packet

        packet = {
            "project_id": self.project_a,
            "purpose": "task_prompt",
            "token_budget": 4000,
            "summary": "bounded context",
            "cards": [],
            "citations": [{"path": "README.md"}],
            "memories": [],
            "redactions": {"count": 0, "patterns": []},
        }
        saved = persist_context_packet(
            self.conn,
            project_id=self.project_a,
            purpose="task_prompt",
            token_budget=4000,
            packet=packet,
            task_id="task-abc",
        )
        self.conn.commit()
        row = self.conn.execute(
            "select packet_json from project_context_packets where id = ?",
            (saved.id,),
        ).fetchone()
        loaded = json.loads(row["packet_json"])
        self.assertEqual(loaded["purpose"], "task_prompt")
        self.assertEqual(loaded["token_budget"], 4000)


if __name__ == "__main__":
    unittest.main()