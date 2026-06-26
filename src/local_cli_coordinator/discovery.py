"""Discovery result persistence.

Findings are stored as JSONL files under ``state/findings/`` so they survive
restarts and can be inspected by operators.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import CoordinatorConfig
from .gitops import git
from .models import Finding
from .process import run_command
from .reporting import NULL_REPORTER, ExecutionContext, Reporter

FINDINGS_DIR = Path("state") / "findings"
DISCOVERY_DIR = Path("state") / "discovery"
CURSORS_DIR = DISCOVERY_DIR / "cursors"
FAILURES_FILENAME = "failures.jsonl"


@dataclass(frozen=True)
class CommandDiscoveryResult:
    findings: list[Finding]
    failures: list[str]


@dataclass(frozen=True)
class DiscoveryRunResult:
    discovered: int
    failures: int
    skipped: int


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _failures_path(root: Path) -> Path:
    return root / DISCOVERY_DIR / FAILURES_FILENAME


def log_discovery_failure(root: Path, source_id: str, message: str) -> None:
    path = _failures_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "source": source_id,
        "message": message,
        "logged_at": _utc_now(),
    }
    with path.open("a", encoding="utf-8") as handle:
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        handle.write(f"{line}\n")


def load_discovery_failures(root: Path) -> list[dict[str, str]]:
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
                raise ValueError("discovery failure JSONL line must be a JSON object")
            failures.append({str(key): str(value) for key, value in payload.items()})
    return failures


def _parse_command_findings(stdout: str) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    failures: list[str] = []
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            failures.append(f"invalid JSON on line {line_number}")
            continue
        if not isinstance(payload, dict):
            failures.append(f"invalid JSON on line {line_number}: expected object")
            continue
        try:
            findings.append(Finding.from_dict(payload))
        except (KeyError, TypeError, ValueError) as exc:
            failures.append(f"invalid finding on line {line_number}: {exc}")
    return findings, failures


def discover_from_command(
    *,
    root: Path,
    source_id: str,
    command: str,
    repo_id: str,
    enabled_repos: dict[str, bool],
    persist: bool = False,
    reporter: Reporter = NULL_REPORTER,
) -> CommandDiscoveryResult:
    if not enabled_repos.get(repo_id, False):
        return CommandDiscoveryResult(findings=[], failures=[])

    shell = os.environ.get("SHELL", "/bin/sh")
    argv = [shell, "-lc", command]
    context = ExecutionContext(stage="discovery", actor=source_id)
    try:
        result = run_command(
            argv,
            cwd=root,
            reporter=reporter,
            context=context,
        )
    except OSError as exc:
        message = f"discovery command failed: {exc}"
        log_discovery_failure(root, source_id, message)
        return CommandDiscoveryResult(findings=[], failures=[message])

    if result.returncode != 0:
        message = f"discovery command failed with exit code {result.returncode}"
        stderr = result.stderr.strip()
        if stderr:
            message = f"{message}: {stderr}"
        log_discovery_failure(root, source_id, message)
        return CommandDiscoveryResult(findings=[], failures=[message])

    findings, failures = _parse_command_findings(result.stdout)
    for message in failures:
        log_discovery_failure(root, source_id, message)
    if persist:
        for finding in findings:
            save_finding(root, finding)
    return CommandDiscoveryResult(findings=findings, failures=failures)


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


def run_configured_discovery(
    config: CoordinatorConfig,
    root: Path,
    *,
    reporter: Reporter = NULL_REPORTER,
) -> DiscoveryRunResult:
    """Execute all enabled discovery sources from configuration."""
    if not config.discovery_sources:
        return DiscoveryRunResult(discovered=0, failures=0, skipped=0)

    discovered = 0
    skipped = 0
    failures = 0

    for source in config.discovery_sources.values():
        if source.type == "git_recent_commits":
            for repo_id, repo_config in config.repos.items():
                if not source.repos.get(repo_id, False):
                    skipped += 1
                    continue
                try:
                    findings = discover_git_recent_commits(
                        root=root,
                        source_id=source.id,
                        repo_id=repo_id,
                        repo_path=repo_config.path,
                        enabled_repos=source.repos,
                        persist=True,
                    )
                    discovered += len(findings)
                except Exception as exc:
                    log_discovery_failure(root, source.id, str(exc))
                    failures += 1
        elif source.type in ("command", "ci_command", "issue_command"):
            for repo_id in config.repos:
                if not source.repos.get(repo_id, False):
                    skipped += 1
                    continue
                if source.command is None:
                    skipped += 1
                    continue
                result = discover_from_command(
                    root=root,
                    source_id=source.id,
                    command=source.command,
                    repo_id=repo_id,
                    enabled_repos=source.repos,
                    persist=True,
                    reporter=reporter,
                )
                discovered += len(result.findings)
                failures += len(result.failures)
        elif source.type == "inbox":
            skipped += 1
        else:
            skipped += 1

    return DiscoveryRunResult(discovered=discovered, failures=failures, skipped=skipped)


def delete_finding(root: Path, finding_id: str) -> bool:
    """Delete a finding by id.  Returns True if it existed."""
    path = findings_dir(root) / f"{finding_id}.jsonl"
    if path.exists():
        path.unlink()
        return True
    return False


def write_findings(root: Path, filename: str, findings: list[Finding]) -> Path:
    """Persist multiple findings to a single JSONL file."""
    path = findings_dir(root) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for finding in findings:
            line = json.dumps(finding.to_dict(), sort_keys=True, separators=(",", ":"))
            handle.write(f"{line}\n")
    return path


def load_findings(root: Path, filename: str) -> list[Finding]:
    """Load findings from a named JSONL file.  Returns [] when missing."""
    path = findings_dir(root) / filename
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
            findings.append(Finding.from_dict(payload))
    return findings
