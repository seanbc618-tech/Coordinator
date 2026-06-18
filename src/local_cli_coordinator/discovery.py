"""Discovery result persistence.

Findings are stored as JSONL files under ``state/findings/`` so they survive
restarts and can be inspected by operators.
"""

from __future__ import annotations

import json
from pathlib import Path

from .gitops import git
from .models import Finding

FINDINGS_DIR = Path("state") / "findings"
CURSORS_DIR = Path("state") / "discovery" / "cursors"


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


def _cursor_path(root: Path, source_id: str, repo_id: str) -> Path:
    return root / CURSORS_DIR / f"{source_id}__{repo_id}.txt"


def load_cursor(root: Path, source_id: str, repo_id: str) -> str | None:
    path = _cursor_path(root, source_id, repo_id)
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def save_cursor(root: Path, source_id: str, repo_id: str, commit_hash: str) -> Path:
    path = _cursor_path(root, source_id, repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{commit_hash}\n", encoding="utf-8")
    return path


def _root_commit(repo_path: Path) -> str:
    result = git(["rev-list", "--max-parents=0", "HEAD"], cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"read root commit failed: {result.stderr.strip()}")
    commits = [line for line in result.stdout.splitlines() if line.strip()]
    if not commits:
        raise RuntimeError("repository has no commits")
    return commits[0]


def _recent_commits_since(repo_path: Path, since_commit: str) -> list[tuple[str, str, str]]:
    result = git(
        [
            "log",
            f"{since_commit}..HEAD",
            "--reverse",
            "--format=%H%x1f%s%x1f%aI",
        ],
        cwd=repo_path,
    )
    if result.returncode != 0:
        raise RuntimeError(f"read recent commits failed: {result.stderr.strip()}")

    commits: list[tuple[str, str, str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        commit_hash, subject, discovered_at = line.split("\x1f", 2)
        commits.append((commit_hash, subject, discovered_at))
    return commits


def _finding_id(source_id: str, repo_id: str, commit_hash: str) -> str:
    return f"finding-{source_id}-{repo_id}-{commit_hash[:12]}"


def _commit_evidence(commit_hash: str, subject: str) -> str:
    return f"commit={commit_hash};subject={subject}"


def discover_git_recent_commits(
    *,
    root: Path,
    source_id: str,
    repo_id: str,
    repo_path: Path,
    enabled_repos: dict[str, bool],
    persist: bool = False,
) -> list[Finding]:
    if not enabled_repos.get(repo_id, False):
        return []

    since_commit = load_cursor(root, source_id, repo_id) or _root_commit(repo_path)
    raw_commits = _recent_commits_since(repo_path, since_commit)
    if not raw_commits:
        return []

    findings = [
        Finding(
            id=_finding_id(source_id, repo_id, commit_hash),
            repo=repo_id,
            source=source_id,
            title=subject,
            body=subject,
            severity="info",
            evidence=_commit_evidence(commit_hash, subject),
            discovered_at=discovered_at,
        )
        for commit_hash, subject, discovered_at in raw_commits
    ]

    save_cursor(root, source_id, repo_id, raw_commits[-1][0])
    if persist:
        for finding in findings:
            save_finding(root, finding)
    return findings


def delete_finding(root: Path, finding_id: str) -> bool:
    """Delete a finding by id.  Returns True if it existed."""
    path = findings_dir(root) / f"{finding_id}.jsonl"
    if path.exists():
        path.unlink()
        return True
    return False
