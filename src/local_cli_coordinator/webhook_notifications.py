"""Webhook notification sink with dry-run default and injectable transport."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|authorization|(?:^|_)token$)"
)


@dataclass(frozen=True)
class WebhookResult:
    status: str
    error: str = ""


class WebhookTransport(Protocol):
    def post_json(self, url: str, payload: dict[str, Any]) -> int: ...


def _redact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if _SECRET_RE.search(str(key)):
            continue
        cleaned[key] = value
    return cleaned


def deliver_webhook_notification(
    *,
    url: str,
    payload: Mapping[str, Any],
    dry_run: bool = True,
    transport: WebhookTransport | None = None,
    redact_secrets: bool = True,
) -> WebhookResult:
    if dry_run or not url.strip():
        return WebhookResult(status="skipped", error="webhook dry-run")
    body = _redact_payload(payload) if redact_secrets else dict(payload)
    if transport is None:
        return WebhookResult(status="failed", error="webhook transport missing")
    code = transport.post_json(url, body)
    if code < 200 or code >= 300:
        return WebhookResult(status="failed", error=f"webhook status {code}")
    return WebhookResult(status="sent")