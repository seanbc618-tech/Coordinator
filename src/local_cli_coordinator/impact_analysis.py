"""Heuristic where/impact analysis with citations."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .project_brain import ensure_brain_indexed
from .project_indexer import index_repository

_IMPORT_RE = re.compile(
    r"(?:from|import)\s+([\w.]+)|(?:require|import)\s*\(?['\"]([^'\"]+)['\"]"
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _score_match(query: str, path: str, content: str) -> tuple[float, str]:
    q = query.lower()
    path_l = path.lower()
    content_l = content.lower()
    if q in path_l:
        return 0.9, "path matches query"
    tokens = [t for t in re.split(r"\W+", q) if len(t) > 2]
    hits = 0
    for token in tokens:
        if token in content_l or token in path_l:
            hits += 1
            continue
        stem = Path(path).stem.lower()
        if token.startswith("database") and stem in {"db", "database"}:
            hits += 1
            continue
        if "connect" in token and "connect" in content_l:
            hits += 1
    if hits:
        return min(0.85, 0.4 + 0.1 * hits), f"matched {hits} query token(s)"
    return 0.2, "weak heuristic match"


def analyze_where(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    repo_path: Path,
    query: str,
) -> dict[str, object]:
    ensure_brain_indexed(conn, project_id=project_id, repo_path=repo_path)
    index = index_repository(repo_path.resolve())
    matches: list[dict[str, object]] = []
    for entry in index.entries:
        if entry.kind not in {"source", "test", "entrypoint", "config", "component"}:
            continue
        full = repo_path / entry.path
        content = _read_text(full)
        confidence, reason = _score_match(query, entry.path, content)
        if confidence >= 0.35:
            matches.append({
                "path": entry.path,
                "reason": reason,
                "confidence": round(confidence, 2),
            })
    matches.sort(key=lambda m: float(m["confidence"]), reverse=True)
    return {
        "project_id": project_id,
        "query": query,
        "matches": matches[:10],
        "heuristic": True,
    }


def analyze_impact(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    repo_path: Path,
    target_path: str,
) -> dict[str, object]:
    repo_path = repo_path.resolve()
    target = (repo_path / target_path).resolve()
    if not str(target).startswith(str(repo_path)):
        raise ValueError("target outside repository")
    stem = Path(target_path).stem
    module_hint = target_path.replace("/", ".").removesuffix(".py")
    index = index_repository(repo_path)
    related: list[dict[str, object]] = []
    for entry in index.entries:
        if entry.path == target_path:
            continue
        full = repo_path / entry.path
        content = _read_text(full)
        reasons: list[str] = []
        if stem in content:
            reasons.append(f"references '{stem}'")
        if module_hint in content:
            reasons.append("imports or mentions module path")
        for match in _IMPORT_RE.findall(content):
            token = match[0] or match[1]
            if stem in token or Path(target_path).name in token:
                reasons.append(f"import hint: {token}")
        if reasons:
            related.append({
                "path": entry.path,
                "reason": "; ".join(reasons),
                "confidence": 0.75,
            })
    return {
        "project_id": project_id,
        "target": target_path,
        "related": related[:15],
        "confidence": 0.7 if related else 0.3,
        "heuristic": True,
    }