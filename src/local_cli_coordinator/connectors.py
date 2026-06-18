"""Command-backed connector interface for discovery and persistence steps."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

FAILURES_FILENAME = "connector_failures.jsonl"


@dataclass(frozen=True)
class ConnectorResult:
    output: dict[str, object] | None
    failures: list[str]


def _failures_path(root: Path) -> Path:
    return root / "state" / "connectors" / FAILURES_FILENAME


def log_connector_failure(root: Path, connector_id: str, message: str) -> None:
    path = _failures_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "connector": connector_id,
        "message": message,
        "logged_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")


def load_connector_failures(root: Path) -> list[dict[str, str]]:
    path = _failures_path(root)
    if not path.is_file():
        return []
    failures: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                raise ValueError("connector failure JSONL line must be a JSON object")
            failures.append({str(key): str(value) for key, value in payload.items()})
    return failures


def run_connector(
    *,
    root: Path,
    connector_id: str,
    command: str,
    payload: dict[str, object] | None = None,
) -> ConnectorResult:
    stdin_data = ""
    if payload is not None:
        stdin_data = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    completed = subprocess.run(
        command,
        shell=True,
        cwd=root,
        input=stdin_data,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        message = f"connector {connector_id!r} failed with exit code {completed.returncode}"
        stderr = completed.stderr.strip()
        if stderr:
            message = f"{message}: {stderr}"
        log_connector_failure(root, connector_id, message)
        return ConnectorResult(output=None, failures=[message])

    stdout = completed.stdout.strip()
    if not stdout:
        return ConnectorResult(output={}, failures=[])

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        message = f"connector {connector_id!r} returned invalid JSON"
        log_connector_failure(root, connector_id, message)
        return ConnectorResult(output=None, failures=[message])

    if not isinstance(parsed, dict):
        message = f"connector {connector_id!r} returned non-object JSON"
        log_connector_failure(root, connector_id, message)
        return ConnectorResult(output=None, failures=[message])

    return ConnectorResult(output=parsed, failures=[])