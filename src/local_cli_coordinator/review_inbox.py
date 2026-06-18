"""Human review inbox.

When a task reaches ``awaiting_human`` state, the engine writes a Markdown
review packet to ``tasks/review/`` so the operator has everything needed to
make a decision without digging through logs.
"""

from __future__ import annotations

from pathlib import Path


def review_packet_path(root: Path, task_id: str) -> Path:
    """Return the path where a review packet for *task_id* should be written."""
    return root / "tasks" / "review" / f"{task_id}.md"


def write_review_packet(
    root: Path,
    task: dict,
    *,
    changed_files: list[str] | None = None,
    verifier_result: str = "",
    spec_review_result: str = "",
    quality_review_result: str = "",
    suggested_action: str = "review and approve or reject",
) -> Path:
    """Write a Markdown review packet for a task that needs human review.

    Returns the path to the written packet.
    """
    path = review_packet_path(root, task["id"])
    path.parent.mkdir(parents=True, exist_ok=True)

    files_section = "\n".join(f"- {f}" for f in (changed_files or [])) or "(none)"

    try:
        branch = task["branch"]
    except (KeyError, IndexError):
        branch = "(not set)"

    sections = [
        f"# Review: {task['title']}",
        "",
        f"- **Task ID:** {task['id']}",
        f"- **Repo:** {task['repo']}",
        f"- **Branch:** {branch}",
        f"- **State:** {task['state']}",
        "",
        "## Changed Files",
        "",
        files_section,
        "",
        "## Verification",
        "",
        verifier_result or "(not available)",
        "",
        "## Spec Review",
        "",
        spec_review_result or "(not available)",
        "",
        "## Quality Review",
        "",
        quality_review_result or "(not available)",
        "",
        "## Suggested Action",
        "",
        suggested_action,
        "",
    ]

    path.write_text("\n".join(sections), encoding="utf-8")
    return path
