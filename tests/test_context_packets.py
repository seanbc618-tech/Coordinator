"""Phase 11 bounded context packet contract tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from tests.helpers import init_git_repo


class ContextPacketTests(unittest.TestCase):
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
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_cards(self) -> str:
        from local_cli_coordinator.project_brain import (
            create_brain_snapshot,
            upsert_brain_card,
        )

        snapshot = create_brain_snapshot(
            self.conn, project_id=self.project_id, repo_path=self.repo
        )
        for idx in range(5):
            upsert_brain_card(
                self.conn,
                project_id=self.project_id,
                snapshot_id=snapshot.id,
                card_type="component",
                title=f"module-{idx}",
                summary="x" * 500,
                citations=[{"path": f"src/m{idx}.py"}],
            )
        self.conn.commit()
        return snapshot.id

    def test_build_packet_includes_required_fields(self) -> None:
        from local_cli_coordinator.context_packets import build_context_packet

        self._seed_cards()
        packet = build_context_packet(
            self.conn,
            project_id=self.project_id,
            purpose="commander_chat",
            token_budget=4000,
            query="where is db code?",
        )
        self.assertEqual(packet["project_id"], self.project_id)
        self.assertEqual(packet["purpose"], "commander_chat")
        self.assertEqual(packet["token_budget"], 4000)
        self.assertIn("summary", packet)
        self.assertIn("cards", packet)
        self.assertIn("citations", packet)
        self.assertIn("memories", packet)
        self.assertIn("redactions", packet)

    def test_build_packet_prunes_low_priority_cards_under_tight_budget(self) -> None:
        from local_cli_coordinator.context_packets import build_context_packet

        self._seed_cards()
        packet = build_context_packet(
            self.conn,
            project_id=self.project_id,
            purpose="task_prompt",
            token_budget=200,
            query="need db layer",
        )
        self.assertLess(len(packet["cards"]), 5)
        self.assertIn("pruned", packet)
        self.assertGreater(packet["pruned"]["cards"], 0)

    def test_build_packet_fails_only_when_core_context_exceeds_budget(self) -> None:
        from local_cli_coordinator.context_packets import (
            ContextPacketBudgetError,
            build_context_packet,
        )

        self._seed_cards()
        with self.assertRaises(ContextPacketBudgetError):
            build_context_packet(
                self.conn,
                project_id=self.project_id,
                purpose="task_prompt",
                token_budget=10,
                query="x" * 500,
                required_summary="mandatory " * 200,
            )

    def test_build_packet_warns_when_repo_dirty_or_head_changed(self) -> None:
        from local_cli_coordinator.context_packets import build_context_packet
        from local_cli_coordinator.project_brain import create_brain_snapshot

        create_brain_snapshot(
            self.conn, project_id=self.project_id, repo_path=self.repo
        )
        self.conn.commit()
        (self.repo / "dirty.txt").write_text("wip\n")
        packet = build_context_packet(
            self.conn,
            project_id=self.project_id,
            purpose="commander_chat",
            token_budget=4000,
            repo_path=self.repo,
        )
        warning = packet.get("stale_warning", "")
        self.assertTrue(warning)
        self.assertIn("STALE", warning.upper())

    def test_build_packet_redacts_secrets_in_summary(self) -> None:
        from local_cli_coordinator.context_packets import build_context_packet
        from local_cli_coordinator.project_brain import (
            create_brain_snapshot,
            upsert_brain_card,
        )

        snapshot = create_brain_snapshot(
            self.conn, project_id=self.project_id, repo_path=self.repo
        )
        upsert_brain_card(
            self.conn,
            project_id=self.project_id,
            snapshot_id=snapshot.id,
            card_type="hazard",
            title="auth",
            summary="token=abc123-secret",
            citations=[{"path": "config.toml"}],
        )
        self.conn.commit()
        packet = build_context_packet(
            self.conn,
            project_id=self.project_id,
            purpose="review",
            token_budget=4000,
        )
        blob = json.dumps(packet)
        self.assertNotIn("abc123-secret", blob)
        self.assertGreaterEqual(packet["redactions"]["count"], 1)


if __name__ == "__main__":
    unittest.main()