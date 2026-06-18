import json
from pathlib import Path

from .models import Finding

FINDINGS_RELATIVE_DIR = Path("state") / "findings"


def findings_path(root: Path, filename: str) -> Path:
    return root / FINDINGS_RELATIVE_DIR / filename


def _finding_to_dict(finding: Finding) -> dict[str, object]:
    return {
        "id": finding.id,
        "repo": finding.repo,
        "source": finding.source,
        "title": finding.title,
        "body": finding.body,
        "severity": finding.severity,
        "evidence": dict(finding.evidence),
        "discovered_at": finding.discovered_at,
    }


def _finding_from_dict(data: dict[str, object]) -> Finding:
    evidence = data.get("evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("finding evidence must be a JSON object")
    return Finding(
        id=str(data["id"]),
        repo=str(data["repo"]),
        source=str(data["source"]),
        title=str(data["title"]),
        body=str(data["body"]),
        severity=str(data["severity"]),
        evidence={str(key): str(value) for key, value in evidence.items()},
        discovered_at=str(data["discovered_at"]),
    )


def write_findings(root: Path, filename: str, findings: list[Finding]) -> Path:
    path = findings_path(root, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for finding in findings:
            line = json.dumps(_finding_to_dict(finding), sort_keys=True, separators=(",", ":"))
            handle.write(f"{line}\n")
    return path


def load_findings(root: Path, filename: str) -> list[Finding]:
    path = findings_path(root, filename)
    if not path.is_file():
        return []
    findings: list[Finding] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                raise ValueError("finding JSONL line must be a JSON object")
            findings.append(_finding_from_dict(payload))
    return findings