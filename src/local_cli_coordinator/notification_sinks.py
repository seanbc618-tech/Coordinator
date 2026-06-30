"""Local notification sinks for operator control tower."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .process import run_command


@dataclass(frozen=True)
class SinkResult:
    status: str
    error: str = ""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def deliver_to_file_sink(path: Path, *, payload: Mapping[str, Any]) -> SinkResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": _iso_now(), **dict(payload)}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return SinkResult(status="sent")


def deliver_to_stdout_sink(*, payload: Mapping[str, Any]) -> SinkResult:
    print(json.dumps({"timestamp": _iso_now(), **dict(payload)}))
    return SinkResult(status="sent")


def deliver_to_command_sink(
    argv: list[str],
    *,
    payload: Mapping[str, Any],
) -> SinkResult:
    """Run command with JSON payload on stdin — never shell-interpolate."""
    import subprocess

    completed = subprocess.run(
        argv,
        input=json.dumps(dict(payload)),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return SinkResult(
            status="failed",
            error=completed.stderr.strip() or "command failed",
        )
    return SinkResult(status="sent")


def deliver_notification(
    *,
    sink: str,
    payload: Mapping[str, Any],
    state_dir: Path | None = None,
    command_argv: list[str] | None = None,
) -> SinkResult:
    if sink == "file":
        target = (state_dir or Path("state")) / "notifications.jsonl"
        return deliver_to_file_sink(target, payload=payload)
    if sink == "stdout":
        return deliver_to_stdout_sink(payload=payload)
    if sink == "command":
        if not command_argv:
            return SinkResult(status="failed", error="command sink argv missing")
        return deliver_to_command_sink(command_argv, payload=payload)
    return SinkResult(status="failed", error=f"unknown sink: {sink}")