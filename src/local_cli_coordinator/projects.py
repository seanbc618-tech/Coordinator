"""Project registry for multi-project Coordinator.

Manages registration and lookup of Git repositories as Coordinator projects.
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class ProjectDraft:
    """Inspected project metadata before registration."""

    canonical_path: Path
    repo_id: str
    default_branch: str = "main"
    branch_prefix: str = "coord/"
    verify_commands: tuple[str, ...] = ()


def inspect_project(path: Path) -> ProjectDraft:
    """Inspect a directory and return a ProjectDraft.

    Raises ValueError if the path is not inside a Git repository.
    """
    path = Path(path).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise ValueError(f"not a git repository: {path}")
        repo_root = Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"git inspection failed: {exc}") from exc

    # Get repo ID from remote or first commit
    repo_id = _resolve_repo_id(repo_root)

    # Get default branch
    default_branch = _resolve_default_branch(repo_root)

    return ProjectDraft(
        canonical_path=repo_root,
        repo_id=repo_id,
        default_branch=default_branch,
    )


def _resolve_repo_id(repo_root: Path) -> str:
    """Derive a stable repo ID from remote URL or first commit hash."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            url = result.stdout.strip()
            # Use last two path components as ID
            parts = url.rstrip("/").split("/")
            if len(parts) >= 2:
                return "/".join(parts[-2:]).removesuffix(".git")
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Fallback: first commit hash
    try:
        result = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()[:12]
    except (OSError, subprocess.TimeoutExpired):
        pass

    return "unknown"


def _resolve_default_branch(repo_root: Path) -> str:
    """Resolve the default branch name."""
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().removeprefix("refs/remotes/origin/")
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Fallback: check if main or master exists
    for branch in ("main", "master"):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", branch],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return branch
        except (OSError, subprocess.TimeoutExpired):
            continue

    return "main"


def register_project(
    conn: sqlite3.Connection,
    draft: ProjectDraft,
    *,
    confirmed: bool = False,
) -> str:
    """Register a project in the registry.

    Raises PermissionError if confirmed=False.
    Returns the project ID (idempotent for duplicate canonical paths).
    """
    if not confirmed:
        raise PermissionError("project registration requires confirmation")

    canonical = str(draft.canonical_path)

    # Check if already registered
    existing = conn.execute(
        "select id from projects where canonical_path = ?",
        (canonical,),
    ).fetchone()
    if existing is not None:
        conn.execute(
            "update projects set updated_at = current_timestamp where id = ?",
            (existing["id"],),
        )
        conn.commit()
        return existing["id"]

    project_id = f"proj-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        insert into projects(id, canonical_path, repo_id, default_branch, branch_prefix, verify_commands)
        values (?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            canonical,
            draft.repo_id,
            draft.default_branch,
            draft.branch_prefix,
            "\n".join(draft.verify_commands),
        ),
    )
    conn.commit()
    return project_id


def find_project_by_path(
    conn: sqlite3.Connection, path: Path
) -> sqlite3.Row | None:
    """Find a project whose canonical path is an ancestor of *path*."""
    resolved = Path(path).resolve()
    # Try exact match first
    row = conn.execute(
        "select * from projects where canonical_path = ?",
        (str(resolved),),
    ).fetchone()
    if row is not None:
        return row

    # Walk up looking for a registered ancestor
    current = resolved
    while current != current.parent:
        row = conn.execute(
            "select * from projects where canonical_path = ?",
            (str(current),),
        ).fetchone()
        if row is not None:
            return row
        current = current.parent

    return None


def list_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all registered projects."""
    return conn.execute(
        "select * from projects order by created_at, id"
    ).fetchall()
