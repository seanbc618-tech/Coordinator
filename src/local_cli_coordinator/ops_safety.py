"""Dry-run plans and confirm tokens for destructive CLI operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpsPlan:
    action: str
    summary: str
    items: list[str]
    extra: dict[str, Any]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "items": list(self.items),
            "extra": self.extra,
        }


def confirm_token_for_plan(plan: OpsPlan) -> str:
    payload = json.dumps(plan.canonical_payload(), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{plan.action}:{payload}".encode("utf-8")).hexdigest()
    return digest[:16]


def verify_confirm_token(plan: OpsPlan, token: str | None) -> str | None:
    if not token:
        return "confirm_required: --apply requires --confirm <token> from dry-run"
    expected = confirm_token_for_plan(plan)
    if token != expected:
        return f"confirm_mismatch: expected token {expected}"
    return None