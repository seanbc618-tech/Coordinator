"""Cross-agent fallback decision logic.

When a worker is blocked by an interactive approval request, this module
decides whether to hand the task to a fallback agent, fail, or escalate
to human review.
"""

from __future__ import annotations

import enum
import subprocess
from pathlib import Path
import sqlite3

from .agent_result import AgentResultClass, ClassifiedResult
from .db import fallback_count_for_task


class FallbackDecision(enum.Enum):
    """What to do after a blocked worker attempt."""

    RUN = "run"  # Run the fallback agent
    FAIL = "fail"  # Task failed, no fallback
    HUMAN_REVIEW = "human_review"  # Escalate to operator


MAX_FALLBACK_COUNT = 1  # At most one fallback attempt per task


def _worktree_has_changes(worktree: Path) -> bool:
    """Check if a git worktree has any tracked or untracked changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return True  # Assume changes if we can't check


def decide_fallback(
    conn: sqlite3.Connection,
    task_id: str,
    classified: ClassifiedResult,
    *,
    fallback_agent_id: str | None,
    worktree: Path | None = None,
) -> FallbackDecision:
    """Decide whether to run a fallback agent after a blocked attempt.

    Permits RUN only when:
    - Classification is interactive_blocked
    - Fallback count for this task is 0
    - A different eligible worker exists (fallback_agent_id is not None)
    - Worktree has no tracked or untracked changes since base commit

    Returns HUMAN_REVIEW when blocked but fallback is not possible.
    Returns FAIL for all other classifications.
    """
    # Only interactive blocks are candidates for fallback
    if classified.classification != AgentResultClass.INTERACTIVE_BLOCKED:
        return FallbackDecision.FAIL

    # No eligible fallback agent
    if fallback_agent_id is None:
        return FallbackDecision.HUMAN_REVIEW

    # Already used fallback
    current_fallbacks = fallback_count_for_task(conn, task_id)
    if current_fallbacks >= MAX_FALLBACK_COUNT:
        return FallbackDecision.HUMAN_REVIEW

    # Worktree must be clean (no changes from the blocked agent)
    if worktree is not None and _worktree_has_changes(worktree):
        return FallbackDecision.HUMAN_REVIEW

    return FallbackDecision.RUN
