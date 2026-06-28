from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AdminError:
    code: str
    message: str
    hint: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def envelope(
    *,
    command: str,
    ok: bool,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[AdminError] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "command": command,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": data or {},
        "warnings": warnings or [],
        "errors": [error.to_dict() for error in errors or []],
    }


def print_envelope(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def emit_envelope(payload: dict[str, Any]) -> int:
    print_envelope(payload)
    return 0 if payload["ok"] else 1