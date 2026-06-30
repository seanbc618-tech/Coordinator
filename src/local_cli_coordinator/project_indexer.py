"""Safe repository indexing for the project brain."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import re

_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|token)\s*[=:]\s*)(\S+)"
)


def redact_text(text: str) -> str:
    return _SECRET_RE.sub(r"\1[REDACTED]", text)

MAX_FILE_BYTES = 256 * 1024
SKIP_DIR_NAMES = frozenset({
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
})
SKIP_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_rsa.pub",
    "credentials",
    "credentials.*",
)
BINARY_EXTENSIONS = frozenset({
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".whl",
    ".pyc",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
})


@dataclass
class IndexEntry:
    path: str
    kind: str
    size: int


@dataclass
class IndexResult:
    repo_root: Path
    git_head: str
    git_dirty: bool
    file_count: int
    entries: list[IndexEntry] = field(default_factory=list)

    def model_dump_json(self) -> str:
        payload = {
            "git_head": self.git_head,
            "git_dirty": self.git_dirty,
            "file_count": self.file_count,
            "entries": [
                {"path": e.path, "kind": e.kind, "size": e.size} for e in self.entries
            ],
        }
        return redact_text(json.dumps(payload))


def _git_head(repo_root: Path) -> tuple[str, bool]:
    try:
        head = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return head, bool(dirty)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "", False


def _load_gitignore_patterns(repo_root: Path) -> list[str]:
    path = repo_root / ".gitignore"
    if not path.is_file():
        return []
    patterns: list[str] = []
    for line in path.read_text().splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        patterns.append(text)
    return patterns


def _ignored_by_gitignore(rel_path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/"):
            if rel_path.startswith(pattern) or fnmatch(rel_path, pattern.rstrip("/") + "/**"):
                return True
        if fnmatch(rel_path, pattern) or fnmatch(Path(rel_path).name, pattern):
            return True
    return False


def _skip_file_name(name: str) -> bool:
    for pattern in SKIP_FILE_PATTERNS:
        if fnmatch(name, pattern):
            return True
    return False


def _classify_kind(rel_path: str) -> str:
    name = Path(rel_path).name.lower()
    if name.startswith("test_") or rel_path.startswith("tests/"):
        return "test"
    if name in {"pyproject.toml", "package.json", "cargo.toml", "go.mod"}:
        return "config"
    if rel_path.startswith("migrations/") or "/migrations/" in rel_path:
        return "migration"
    if name in {"__main__.py", "main.py", "cli.py", "app.py"}:
        return "entrypoint"
    if rel_path.startswith("docs/"):
        return "doc"
    return "source"


def index_repository(repo_root: Path) -> IndexResult:
    if not repo_root.is_dir():
        raise ValueError(f"not a directory: {repo_root}")
    repo_root = repo_root.resolve()
    git_head, git_dirty = _git_head(repo_root)
    gitignore = _load_gitignore_patterns(repo_root)
    entries: list[IndexEntry] = []

    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise ValueError(f"path outside repo root: {path}") from exc
        parts = rel.split("/")
        if any(part in SKIP_DIR_NAMES for part in parts):
            continue
        if _ignored_by_gitignore(rel, gitignore):
            continue
        if _skip_file_name(path.name):
            continue
        if path.suffix.lower() in BINARY_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            continue
        entries.append(
            IndexEntry(path=rel, kind=_classify_kind(rel), size=size)
        )

    return IndexResult(
        repo_root=repo_root,
        git_head=git_head,
        git_dirty=git_dirty,
        file_count=len(entries),
        entries=entries,
    )


def generate_brain_cards_from_index(index: IndexResult) -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    cards.append({
        "card_type": "overview",
        "title": "Repository overview",
        "summary": redact_text(
            f"{index.file_count} indexed files at commit {index.git_head[:8]}"
        ),
        "citations": [{"path": "README.md"}] if any(
            e.path == "README.md" for e in index.entries
        ) else [],
        "confidence": 0.9,
    })
    by_kind: dict[str, list[IndexEntry]] = {}
    for entry in index.entries:
        by_kind.setdefault(entry.kind, []).append(entry)

    for kind, group in by_kind.items():
        card_type = kind if kind in {
            "test", "config", "migration", "entrypoint"
        } else "component"
        sample = group[:5]
        paths = ", ".join(e.path for e in sample)
        cards.append({
            "card_type": card_type,
            "title": f"{kind} files",
            "summary": redact_text(f"{len(group)} {kind} file(s); examples: {paths}"),
            "citations": [{"path": e.path} for e in sample],
            "confidence": 0.7,
        })

    for entry in index.entries:
        if entry.path.endswith("pyproject.toml") or entry.path == "package.json":
            cards.append({
                "card_type": "command",
                "title": "Package metadata",
                "summary": redact_text(f"Build/package config at {entry.path}"),
                "citations": [{"path": entry.path}],
                "confidence": 0.8,
            })
            break

    return cards