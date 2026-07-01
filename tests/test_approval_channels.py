"""Red tests for Phase 13 approval channel persistence and delivery.

Owner: Grok (Phase 13 Task 0)
Expected before implementation: missing approval_channels module and migration 023.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.config import NotificationsPolicyConfig
from local_cli_coordinator.db import connect, init_db


class ApprovalChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
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

    def test_migration_023_tables_exist(self) -> None:
        tables = {
            row[0]
            for row in self.conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        for name in (
            "approval_requests",
            "approval_channel_configs",
            "approval_deliveries",
            "approval_audit_events",
        ):
            self.assertIn(name, tables)

    def test_default_channel_configs_are_safe(self) -> None:
        from local_cli_coordinator.approval_channels import seed_default_channel_configs

        configs = seed_default_channel_configs(self.conn, commit=True)
        by_type = {cfg.channel_type: cfg for cfg in configs}
        self.assertTrue(by_type["file"].enabled)
        self.assertFalse(by_type["macos"].enabled)
        self.assertFalse(by_type["command"].enabled)
        self.assertFalse(by_type["stdout"].enabled)
        webhook = by_type["webhook"].config
        self.assertTrue(webhook.get("dry_run", False))

    def test_file_channel_delivery_records_audit(self) -> None:
        from local_cli_coordinator.approval_channels import (
            deliver_approval_request,
            list_audit_events,
            seed_default_channel_configs,
        )
        from local_cli_coordinator.approval_tokens import create_approval_token

        seed_default_channel_configs(self.conn, commit=True)
        _raw, request = create_approval_token(
            self.conn,
            project_id=self.project_id,
            action_method="project.task.approve",
            action_params={"task_id": "task-1"},
            commit=True,
        )
        result = deliver_approval_request(
            self.conn,
            request_id=request.id,
            project_id=self.project_id,
            state_dir=self.state_dir,
            policy=NotificationsPolicyConfig(allow_command_sink=False),
            commit=True,
        )
        self.assertTrue(any(d["status"] == "sent" for d in result["deliveries"]))
        inbox = self.state_dir / "approvals.jsonl"
        self.assertTrue(inbox.exists())
        events = list_audit_events(self.conn, approval_request_id=request.id)
        self.assertTrue(any(e.event_type == "sent" for e in events))

    def test_command_channel_skipped_without_policy(self) -> None:
        from local_cli_coordinator.approval_channels import (
            deliver_approval_request,
            upsert_channel_config,
        )
        from local_cli_coordinator.approval_tokens import create_approval_token

        upsert_channel_config(
            self.conn,
            channel_type="command",
            enabled=True,
            config_json={"argv": ["true"]},
            commit=True,
        )
        _raw, request = create_approval_token(
            self.conn,
            project_id=self.project_id,
            action_method="project.task.cancel",
            action_params={"task_id": "task-2"},
            commit=True,
        )
        result = deliver_approval_request(
            self.conn,
            request_id=request.id,
            project_id=self.project_id,
            state_dir=self.state_dir,
            policy=NotificationsPolicyConfig(allow_command_sink=False),
            commit=True,
        )
        self.assertTrue(
            any(
                d.get("channel_type") == "command" and d.get("status") == "skipped"
                for d in result["deliveries"]
            )
        )


if __name__ == "__main__":
    unittest.main()