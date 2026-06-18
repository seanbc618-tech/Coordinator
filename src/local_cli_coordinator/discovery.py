"""Discovery result persistence.

Findings are stored as JSONL files under ``state/findings/`` so they survive
restarts and can be inspected by operators.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Finding

FINDINGS_DIR = Path("state") / "findings"


def findings_dir(root: Path) -> Path:
    """Return the directory where finding JSONL files live."""
    return root / FINDINGS_DIR


def _finding_path(root: Path, finding: Finding) -> Path:
    directory = findings_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{finding.id}.jsonl"


def save_finding(root: Path, finding: Finding) -> Path:
    """Persist a single finding as a JSONL file."""
    path = _finding_path(root, finding)
    path.write_text(json.dumps(finding.to_dict(), ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def load_finding(root: Path, finding_id: str) -> Finding | None:
    """Load a single finding by id.  Returns None if not found."""
    path = findings_dir(root) / f"{finding_id}.jsonl"
    if not path.exists():
        return None
    return _load_one(path)


def _load_one(path: Path) -> Finding:
    line = path.read_text(encoding="utf-8").strip()
    return Finding.from_dict(json.loads(line))


def list_findings(root: Path) -> list[Finding]:
    """List all persisted findings, sorted by discovery time."""
    directory = findings_dir(root)
    if not directory.exists():
        return []
    results: list[Finding] = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            results.append(_load_one(path))
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def delete_finding(root: Path, finding_id: str) -> bool:
    """Delete a finding by id.  Returns True if it existed."""
    path = findings_dir(root) / f"{finding_id}.jsonl"
    if path.exists():
        path.unlink()
        return True
    return False
