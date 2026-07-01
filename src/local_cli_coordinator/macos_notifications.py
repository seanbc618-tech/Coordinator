"""macOS notification adapter behind injectable runner (tests use fakes)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MacOSNotificationResult:
    status: str
    error: str = ""


class NotificationRunner(Protocol):
    def run(self, argv: list[str]) -> int: ...


class SubprocessRunner:
    def run(self, argv: list[str]) -> int:
        completed = subprocess.run(argv, check=False, capture_output=True, text=True)
        return completed.returncode


def deliver_macos_notification(
    *,
    title: str,
    body: str,
    enabled: bool = False,
    runner: NotificationRunner | None = None,
) -> MacOSNotificationResult:
    if not enabled:
        return MacOSNotificationResult(status="skipped", error="macos channel disabled")
    safe_title = title.replace('"', "'")
    safe_body = body.replace('"', "'")
    script = (
        f'display notification "{safe_body}" with title "{safe_title}"'
    )
    argv = ["osascript", "-e", script]
    active = runner or SubprocessRunner()
    code = active.run(argv)
    if code != 0:
        return MacOSNotificationResult(status="failed", error="osascript failed")
    return MacOSNotificationResult(status="sent")