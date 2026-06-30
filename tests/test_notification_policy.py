"""Phase 10 notification policy tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from local_cli_coordinator.config import NotificationsPolicyConfig
from local_cli_coordinator.db import connect, init_db


class NotificationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.conn = connect(self.tmp / "data.db")
        init_db(self.conn)
        self.project_id = "proj-1"
        self.conn.execute(
            """
            insert into notification_rules(
                id, enabled, sink, min_severity, project_id, event_filter,
                quiet_start, quiet_end, created_at, updated_at
            ) values (?, 1, 'file', 'warning', ?, '*', '22:00', '08:00', datetime('now'), datetime('now'))
            """,
            ("rule-1", self.project_id),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_quiet_hours_suppress_warning_not_critical(self) -> None:
        from local_cli_coordinator.notification_policy import should_deliver_notification

        night = datetime(2026, 6, 29, 23, 0, tzinfo=timezone.utc)
        warning = should_deliver_notification(
            self.conn,
            rule_id="rule-1",
            severity="warning",
            project_id=self.project_id,
            now=night,
        )
        critical = should_deliver_notification(
            self.conn,
            rule_id="rule-1",
            severity="critical",
            project_id=self.project_id,
            now=night,
        )
        self.assertFalse(warning.allowed)
        self.assertTrue(critical.allowed)

    def test_command_sink_blocked_without_policy_flag(self) -> None:
        from local_cli_coordinator.notification_policy import command_sink_allowed

        policy = NotificationsPolicyConfig(allow_command_sink=False)
        self.assertFalse(command_sink_allowed(policy, rule_enabled=True))


if __name__ == "__main__":
    unittest.main()