"""Red tests for Phase 13 one-time approval tokens.

Owner: Grok (Phase 13 Task 0)
Expected before implementation: missing approval_tokens module and migration 023.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_cli_coordinator.db import connect, init_db


class ApprovalTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.conn = connect(self.tmp / "data.db")
        init_db(self.conn)
        self.project_id = "proj-1"
        self.conn.execute(
            "insert into projects(id, canonical_path, repo_id) values (?, ?, ?)",
            (self.project_id, str(self.tmp / "repo"), "demo"),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_token_stored_as_hash_only(self) -> None:
        from local_cli_coordinator.approval_tokens import create_approval_token
        from local_cli_coordinator.approval_channels import get_approval_request

        raw, request = create_approval_token(
            self.conn,
            project_id=self.project_id,
            action_method="project.task.approve",
            action_params={"task_id": "task-1"},
            commit=True,
        )
        self.assertTrue(len(raw) >= 24)
        row = self.conn.execute(
            "select token_hash, token_hint from approval_requests where id = ?",
            (request.id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row["token_hash"], raw)
        self.assertEqual(row["token_hint"], raw[-4:])
        loaded = get_approval_request(self.conn, request_id=request.id)
        assert loaded is not None
        self.assertNotIn(raw, json.dumps(loaded.__dict__))

    def test_verify_and_consume_single_use(self) -> None:
        from local_cli_coordinator.approval_tokens import (
            consume_approval_token,
            create_approval_token,
            verify_approval_token,
        )

        raw, request = create_approval_token(
            self.conn,
            project_id=self.project_id,
            action_method="project.task.retry",
            action_params={"task_id": "task-2"},
            commit=True,
        )
        verified = verify_approval_token(
            self.conn, raw_token=raw, project_id=self.project_id
        )
        self.assertEqual(verified.id, request.id)
        consume_approval_token(
            self.conn, raw_token=raw, project_id=self.project_id, commit=True
        )
        with self.assertRaises(ValueError):
            verify_approval_token(
                self.conn, raw_token=raw, project_id=self.project_id
            )

    def test_replay_after_approval_rejected(self) -> None:
        from local_cli_coordinator.approval_callbacks import reject_approval_token
        from local_cli_coordinator.approval_tokens import (
            create_approval_token,
            verify_approval_token,
        )

        raw, _request = create_approval_token(
            self.conn,
            project_id=self.project_id,
            action_method="project.task.cancel",
            action_params={"task_id": "task-3"},
            commit=True,
        )
        reject_approval_token(
            self.conn,
            raw_token=raw,
            project_id=self.project_id,
            decided_by="tester",
            commit=True,
        )
        with self.assertRaises(ValueError):
            verify_approval_token(
                self.conn, raw_token=raw, project_id=self.project_id
            )

    def test_expired_token_rejected(self) -> None:
        from local_cli_coordinator.approval_tokens import (
            create_approval_token,
            verify_approval_token,
        )

        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        raw, _request = create_approval_token(
            self.conn,
            project_id=self.project_id,
            action_method="project.deliver",
            action_params={"task_id": "task-4"},
            expires_at=past,
            commit=True,
        )
        with self.assertRaises(ValueError):
            verify_approval_token(
                self.conn, raw_token=raw, project_id=self.project_id
            )

    def test_cross_project_token_rejected(self) -> None:
        from local_cli_coordinator.approval_tokens import (
            create_approval_token,
            verify_approval_token,
        )

        raw, _request = create_approval_token(
            self.conn,
            project_id=self.project_id,
            action_method="project.task.approve",
            action_params={"task_id": "task-5"},
            commit=True,
        )
        with self.assertRaises(ValueError):
            verify_approval_token(
                self.conn, raw_token=raw, project_id="other-project"
            )


if __name__ == "__main__":
    unittest.main()