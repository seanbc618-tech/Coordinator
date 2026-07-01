"""Red tests for Phase 13 macOS notification adapter (fake runner only).

Owner: Grok (Phase 13 Task 0)
Expected before implementation: missing macos_notifications module.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field


@dataclass
class FakeRunner:
    calls: list[list[str]] = field(default_factory=list)

    def run(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        return 0


class MacOSNotificationTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        from local_cli_coordinator.macos_notifications import deliver_macos_notification

        runner = FakeRunner()
        result = deliver_macos_notification(
            title="Approval needed",
            body="Cancel task?",
            enabled=False,
            runner=runner,
        )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(runner.calls, [])

    def test_enabled_uses_fake_runner_not_real_ui(self) -> None:
        from local_cli_coordinator.macos_notifications import deliver_macos_notification

        runner = FakeRunner()
        result = deliver_macos_notification(
            title="Approval needed",
            body="Repair CI?",
            enabled=True,
            runner=runner,
        )
        self.assertEqual(result.status, "sent")
        self.assertEqual(len(runner.calls), 1)
        argv = runner.calls[0]
        self.assertIn("osascript", argv[0])
        joined = " ".join(argv)
        self.assertNotIn("secret=", joined.lower())
        self.assertNotIn("password", joined.lower())


if __name__ == "__main__":
    unittest.main()