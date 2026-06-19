import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re


def _parse_changed_files_from_diff(diff_content: str) -> list[str]:
    """Extract changed files from a unified diff patch."""
    files = set()
    for line in diff_content.splitlines():
        if line.startswith("+++ b/"):
            files.add(line[6:])
    return list(files)


def generate_digest(conn: sqlite3.Connection, date_str: str, root: Path) -> str:
    """Generate a markdown comprehension digest for the given date (YYYY-MM-DD)."""
    # Find all tasks updated on this date that are in terminal or blocked states
    rows = conn.execute(
        """
        select id, title, state, repo, goal
        from tasks
        where substr(updated_at, 1, 10) = ?
          and state in ('done', 'failed', 'rejected', 'awaiting_human')
        order by state, id
        """,
        (date_str,)
    ).fetchall()

    tasks_by_state: dict[str, list[sqlite3.Row]] = {
        "done": [],
        "failed": [],
        "rejected": [],
        "awaiting_human": []
    }
    task_ids = []
    for row in rows:
        tasks_by_state[row["state"]].append(row)
        task_ids.append(row["id"])

    # Gather top changed files across all these tasks by looking at their latest diff artifact
    file_changes = Counter()
    for task_id in task_ids:
        artifact = conn.execute(
            """
            select path from artifacts
            where task_id = ? and kind = 'diff'
            order by created_at desc limit 1
            """,
            (task_id,)
        ).fetchone()

        if artifact:
            diff_path = Path(artifact["path"])
            if diff_path.is_absolute():
                actual_path = diff_path
            else:
                actual_path = root / diff_path

            if actual_path.exists():
                diff_content = actual_path.read_text(errors="replace")
                files = _parse_changed_files_from_diff(diff_content)
                for f in files:
                    file_changes[f] += 1

    lines = [f"# Loop Comprehension Digest: {date_str}", ""]

    def _render_section(state_name: str, display_name: str) -> None:
        tasks = tasks_by_state[state_name]
        lines.append(f"## {display_name}")
        if not tasks:
            lines.append("No tasks.")
        else:
            for t in tasks:
                lines.append(f"- **{t['id']}** ({t['repo']}): {t['title']}")
                if t['goal']:
                    # Adding a little padding for goal to make it readable
                    lines.append(f"  - **Goal:** {t['goal']}")
        lines.append("")

    _render_section("done", "Completed Tasks")
    _render_section("awaiting_human", "Awaiting Human Review")
    _render_section("rejected", "Rejected Tasks")
    _render_section("failed", "Failed Tasks")

    lines.append("## Top Changed Files")
    if not file_changes:
        lines.append("No files changed.")
    else:
        for file_path, count in file_changes.most_common(10):
            lines.append(f"- `{file_path}` (touched by {count} task{'s' if count > 1 else ''})")
    lines.append("")

    return "\n".join(lines)


def write_daily_digest(conn: sqlite3.Connection, root: Path, date_str: str | None = None) -> Path:
    """Write the daily digest to state/digests/YYYY-MM-DD.md and return its path."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    digest_dir = root / "state" / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)

    out_path = digest_dir / f"{date_str}.md"
    content = generate_digest(conn, date_str, root)
    out_path.write_text(content)

    return out_path
