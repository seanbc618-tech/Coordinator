"""Evidence-rich review packets for Phase 8 human review gates."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .db import get_task
from .evidence import list_task_evidence
from .risk import get_latest_risk_assessment

_SECRET_REDACT = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|token)\s*=\s*)(\S+)"
)
_ENV_REDACT = re.compile(r"(?i)(env[\"']?\s*:\s*[\"'])([^\"']+)([\"'])")

_PACKET_DIR = Path(".coordinator") / "review_packets_v2"


@dataclass(frozen=True)
class ReviewPacketV2:
    project_id: str
    task_id: str
    verdict: str
    json_path: Path
    markdown_path: Path
    suggested_action: str
    risk_level: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_repo_path(repo_root: Path, relative: Path) -> Path:
    root = repo_root.resolve()
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise ValueError("review packet path escapes repo root")
    return target


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        text = _SECRET_REDACT.sub(r"\1[REDACTED]", value)
        return _ENV_REDACT.sub(r"\1[REDACTED]\3", text)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _evidence_summary(evidence_rows) -> dict[str, Any]:
    commands_passed = sum(
        1 for row in evidence_rows if row.evidence_type == "command" and row.status == "passed"
    )
    commands_failed = sum(
        1 for row in evidence_rows if row.evidence_type == "command" and row.status == "failed"
    )
    changed_files: list[str] = []
    for row in evidence_rows:
        if row.evidence_type != "diff":
            continue
        files = row.data.get("changed_files")
        if isinstance(files, list):
            changed_files.extend(str(path) for path in files)
    acceptance_covered = sum(
        1 for row in evidence_rows if row.evidence_type == "acceptance" and row.status == "covered"
    )
    return _redact_value(
        {
            "commands_passed": commands_passed,
            "commands_failed": commands_failed,
            "changed_files": changed_files,
            "acceptance_covered": acceptance_covered,
        }
    )


def write_review_packet_v2(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    project_id: str,
    task_id: str,
    verdict: str,
    suggested_action: str,
    evidence_summary: Mapping[str, Any] | None = None,
    risk_level: str = "unknown",
    commit: bool = True,
) -> ReviewPacketV2:
    """Write JSON and Markdown review packets under the repo root."""
    task = get_task(conn, task_id)
    if str(task["project_id"]) != project_id:
        raise ValueError(f"task {task_id!r} does not belong to project {project_id!r}")

    evidence_rows = list_task_evidence(
        conn, project_id=project_id, task_id=task_id
    )
    summary = dict(evidence_summary or _evidence_summary(evidence_rows))
    summary = _redact_value(summary)
    risk = get_latest_risk_assessment(
        conn, project_id=project_id, task_id=task_id
    )
    resolved_risk = risk.risk_level if risk is not None else risk_level

    packet_dir = _safe_repo_path(repo_root, _PACKET_DIR)
    packet_dir.mkdir(parents=True, exist_ok=True)
    json_path = packet_dir / f"{task_id}.json"
    markdown_path = packet_dir / f"{task_id}.md"

    payload = _redact_value(
        {
            "project_id": project_id,
            "task_id": task_id,
            "title": task["title"],
            "verdict": verdict,
            "suggested_action": suggested_action,
            "risk_level": resolved_risk,
            "evidence_summary": summary,
            "evidence": [
                {
                    "id": row.id,
                    "type": row.evidence_type,
                    "status": row.status,
                    "summary": row.summary,
                }
                for row in evidence_rows
            ],
        }
    )
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        f"# Review Packet: {task['title']}",
        "",
        f"- **Task ID:** {task_id}",
        f"- **Verdict:** {verdict}",
        f"- **Risk:** {resolved_risk}",
        f"- **Suggested action:** {suggested_action}",
        "",
        "## Evidence Summary",
        "",
        json.dumps(summary, indent=2),
        "",
    ]
    markdown_path.write_text("\n".join(md_lines), encoding="utf-8")

    conn.execute(
        """
        insert into review_packets_v2(
            project_id, task_id, packet_json_path, packet_markdown_path,
            verdict, created_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            task_id,
            str(json_path),
            str(markdown_path),
            verdict,
            _iso_now(),
        ),
    )
    if commit:
        conn.commit()

    return ReviewPacketV2(
        project_id=project_id,
        task_id=task_id,
        verdict=verdict,
        json_path=json_path,
        markdown_path=markdown_path,
        suggested_action=suggested_action,
        risk_level=resolved_risk,
    )


def get_review_packet_v2(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> ReviewPacketV2 | None:
    """Return the latest persisted v2 packet metadata for a task."""
    row = conn.execute(
        """
        select * from review_packets_v2
        where project_id = ? and task_id = ?
        order by created_at desc, id desc
        limit 1
        """,
        (project_id, task_id),
    ).fetchone()
    if row is None:
        return None
    return ReviewPacketV2(
        project_id=str(row["project_id"]),
        task_id=str(row["task_id"]),
        verdict=str(row["verdict"]),
        json_path=Path(str(row["packet_json_path"])),
        markdown_path=Path(str(row["packet_markdown_path"])),
        suggested_action="",
        risk_level="unknown",
    )