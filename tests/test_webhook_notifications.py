"""Red tests for Phase 13 webhook dry-run notifications.

Owner: Grok (Phase 13 Task 0)
Expected before implementation: missing webhook_notifications module.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeTransport:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def post_json(self, url: str, payload: dict[str, Any]) -> int:
        self.calls.append((url, payload))
        return 202


class WebhookNotificationTests(unittest.TestCase):
    def test_dry_run_does_not_call_transport(self) -> None:
        from local_cli_coordinator.webhook_notifications import (
            deliver_webhook_notification,
        )

        transport = FakeTransport()
        result = deliver_webhook_notification(
            url="https://example.test/hook",
            payload={"title": "Approval", "token_hint": "abcd"},
            dry_run=True,
            transport=transport,
        )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(transport.calls, [])

    def test_live_mode_uses_transport_without_secrets(self) -> None:
        from local_cli_coordinator.webhook_notifications import (
            deliver_webhook_notification,
        )

        transport = FakeTransport()
        result = deliver_webhook_notification(
            url="https://example.test/hook",
            payload={
                "title": "Approval",
                "token_hint": "wxyz",
                "api_key": "should-not-appear",
            },
            dry_run=False,
            transport=transport,
            redact_secrets=True,
        )
        self.assertEqual(result.status, "sent")
        self.assertEqual(len(transport.calls), 1)
        _url, body = transport.calls[0]
        self.assertEqual(body["token_hint"], "wxyz")
        self.assertNotIn("api_key", body)


if __name__ == "__main__":
    unittest.main()